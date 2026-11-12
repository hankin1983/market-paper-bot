"""Offline US and UK equity paper trading from point-in-time market data."""
import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

D = Decimal
BPS = D(10000)
MARKETS = {"NYSE": ("US", "America/New_York"),
           "NASDAQ": ("US", "America/New_York"),
           "LSE": ("UK", "Europe/London")}


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError(f"Timestamp needs a UTC offset: {value}")
    return result.astimezone(timezone.utc)


def number(value, minimum=D(0), maximum=None):
    result = D(str(value))
    if not result.is_finite() or result < minimum or (
        maximum is not None and result > maximum
    ):
        raise ValueError(f"Invalid number: {value}")
    return result


def positive(value):
    result = number(value)
    if not result:
        raise ValueError("Prices, rates and split ratios must be positive")
    return result


@dataclass(frozen=True)
class Session:
    opened: datetime
    closed: datetime


@dataclass(frozen=True)
class Bar:
    opened: Decimal
    closed: Decimal
    open_known: datetime
    close_known: datetime


@dataclass(frozen=True)
class Costs:
    spread: Decimal
    slippage: Decimal
    commission: Decimal
    fx: Decimal
    buy_charge: Decimal
    sell_charge: Decimal

    @classmethod
    def load(cls, values):
        return cls(*(number(values[key], maximum=D(1000)) / BPS for key in (
            "spread_bps", "slippage_bps", "commission_bps", "fx_bps",
            "buy_charge_bps", "sell_charge_bps",
        )))

    def quote(self, side, price, rate, usd):
        impact = self.spread / 2 + self.slippage
        direction = 1 if side == "BUY" else -1
        execution_price = price * (1 + direction * impact)
        gross = execution_price * rate
        charge = self.buy_charge if side == "BUY" else self.sell_charge
        fee = gross * (self.commission + charge + self.fx * usd)
        return execution_price, gross + direction * fee, fee


def calendar(venue, rows):
    zone = ZoneInfo(MARKETS[venue][1])
    sessions = {}
    for row in rows:
        opened, closed = timestamp(row["open"]), timestamp(row["close"])
        day = opened.astimezone(zone).date().isoformat()
        if (day in sessions or closed <= opened or
                closed.astimezone(zone).date().isoformat() != day or
                opened.astimezone(zone).weekday() >= 5):
            raise ValueError(f"Invalid or duplicate session: {venue} {day}")
        sessions[day] = Session(opened, closed)
    return dict(sorted(sessions.items()))


def bars_for(instrument, sessions):
    bars = {}
    unit = instrument["unit"]
    valid_units = {"GBP", "GBX"} if instrument["venue"] == "LSE" else {"USD"}
    if unit not in valid_units:
        raise ValueError(f"Unexpected quote unit: {unit}")
    divisor = 100 if unit == "GBX" else 1
    for row in instrument["bars"]:
        day = row["date"]
        session = sessions[day]
        bar = Bar(positive(row["open"]) / divisor, positive(row["close"]) / divisor,
                  timestamp(row["open_available_at"]), timestamp(row["close_available_at"]))
        if (day in bars or bar.open_known < session.opened or
                bar.close_known < max(session.closed, bar.open_known)):
            raise ValueError(f"Invalid or duplicate bar: {instrument['symbol']} {day}")
        bars[day] = bar
    return bars


@dataclass
class Position:
    shares: Decimal
    basis: Decimal
    mark: Decimal
    mark_at: datetime


class Account:
    def __init__(self, capital):
        self.capital = self.cash = capital
        self.positions = {}
        self.realized = self.fees = self.dividends = D(0)
        self.events = []

    def trade(self, key, side, at, bar, costs, rate, rate_at, usd, stake):
        price, per_share, fee = costs.quote(side, bar.opened, rate, usd)
        if side == "BUY":
            budget = min(self.cash, stake)
            if not budget:
                return
            shares = budget / per_share
            self.cash -= budget
            self.positions[key] = Position(shares, budget, bar.opened * rate, at)
            net = -budget
        else:
            position = self.positions.pop(key)
            shares = position.shares
            net = shares * per_share
            self.cash += net
            self.realized += net - position.basis
        self.fees += shares * fee
        self.events.append(dict(
            event=side, instrument=key, at=at.isoformat(), observed_at=bar.open_known.isoformat(),
            shares=str(shares), price=str(price), gbp_per_quote_unit=str(rate),
            fx_available_at=rate_at.isoformat() if rate_at else None,
            fees_gbp=str(shares * fee), cash_change_gbp=str(net),
        ))

    def report(self):
        unrealized = sum((p.mark * p.shares - p.basis for p in self.positions.values()), D(0))
        pnl = self.realized + self.dividends + unrealized
        return dict(
            starting_gbp=str(self.capital), cash_gbp=str(self.cash),
            realized_gbp=str(self.realized), dividends_gbp=str(self.dividends),
            unrealized_gbp=str(unrealized), pnl_gbp=str(pnl),
            return_pct=str(pnl / self.capital * 100), fees_gbp=str(self.fees),
            trades=sum(e["event"] in {"BUY", "SELL"} for e in self.events),
            positions={key: dict(shares=str(p.shares), mark_gbp=str(p.mark),
                                 mark_at=p.mark_at.isoformat())
                       for key, p in sorted(self.positions.items())},
            events=self.events,
        )


