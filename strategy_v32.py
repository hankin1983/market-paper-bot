"""Parallel scoring-v3.2 simulation using only public Kraken market data."""

import json
import math
import urllib.parse
import urllib.request
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from statistics import mean

import bot

ACCOUNT_FILE = "account_v32.json"
STRATEGY = "scoring-v3.2"
INTERVAL = 30 * 60
RSI_PERIOD = 14
VOLUME_PERIOD = 20
TREND_PERIOD = 50
STOP_LOSS = 0.025
TAKE_PROFIT = 0.05
TRAIL_TRIGGER = 0.03
TRAIL_DISTANCE = 0.02
MAX_HOLD = timedelta(hours=24)


def finite(value, minimum=0):
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise ValueError("Invalid market data number")
    return number


def candles_from_response(payload, now):
    if payload["error"]:
        raise ValueError(str(payload["error"]))
    rows, = (rows for key, rows in payload["result"].items() if key != "last")
    # Kraken always includes its still-forming candle last. Validate only the
    # fixed lookback, so each scan uses the same RSI initialization window.
    rows = rows[:-1][-TREND_PERIOD:]
    latest = int(now.timestamp()) // INTERVAL * INTERVAL - INTERVAL
    expected = list(range(latest - (TREND_PERIOD - 1) * INTERVAL, latest + 1, INTERVAL))
    if [row[0] for row in rows] != expected:
        raise ValueError("Missing, stale or unordered closed candles")
    closes = [finite(row[4]) for row in rows]
    volumes = [finite(row[6]) for row in rows]
    if min(closes) <= 0:
        raise ValueError("Invalid close price")
    return closes, volumes


