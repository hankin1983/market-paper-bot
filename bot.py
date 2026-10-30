
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# PAPER TRADING ONLY - NO REAL ORDERS
API_URL = "https://api.kraken.com/0/public/Ticker"
HISTORY_FILE = "price_history.json"
ACCOUNT_FILE = "account.json"

PAIRS = {
    "BTC": "XBTGBP",
    "ETH": "ETHGBP",
    "SOL": "SOLGBP",
    "XRP": "XRPGBP",
}

TRADE_SIZE = 5.00
FEE_RATE = 0.004
MAX_OPEN_POSITIONS = 1
MAX_SPREAD_PERCENT = 0.3
MAX_DAILY_LOSS = 1.00
MAX_HOLD_HOURS = 48
COOLDOWN_HOURS = 6
STRATEGY = "confirmed-trend-v2"


def parse_time(value):
    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def load_json(filename):
    with open(filename, encoding="utf-8") as file:
        return json.load(file)


def save_json(filename, data):
    temporary = filename + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
    os.replace(temporary, filename)


def get_prices(pair):
    query = urllib.parse.urlencode({"pair": pair})
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": "KrakenPaperResearch/2.0"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)

    if data.get("error"):
        raise RuntimeError(str(data["error"]))

    ticker = next(iter(data["result"].values()))

    bid = float(ticker["b"][0])
    ask = float(ticker["a"][0])
    last = float(ticker["c"][0])

    if not (0 < bid <= ask and last > 0):
        raise ValueError("Invalid ticker values")

    return {"bid": bid, "ask": ask, "last": last}


def nearest_reading(readings, target):
    candidates = []

    for entry in readings:
        try:
            recorded = parse_time(entry["time"])
            difference = abs(
                (recorded - target).total_seconds()
            )

            if difference <= 35 * 60:
                candidates.append((difference, entry))
        except (ValueError, KeyError, TypeError):
            continue

    if not candidates:
        return None

    return min(candidates, key=lambda x: x[0])[1]


def change_since(readings, hours, now):
    target = now - timedelta(hours=hours)
    old = nearest_reading(readings[:-1], target)

    if old is None or float(old["last"]) <= 0:
        return None

    current = float(readings[-1]["last"])
    previous = float(old["last"])

    return (current / previous - 1) * 100


def get_signal(readings, now):
    changes = {
        "1h": change_since(readings, 1, now),
        "6h": change_since(readings, 6, now),
        "24h": change_since(readings, 24, now),
    }

    if any(v is None for v in changes.values()):
        return "COLLECTING DATA", changes

    if (
        changes["24h"] > 0.5
        and changes["6h"] > 0.35
        and 0.15 <= changes["1h"] <= 1.0
    ):
        return "UPTREND", changes

    if (
        changes["24h"] < -0.5
        and changes["6h"] < -0.35
    ):
        return "DOWNTREND", changes

    return "WAIT", changes


def recent_volatility(readings, now):
    samples = []

    for hours_ago in range(7):
        newer = nearest_reading(
            readings,
            now - timedelta(hours=hours_ago)
        )
        older = nearest_reading(
            readings,
            now - timedelta(hours=hours_ago + 1)
        )

        if newer and older and float(older["last"]) > 0:
            movement = abs(
                float(newer["last"]) /
                float(older["last"]) - 1
            )
            samples.append(movement)

    if len(samples) < 4:
        return None

    return max(samples)


def daily_loss(account, now):
    total = 0.0

    for trade in account["closed_trades"]:
        closed = parse_time(trade["closed_at"])

        if closed.date() == now.date():
            total += max(0, -float(trade["profit"]))

    return total


def recently_closed(account, symbol, now):
    for trade in account["closed_trades"]:
        if trade["symbol"] != symbol:
            continue

        elapsed = now - parse_time(trade["closed_at"])

        if timedelta(0) <= elapsed < timedelta(
            hours=COOLDOWN_HOURS
        ):
            return True

    return False


def open_trade(account, symbol, prices, readings, now):
    if account["open_positions"]:
        return

    if float(account["cash_balance"]) < TRADE_SIZE:
        return

    if recently_closed(account, symbol, now):
        return

    spread = (
        (prices["ask"] - prices["bid"]) /
        prices["ask"] * 100
    )

    if spread > MAX_SPREAD_PERCENT:
        print(f"{symbol}: Spread too wide")
        return

    volatility = recent_volatility(readings, now)

    if volatility is None:
        print(f"{symbol}: Waiting for volatility data")
        return

    stop_fraction = min(
        0.035,
        max(0.015, volatility * 1.5)
    )

    entry_fee = TRADE_SIZE * FEE_RATE
    quantity = (
        (TRADE_SIZE - entry_fee) / prices["ask"]
    )

    stop_price = prices["ask"] * (1 - stop_fraction)
    target_price = (
        prices["ask"] * (1 + stop_fraction * 1.8)
    )

    stop_proceeds = (
        quantity * stop_price * (1 - FEE_RATE)
    )
    planned_loss = TRADE_SIZE - stop_proceeds

    risk_limit = min(
        1.0,
        float(account["max_planned_loss_per_trade"])
    )

    if planned_loss > risk_limit:
        print(f"{symbol}: Risk budget exceeded")
        return

    account["cash_balance"] = round(
        float(account["cash_balance"]) - TRADE_SIZE, 8
    )

    account["open_positions"].append({
        "symbol": symbol,
        "opened_at": now.isoformat(),
        "entry_price": prices["ask"],
        "quantity": quantity,
        "cost": TRADE_SIZE,
        "entry_fee": entry_fee,
        "stop_price": stop_price,
        "target_price": target_price,
        "strategy": STRATEGY,
    })

    print(
        f"PAPER BUY: {symbol} "
        f"£{TRADE_SIZE:.2f} at £{prices['ask']:.4f}"
    )