def fx_for(rows, at, usd):
    if not usd:
        return D(1), None
    for available, rate in reversed(rows):
        if available <= at:
            if at - available <= timedelta(days=4):
                return rate, available
            break
    raise ValueError("Missing or stale USD/GBP rate")


def corporate_actions(instrument, sessions):
    actions = {}
    for row in instrument["actions"]:
        kind, day = row["type"], row["date"]
        if kind not in {"split", "dividend"} or (day, kind) in actions:
            raise ValueError(f"Invalid or duplicate corporate action: {row}")
        known = timestamp(row["available_at"])
        if known > sessions[day].opened:
            raise ValueError("Corporate action information arrived after its effective open")
        amount = positive(row["amount"])
        if kind == "dividend" and instrument["unit"] == "GBX":
            amount /= 100
        actions[day, kind] = amount
    return actions


def skip(account, key, at, reason):
    account.events.append(dict(event="SKIP", instrument=key, at=at.isoformat(), reason=reason))


def replay(data, cutoff):
    """Rebuild a ledger deterministically from immutable inputs and a fixed cutoff."""
    capital = {key: positive(data["capital_gbp"][key]) for key in ("US", "UK")}
    if "ETF" in data["capital_gbp"]:
        capital["ETF"] = positive(data["capital_gbp"]["ETF"])
    accounts = {key: Account(value) for key, value in capital.items()}
    stake = positive(data["stake_gbp"])
    costs = {key: Costs.load(value) for key, value in data["costs"].items()}
    calendars = {venue: calendar(venue, rows) for venue, rows in data["sessions"].items()}
    fx = sorted((timestamp(row["available_at"]), positive(row["gbp_per_usd"])) for row in data["fx"])
    if len({at for at, _ in fx}) != len(fx):
        raise ValueError("Duplicate FX publication timestamp")

    instruments = {}
    events = []
    for item in data["instruments"]:
        venue, kind = item["venue"], item["kind"]
        if kind not in {"stock", "etf"}:
            raise ValueError(f"Unknown instrument kind: {kind}")
        bucket = "ETF" if kind == "etf" else MARKETS[venue][0]
        if bucket not in accounts:
            continue
        key = f"{venue}:{item['symbol']}"
        if key in instruments:
            raise ValueError(f"Duplicate instrument: {key}")
        sessions = calendars[venue]
        bars = bars_for(item, sessions)
        actions = corporate_actions(item, sessions)
        instruments[key] = (accounts[bucket], item["unit"] == "USD", costs[key], bars, actions)
        for day, session in sessions.items():
            bar = bars.get(day)
            events.append((session.opened, 0, key, day))
            if bar:
                events.append((bar.close_known, 1, key, day))

    decisions = {}
    previous = {}
    last_session = {}
    last_close = {}
    for at, phase, key, day in sorted(events):
        if at > cutoff:
            break
        account, usd, model, bars, actions = instruments[key]
        bar = bars.get(day)
        position = account.positions.get(key)
        if phase == 0:
            if last_session.get(key) != last_close.get(key):
                previous.pop(key, None)
                skip(account, key, at, "Previous session close unavailable")
            last_session[key] = day
            split = actions.get((day, "split"))
            if split:
                previous.pop(key, None)
                if position:
                    position.shares *= split
                    position.mark /= split
                    account.events.append(dict(event="SPLIT", instrument=key,
                                               at=at.isoformat(), ratio=str(split)))
            dividend = actions.get((day, "dividend"))
            if position and dividend:
                rate, _ = fx_for(fx, at, usd)
                # Credit on ex-date in this cash-account model.
                amount = position.shares * dividend * rate
                account.cash += amount
                account.dividends += amount
                account.events.append(dict(event="DIVIDEND", instrument=key,
                                           at=at.isoformat(), gbp=str(amount)))
            decision = decisions.pop(key, None)
            if not decision or decision[0] >= at or split or dividend:
                continue
            # Imported opening prints may arrive shortly after the exchange open.
            # This is a historical model with a pre-existing decision, not a live fill.
            if not bar or bar.open_known > min(cutoff, at + timedelta(minutes=5)):
                skip(account, key, at, "Missing or late opening price")
                continue
            try:
                rate, rate_at = fx_for(fx, at, usd)
            except ValueError as error:
                skip(account, key, at, str(error))
                continue
            account.trade(key, decision[1], at, bar, model, rate, rate_at, usd, stake)
        else:
            if last_session.get(key) != day:
                skip(account, key, at, "Close published after a later session opened")
                continue
            last_close[key] = day
            if position:
                try:
                    rate, _ = fx_for(fx, at, usd)
                except ValueError as error:
                    skip(account, key, at, str(error))
                else:
                    position.mark = bar.closed * rate
                    position.mark_at = at
            prior = previous.get(key)
            previous[key] = bar.closed
            if prior is not None and not actions.get((day, "dividend")):
                if bar.closed > prior and position is None:
                    decisions[key] = (at, "BUY")
                elif bar.closed < prior and position is not None:
                    decisions[key] = (at, "SELL")

    return dict(as_of=cutoff.isoformat(),
                accounts={key: account.report() for key, account in accounts.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--as-of", required=True, type=timestamp)
    args = parser.parse_args()
    print(json.dumps(replay(json.loads(args.input.read_text(), parse_float=D), args.as_of), indent=2))


if __name__ == "__main__":
    main()