def fetch_candles(pair, now):
    query = urllib.parse.urlencode({"pair": pair, "interval": INTERVAL // 60})
    request = urllib.request.Request(
        "https://api.kraken.com/0/public/OHLC?" + query,
        headers={"User-Agent": "KrakenPaperBot/3.2"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return candles_from_response(json.load(response), now)


def wilder_rsi(closes):
    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    gains = mean(max(0, change) for change in changes[:RSI_PERIOD])
    losses = mean(max(0, -change) for change in changes[:RSI_PERIOD])
    for change in changes[RSI_PERIOD:]:
        gains = (gains * (RSI_PERIOD - 1) + max(0, change)) / RSI_PERIOD
        losses = (losses * (RSI_PERIOD - 1) + max(0, -change)) / RSI_PERIOD
    if gains + losses == 0:
        return 50.0
    return 100 * gains / (gains + losses)


def above_average(candles):
    closes, _ = candles
    return closes[-1] > mean(closes)


def tight_spread(prices):
    ask = Decimal(str(prices["ask"]))
    bid = Decimal(str(prices["bid"]))
    return ask - bid < ask * Decimal("0.003")


def entry_filters(candles, prices, btc_candles, symbol):
    closes, volumes = candles
    recent_volume = mean(volumes[-VOLUME_PERIOD - 1:-1])
    return {
        "trend": above_average(candles),
        "momentum": closes[-1] > max(closes[-3], closes[-13]),
        "rsi": 45 <= wilder_rsi(closes) <= 65,
        "volume": recent_volume > 0 and volumes[-1] >= 1.3 * recent_volume,
        "bitcoin": symbol == "BTC" or (
            btc_candles is not None and above_average(btc_candles)
        ),
        "spread": tight_spread(prices),
    }


def initial_account(starting_balance, now):
    return {
        "strategy": STRATEGY,
        "mode": "PAPER",
        "currency": "GBP",
        "starting_balance": starting_balance,
        "cash_balance": starting_balance,
        "open_positions": [],
        "closed_trades": [],
        "started_at": now.isoformat(),
        "last_scan": None,
        "last_entry_slot": None,
        "comparison": {"baseline": {}, "v32": {}},
    }


def manage_exits(account, quotes, now):
    for position in list(account["open_positions"]):
        prices = quotes.get(position["symbol"])
        if prices is None:
            print(f"{position['symbol']}: Exit check deferred until a quote is available")
            continue
        bid = prices["bid"]
        entry = position["entry_price"]
        position["peak_bid"] = max(bid, position["peak_bid"])
        peak = position["peak_bid"]
        reason = None
        if bid <= position["stop_price"]:
            reason = "STOP LOSS"
        elif bid >= position["target_price"]:
            reason = "TAKE PROFIT"
        elif peak >= entry * (1 + TRAIL_TRIGGER) and bid <= peak * (1 - TRAIL_DISTANCE):
            reason = "TRAILING STOP"
        elif now - bot.parse_time(position["opened_at"]) >= MAX_HOLD:
            reason = "TIME EXIT"
        if reason:
            position["exit_spread_cost"] = position["quantity"] * (prices["ask"] - bid) / 2
            bot.close_trade(account, position, prices, now, reason)


def open_trade(account, symbol, prices, now):
    if account["cash_balance"] < bot.TRADE_SIZE:
        return
    entry = prices["ask"]
    fee = bot.TRADE_SIZE * bot.FEE_RATE
    quantity = (bot.TRADE_SIZE - fee) / entry
    account["cash_balance"] = round(account["cash_balance"] - bot.TRADE_SIZE, 8)
    account["open_positions"].append({
        "strategy": STRATEGY,
        "symbol": symbol,
        "opened_at": now.isoformat(),
        "entry_price": entry,
        "entry_fee": fee,
        "entry_spread_cost": quantity * (entry - prices["bid"]) / 2,
        "quantity": quantity,
        "cost": bot.TRADE_SIZE,
        "peak_bid": prices["bid"],
        "stop_price": entry * (1 - STOP_LOSS),
        "target_price": entry * (1 + TAKE_PROFIT),
    })
    print(f"PAPER BUY: {symbol} £{bot.TRADE_SIZE:.2f} ({STRATEGY})")


def fresh_quotes(history, now):
    quotes = {}
    for symbol, readings in history.items():
        if not readings or bot.parse_time(readings[-1]["time"]) != now:
            continue
        try:
            bid = finite(readings[-1]["bid"])
            ask = finite(readings[-1]["ask"])
            if 0 < bid <= ask:
                quotes[symbol] = {"bid": bid, "ask": ask}
        except (ArithmeticError, KeyError, TypeError, ValueError):
            print(f"{symbol}: Invalid quote, skipping")
    return quotes


def marked_equity(account, quotes):
    if any(position["symbol"] not in quotes for position in account["open_positions"]):
        return None
    return account["cash_balance"] + sum(
        position["quantity"] * quotes[position["symbol"]]["bid"] * (1 - bot.FEE_RATE)
        for position in account["open_positions"]
    )


def update_equity(record, equity):
    if not record:
        record.update(start=equity, peak=equity, max_drawdown=0.0)
    record["peak"] = max(record["peak"], equity)
    record["max_drawdown"] = max(record["max_drawdown"], 1 - equity / record["peak"])
    record["return"] = equity / record["start"] - 1


def report(account, baseline, quotes, now):
    records = account["comparison"]
    baseline_equity = marked_equity(baseline, quotes)
    new_equity = marked_equity(account, quotes)
    if baseline_equity is not None and new_equity is not None:
        update_equity(records["baseline"], baseline_equity)
        update_equity(records["v32"], new_equity)
        records["marked_at"] = now.isoformat()
    else:
        print("Comparison marks deferred: at least one open position lacks a fresh quote.")

    started = bot.parse_time(account["started_at"])
    counts = []
    for name, ledger in (("baseline", baseline), ("v32", account)):
        closed = [trade for trade in ledger["closed_trades"]
                  if bot.parse_time(trade["opened_at"]) >= started]
        opened = [position for position in ledger["open_positions"]
                  if bot.parse_time(position["opened_at"]) >= started]
        count = len(closed)
        counts.append(count)
        fees = sum(trade["entry_fee"] for trade in closed + opened)
        fees += sum(trade["exit_fee"] for trade in closed)
        # Legacy fills do not consistently record opposite-side quotes.
        spread = sum(trade.get("entry_spread_cost", 0) + trade.get("exit_spread_cost", 0)
                     for trade in closed + opened) if name == "v32" else None
        stats = records[name]
        stats.update(closed_trades=count,
                     win_rate=sum(trade["profit"] > 0 for trade in closed) / count if count else None,
                     fees=fees, spread_cost=spread)
        print(f"{name}: {json.dumps(stats, sort_keys=True)}")
    records["sample"] = "100+" if min(counts) >= 100 else "50+" if min(counts) >= 50 else "collecting"
    print(f"Comparison sample: {records['sample']}")


def run(account, baseline, quotes, now, get_candles=fetch_candles):
    if account["last_scan"] and now <= bot.parse_time(account["last_scan"]):
        return
    # Establish the paired equity baseline before any v3.2 fills. Wait for a
    # complete baseline mark if the original bot has an unquoted open position.
    if not account["comparison"]["baseline"]:
        equity = marked_equity(baseline, quotes)
        if equity is None:
            print("Waiting for quotes to establish the initial comparison.")
            return
        update_equity(account["comparison"]["baseline"], equity)
        update_equity(account["comparison"]["v32"], account["starting_balance"])

    manage_exits(account, quotes, now)
    slot = int(now.timestamp()) // INTERVAL
    if account["last_entry_slot"] != slot and account["cash_balance"] >= bot.TRADE_SIZE:
        candidates = [
            symbol for symbol in bot.PAIRS if symbol in quotes
            and tight_spread(quotes[symbol])
            and all(trade["symbol"] != symbol for trade in account["open_positions"])
            and not bot.recently_closed(account, symbol, now)
        ]

        def load(symbol):
            try:
                return get_candles(bot.PAIRS[symbol], now)
            except (ArithmeticError, KeyError, IndexError, TypeError, ValueError, OSError) as error:
                print(f"{symbol}: Entry candle data unavailable: {error}")
                return None

        btc = load("BTC") if candidates else None
        for symbol in candidates:
            if account["cash_balance"] < bot.TRADE_SIZE:
                break
            if btc is None or (symbol != "BTC" and not above_average(btc)):
                break
            candles = btc if symbol == "BTC" else load(symbol)
            if candles is None:
                continue
            filters = entry_filters(candles, quotes[symbol], btc, symbol)
            failed = [name for name, passed in filters.items() if not passed]
            if failed:
                print(f"{symbol}: Entry filters failed: {', '.join(failed)}")
            else:
                open_trade(account, symbol, quotes[symbol], now)
        account["last_entry_slot"] = slot

    account["last_scan"] = now.isoformat()
    report(account, baseline, quotes, now)


def main():
    baseline = bot.read_json(bot.ACCOUNT_FILE)
    history = bot.read_json(bot.HISTORY_FILE)
    latest = max((bot.parse_time(rows[-1]["time"]) for rows in history.values() if rows), default=None)
    current = datetime.now(timezone.utc)
    if latest is None or not timedelta(0) <= current - latest <= timedelta(minutes=5):
        print("No recent baseline scan. Skipping v3.2.")
        return
    quotes = fresh_quotes(history, latest)
    account = bot.read_json(ACCOUNT_FILE)
    if account["strategy"] != STRATEGY or account["mode"] != "PAPER" or account["currency"] != "GBP":
        raise ValueError("Expected the separate GBP paper account")
    finite(account["cash_balance"])
    if account["started_at"] is None:
        account = initial_account(account["starting_balance"], latest)
    run(account, baseline, quotes, latest)
    if account["last_scan"] == latest.isoformat():
        bot.save_json(ACCOUNT_FILE, account)


if __name__ == "__main__":
    main()
