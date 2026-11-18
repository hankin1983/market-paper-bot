"""US stock price adapter for a fictional GBP ledger. HTTP GET requests only."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from equities import D, positive, timestamp
from equities_forward import atomic_save, initial, scan

PAPER = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
STATE = Path("us_stocks_account.json")
WATCHLIST = ("AAPL", "MSFT", "AMZN", "GOOGL", "NVDA", "META", "TSLA", "JPM", "V", "WMT")
ZONE = ZoneInfo("America/New_York")


def get(base, path, params=None):
    headers = {"Accept": "application/json", "User-Agent": "GBP-stock-paper-simulator/1"}
    if base in (PAPER, DATA):
        headers.update({
            "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
            "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
        })
    url = base + path + ("?" + urlencode(params) if params else "")
    try:
        with urlopen(Request(url, headers=headers), timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        # Never print response bodies or request headers containing credentials.
        raise RuntimeError(f"{base}{path} returned HTTP {error.code}") from None


def corporate_actions(symbols, start, end):
    grouped = {}
    token = None
    while True:
        params = dict(symbols=",".join(symbols), start=start, end=end,
                      limit=1000, data_quality="all")
        if token:
            params["page_token"] = token
        result = get(DATA, "/v1/corporate-actions", params)
        for kind, rows in result.get("corporate_actions", {}).items():
            grouped.setdefault(kind, []).extend(rows)
        token = result.get("next_page_token")
        if not token:
            return grouped


def actions_for(symbol, grouped, now):
    actions = []
    blocked = False
    for kind, rows in grouped.items():
        for row in rows:
            names = [value for key, value in row.items() if "symbol" in key]
            if symbol not in names:
                continue
            if kind not in {"cash_dividends", "forward_splits", "reverse_splits"}:
                blocked = True
                continue
            day = row.get("ex_date")
            if not day:
                blocked = True
                continue
            effective = datetime.fromisoformat(day).replace(
                hour=9, minute=30, tzinfo=ZONE).astimezone(timezone.utc)
            if effective > now:
                continue
            amount = positive(row["rate"]) if kind == "cash_dividends" else (
                positive(row["new_rate"]) / positive(row["old_rate"]))
            actions.append(dict(type="dividend" if kind == "cash_dividends" else "split",
                                effective_at=effective.isoformat(), available_at=now.isoformat(),
                                amount=str(amount)))
    return actions, blocked


def snapshot(state, now):
    day = now.astimezone(ZONE).date().isoformat()
    calendar = get(PAPER, "/v2/calendar", dict(start=day, end=day))
    sessions = []
    for row in calendar:
        sessions.append({
            name: datetime.fromisoformat(row["date"] + "T" + row[name]).replace(
                tzinfo=ZONE).astimezone(timezone.utc).isoformat()
            for name in ("open", "close")
        })
    symbols = sorted(set(WATCHLIST) | {
        key.split(":", 1)[1] for key in state["accounts"]["US"]["positions"]
    })
    base = dict(version=1, generated_at=now.isoformat(),
                sessions={"NYSE": sessions, "NASDAQ": sessions},
                fx=[], costs={}, instruments=[])
    if not sessions or not timestamp(sessions[0]["open"]) <= now < timestamp(sessions[0]["close"]):
        print("US regular session is closed. Connection verified using Alpaca's calendar.")
        return base

    trades = get(DATA, "/v2/stocks/trades/latest", dict(symbols=",".join(symbols), feed="iex"))["trades"]
    previous_day = (now - timedelta(days=1)).date().isoformat()
    fx = get("https://api.frankfurter.dev", "/v1/" + previous_day, dict(base="USD", symbols="GBP"))
    # Daily reference FX is conservatively treated as available next UTC day.
    available = datetime.fromisoformat(fx["date"]).replace(tzinfo=timezone.utc) + timedelta(days=1)
    base["fx"] = [dict(available_at=available.isoformat(), gbp_per_usd=str(positive(fx["rates"]["GBP"])))]
    starts = [state["instruments"].get(key, {}).get("quote_at")
              for key in state["accounts"]["US"]["positions"]]
    start = min([day] + [timestamp(at).date().isoformat() for at in starts if at])
    grouped = corporate_actions(symbols, start, day)
    received = datetime.now(timezone.utc)
    base["generated_at"] = received.isoformat()
    for symbol in symbols:
        trade = trades.get(symbol)
        if not trade:
            print(f"{symbol}: no IEX trade available")
            continue
        asset = get(PAPER, "/v2/assets/" + symbol)
        venue = asset["exchange"]
        if venue not in {"NYSE", "NASDAQ"} or asset["class"] != "us_equity":
            print(f"{symbol}: unsupported exchange or asset class")
            continue
        actions, blocked = actions_for(symbol, grouped, received)
        key = venue + ":" + symbol
        base["instruments"].append(dict(
            venue=venue, symbol=symbol, unit="USD",
            risk=dict(stop="0.01", target="0.02", max_hours=2, min_rise="0.01"),
            status="trading" if asset["tradable"] and not blocked else "unavailable",
            quote=dict(at=trade["t"], available_at=received.isoformat(), price=str(positive(trade["p"]))),
            actions=actions, actions_complete_from=start + "T00:00:00+00:00",
            actions_complete_through=received.isoformat(),
        ))
        # Explicit simulation costs, independent of any live broker fee plan.
        base["costs"][key] = dict(spread_bps=20, slippage_bps=5, commission_bps=0,
                                 fx_bps=25, buy_charge_bps=0, sell_charge_bps=0)
    print(f"Alpaca IEX connection verified: {len(base['instruments'])} stocks fetched.")
    return base


def main():
    if not os.environ.get("ALPACA_API_KEY") or not os.environ.get("ALPACA_SECRET_KEY"):
        raise RuntimeError("Both Alpaca paper secrets are required")
    state = json.loads(STATE.read_text()) if STATE.exists() else initial()
    data = snapshot(state, datetime.now(timezone.utc))
    now = datetime.now(timezone.utc)
    report = scan(data, state, now)
    state["last_scan"] = now.isoformat()
    state["source"] = "Alpaca IEX; fictional GBP account; no broker orders"
    atomic_save(STATE, state)
    print(json.dumps(report, indent=2))
    print("US stock paper ledger saved.")


if __name__ == "__main__":
    main()