def close_trade(account, position, prices, now, reason):
    gross = float(position["quantity"]) * prices["bid"]
    exit_fee = gross * FEE_RATE
    proceeds = gross - exit_fee
    profit = proceeds - float(position["cost"])

    account["cash_balance"] = round(
        float(account["cash_balance"]) + proceeds, 8
    )

    account["closed_trades"].append({
        **position,
        "closed_at": now.isoformat(),
        "exit_price": prices["bid"],
        "exit_fee": exit_fee,
        "proceeds": proceeds,
        "profit": profit,
        "reason": reason,
    })

    account["open_positions"].remove(position)

    print(
        f"PAPER SELL: {position['symbol']} "
        f"{reason} | P/L £{profit:+.2f}"
    )


def check_exits(account, symbol, prices, now, signal):
    for position in list(account["open_positions"]):
        if position["symbol"] != symbol:
            continue

        held_hours = (
            now - parse_time(position["opened_at"])
        ).total_seconds() / 3600

        reason = None

        if prices["bid"] <= float(position["stop_price"]):
            reason = "STOP LOSS"
        elif prices["bid"] >= float(position["target_price"]):
            reason = "TAKE PROFIT"
        elif held_hours >= MAX_HOLD_HOURS:
            reason = "TIME EXIT"
        elif signal == "DOWNTREND" and held_hours >= 2:
            reason = "TREND EXIT"

        if reason:
            close_trade(
                account, position, prices, now, reason
            )


def main():
    now = datetime.now(timezone.utc)

    account = load_json(ACCOUNT_FILE)
    history = load_json(HISTORY_FILE)

    if account.get("mode") != "PAPER":
        raise RuntimeError("PAPER mode required")

    if float(
        account.get("max_planned_loss_per_trade", 999)
    ) > 1.0:
        raise RuntimeError("Risk setting exceeds £1")

    print("KRAKEN CONFIRMED-TREND PAPER BOT V2")
    print("REAL ORDERS DISABLED")
    print(f"Time: {now.isoformat()}")

    if len(account["open_positions"]) > MAX_OPEN_POSITIONS:
        print("Existing positions exceed new limit")
        print("No new buys until positions close")

    latest_prices = {}
    signals = {}
    cutoff = now - timedelta(days=14)

    for symbol, pair in PAIRS.items():
        try:
            prices = get_prices(pair)
            latest_prices[symbol] = prices

            readings = history.setdefault(symbol, [])

            readings.append({
                "time": now.isoformat(),
                **prices,
            })

            history[symbol] = [
                entry for entry in readings
                if parse_time(entry["time"]) >= cutoff
            ]

            signal, changes = get_signal(
                history[symbol], now
            )

            signals[symbol] = signal

            print(f"\n{symbol} / GBP")
            print(f"Sell: £{prices['bid']:.4f}")
            print(f"Signal: {signal}")

            for period, change in changes.items():
                if change is None:
                    print(f"{period}: Waiting")
                else:
                    print(f"{period}: {change:+.3f}%")

        except Exception as error:
            print(f"{symbol}: ERROR - {error}")

    # Check exits before considering new purchases.
    for symbol, prices in latest_prices.items():
        check_exits(
            account, symbol, prices, now, signals[symbol]
        )

    # Daily loss limit blocks new purchases.
    if daily_loss(account, now) >= MAX_DAILY_LOSS:
        print("DAILY LOSS LIMIT: No new purchases")

    elif not account["open_positions"]:
        for symbol, prices in latest_prices.items():
            previous = account.setdefault(
                "last_signals", {}
            ).get(symbol)

            # Buy only when a new uptrend signal appears.
            if (
                signals[symbol] == "UPTREND"
                and previous != "UPTREND"
                and not account["open_positions"]
            ):
                open_trade(
                    account,
                    symbol,
                    prices,
                    history[symbol],
                    now
                )

    for symbol, signal in signals.items():
        account.setdefault(
            "last_signals", {}
        )[symbol] = signal

    save_json(HISTORY_FILE, history)
    save_json(ACCOUNT_FILE, account)

    open_value = 0.0

    for position in account["open_positions"]:
        prices = latest_prices.get(position["symbol"])

        if prices:
            open_value += (
                float(position["quantity"])
                * prices["bid"]
                * (1 - FEE_RATE)
            )
        else:
            open_value += float(position["cost"])

    realised = sum(
        float(trade["profit"])
        for trade in account["closed_trades"]
    )

    cash = float(account["cash_balance"])
    equity = cash + open_value

    print("\n===== VIRTUAL ACCOUNT =====")
    print(f"Cash: £{cash:.2f}")
    print(f"Estimated equity: £{equity:.2f}")
    print(f"Realised P/L: £{realised:+.2f}")
    print(f"Open trades: {len(account['open_positions'])}")
    print(f"Closed trades: {len(account['closed_trades'])}")
    print("===========================")


if __name__ == "__main__":
    main()
