"""Run one unattended US/UK equities paper scan and save the GBP accounts."""
import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from equities import Account, Bar, Costs, D, MARKETS, Position, calendar, fx_for, positive, timestamp

MAX_AGE = timedelta(minutes=25)
MAX_RESPONSE = 2_000_000


def initial():
    return dict(version=1, accounts={
        region: dict(cash="50", realized="0", fees="0", dividends="0",
                     positions={}, events=[])
        for region in ("US", "UK")
    }, instruments={})


def load_account(saved):
    account = Account(D(50))
    for field in ("cash", "realized", "fees", "dividends"):
        setattr(account, field, D(saved[field]))
    account.positions = {
        key: Position(D(p["shares"]), D(p["basis"]), D(p["mark"]), timestamp(p["mark_at"]))
        for key, p in saved["positions"].items()
    }
    return account


def save_account(account, history):
    return dict(cash=str(account.cash), realized=str(account.realized),
                fees=str(account.fees), dividends=str(account.dividends),
                positions={key: dict(shares=str(p.shares), basis=str(p.basis),
                                     mark=str(p.mark), mark_at=p.mark_at.isoformat())
                           for key, p in account.positions.items()},
                events=history + account.events)


def instrument_scan(row, session, account, key, tracking, fx, costs, now):
    # The feed certifies that regular-session prints and corporate actions are complete.
    if row["status"] != "trading":
        return "Instrument unavailable"
    quote = row["quote"]
    at = timestamp(quote["at"])
    available = timestamp(quote["available_at"])
    if not (session.opened <= at < session.closed and
            now - MAX_AGE <= at <= available <= now):
        return "Quote outside session, stale, or unavailable"
    if tracking.get("quote_at") and at <= timestamp(tracking["quote_at"]):
        return "Already processed this quote"
    verified_from = timestamp(row["actions_complete_from"])
    verified_to = timestamp(row["actions_complete_through"])
    since = timestamp(tracking.get("quote_at", session.opened.isoformat()))
    if not verified_from <= since <= at <= verified_to <= now:
        return "Corporate-action coverage incomplete"

    unit = row["unit"]
    if tracking.get("unit", unit) != unit:
        return "Quote unit changed"
    model = Costs.load(costs)
    divisor = 100 if unit == "GBX" else 1
    rate, rate_at = fx_for(fx, at, unit == "USD")
    price = positive(quote["price"]) / divisor
    position = account.positions.get(key)
    actions = []
    if position:
        seen = set()
        for action in row["actions"]:
            effective = timestamp(action["effective_at"])
            if not since < effective <= at:
                continue
            kind = action["type"]
            if (kind not in {"split", "dividend"} or (effective, kind) in seen or
                    timestamp(action["available_at"]) > available):
                raise ValueError("Invalid or unavailable corporate action")
            amount = positive(action["amount"])
            if kind == "dividend":
                action_rate, _ = fx_for(fx, effective, unit == "USD")
                amount = amount / divisor * action_rate
            actions.append((effective, kind, amount))
            seen.add((effective, kind))

    # Validate and price every action before applying any cash or position changes.
    for effective, kind, amount in sorted(actions, key=lambda action: (action[0], action[1] != "split")):
        if kind == "split":
            position.shares *= amount
            position.mark /= amount
        else:
            amount *= position.shares
            account.cash += amount
            account.dividends += amount
        account.events.append(dict(event=kind.upper(), instrument=key,
                                   at=effective.isoformat(), amount=str(amount)))

    if position:
        position.mark = price * rate
        position.mark_at = at

    prior = tracking.get("signal")
    if prior and (timestamp(prior["session"]) != session.opened or actions):
        prior = None
    if prior and timestamp(prior["observed_at"]) < at:
        quote_bar = Bar(price, price, available, available)
        # A delayed print is the simulated price reference. Book at scan time.
        account.trade(key, prior["side"], now, quote_bar, model, rate, rate_at,
                      unit == "USD", D(10))
        if account.events and account.events[-1]["at"] == now.isoformat():
            event = account.events[-1]
            if event["instrument"] == key:
                event["quote_at"] = at.isoformat()
                event["signal_observed_at"] = prior["observed_at"]
        prior = None

    signal = None
    previous = tracking.get("price")
    if previous and tracking.get("session") == session.opened.isoformat() and not actions:
        if price > D(previous) and key not in account.positions:
            signal = "BUY"
        elif price < D(previous) and key in account.positions:
            signal = "SELL"
    if not prior and signal:
        prior = dict(side=signal, session=session.opened.isoformat(), observed_at=now.isoformat())
    tracking.update(quote_at=at.isoformat(), price=str(price), unit=unit,
                    session=session.opened.isoformat(), signal=prior)
    return "Processed"


def scan(snapshot, state, now):
    if state["version"] != 1 or snapshot["version"] != 1:
        raise ValueError("Unsupported account or feed version")
    if not now - MAX_AGE <= timestamp(snapshot["generated_at"]) <= now:
        raise ValueError("Snapshot is stale or future-dated")
    accounts = {region: load_account(state["accounts"][region]) for region in ("US", "UK")}
    sessions = {venue: calendar(venue, rows) for venue, rows in snapshot["sessions"].items()}
    rates = sorted((timestamp(row["available_at"]), positive(row["gbp_per_usd"]))
                   for row in snapshot["fx"])
    rows = snapshot["instruments"]
    keys = [f"{row['venue']}:{row['symbol']}" for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate instrument")
    status = {}
    for key, row in sorted(zip(keys, rows)):
        venue = row["venue"]
        region, zone = MARKETS[venue]
        day = now.astimezone(ZoneInfo(zone)).date().isoformat()
        session = sessions.get(venue, {}).get(day)
        if not session or not session.opened <= now < session.closed:
            status[key] = "Outside regular session"
            continue
        if row["unit"] not in ({"GBX", "GBP"} if region == "UK" else {"USD"}):
            status[key] = "Unexpected quote unit"
            continue
        tracking = state["instruments"].setdefault(key, {})
        try:
            status[key] = instrument_scan(row, session, accounts[region], key, tracking,
                                          rates, snapshot["costs"][key], now)
        except (KeyError, ValueError, ArithmeticError) as error:
            status[key] = f"Skipped: {error}"
    report = {}
    for region, account in accounts.items():
        state["accounts"][region] = save_account(account, state["accounts"][region]["events"])
        account.events = state["accounts"][region]["events"]
        report[region] = account.report()
        report[region].pop("events")
    missing = {key for account in accounts.values() for key in account.positions} - set(keys)
    status.update({key: "Held instrument missing from feed" for key in missing})
    return dict(as_of=now.isoformat(), accounts=report, instruments=status)


def fetch(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use an HTTPS feed URL without embedded credentials")
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=20) as response:
        if urlsplit(response.url).scheme != "https":
            raise ValueError("Feed redirected outside HTTPS")
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError("Feed response too large")
    return json.loads(raw, parse_float=D)


def atomic_save(path, state):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        try:
            json.dump(state, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
            os.replace(file.name, path)
        finally:
            Path(file.name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("equities_account.json"))
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args()
    if args.init:
        with args.state.open("x") as file:
            json.dump(initial(), file, indent=2)
            file.write("\n")
        return
    state = json.loads(args.state.read_text())
    url = os.environ.get("EQUITIES_FEED_URL")
    if not url:
        parser.error("EQUITIES_FEED_URL must point to an authorized US/UK snapshot feed")
    snapshot = fetch(url)
    report = scan(snapshot, state, datetime.now(timezone.utc))
    atomic_save(args.state, state)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
