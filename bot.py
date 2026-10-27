
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
STOP_LOSS = 0.03
TAKE_PROFIT = 0.05
MAX_HOLD_HOURS = 48
MAX_OPEN_POSITIONS = 2
MAX_SPREAD_PERCENT = 0.5
COOLDOWN_HOURS = 6


def read_json(filename):
    with open(filename, "r") as file:
        return json.load(file)


def save_json(filename, data):
    temporary = filename + ".tmp"

    with open(temporary, "w") as file:
        json.dump(data, file, indent=2)

    os.replace(temporary, filename)


def get_prices(pair):
    query = urllib.parse.urlencode({"pair": pair})
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": "KrakenPaperBot/1.0"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)

    if data.get("error"):
        raise RuntimeError(str(data["error"]))

    ticker = next(iter(data["result"].values()))

    prices = {
        "bid": float(ticker["b"][0]),
        "ask": float(ticker["a"][0]),
        "last": float(ticker["c"][0]),
    }

    if (
        prices["bid"] <= 0
        or prices["ask"] <= 0
        or prices["last"] <= 0
        or prices["bid"] > prices["ask"]
    ):
        raise ValueError("Invalid market prices")

    return prices


def parse_time(value):
    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def percentage_change(readings, hours, now):
    target = now - timedelta(hours=hours)
    candidates = []

    for entry in readings[:-1]:
        try:
            recorded = parse_time(entry["time"])
            difference = abs(
                (recorded - target).total_seconds()
            )

            if difference <= 2700:
                candidates.append((difference, entry))
        except (ValueError, KeyError, TypeError):
            continue

    if not candidates:
        return None

    _, old_entry = min(
        candidates,
        key=lambda item: item[0]
    )

    old_price = float(old_entry["last"])
    new_price = float(readings[-1]["last"])

    if old_price <= 0:
        return None

    return (new_price / old_price - 1) * 100


def get_signal(readings, now):
    changes = {
        "1h": percentage_change(readings, 1, now),
        "6h": percentage_change(readings, 6, now),
        "24h": percentage_change(readings, 24, now),
    }

    if any(value is None for value in changes.values()):
        return "COLLECTING DATA", changes

    if (
        changes["1h"] > 0.2
        and changes["6h"] > 0.5
        and changes["24h"] > 0
    ):
        return "BUY WATCH", changes

    if (
        changes["1h"] < -0.2
        and changes["6h"] < -0.5
        and changes["24h"] < 0
    ):
        return "SELL WATCH", changes

    return "WAIT", changes


def has_position(account, symbol):
    return any(
        position["symbol"] == symbol
        for position in account["open_positions"]
    )


def recently_closed(account, symbol, now):
    for trade in account["closed_trades"]:
        if trade["symbol"] != symbol:
            continue

        closed_at = parse_time(trade["closed_at"])

        if now - closed_at < timedelta(hours=COOLDOWN_HOURS):
            return True

    return False


def open_trade(account, symbol, prices, now):
    if has_position(account, symbol):
        return

    if len(account["open_positions"]) >= MAX_OPEN_POSITIONS:
        return

    if account["cash_balance"] < TRADE_SIZE:
        return

    if recently_closed(account, symbol, now):
        return

    spread = (
        (prices["ask"] - prices["bid"])
        / prices["ask"] * 100
    )

    if spread > MAX_SPREAD_PERCENT:
        print(f"{symbol}: Spread too wide - skipping")
        return

    entry_fee = TRADE_SIZE * FEE_RATE
    invested = TRADE_SIZE - entry_fee
    quantity = invested / prices["ask"]

    stop_price = prices["ask"] * (1 - STOP_LOSS)
    stop_proceeds = (
        quantity * stop_price * (1 - FEE_RATE)
    )
    planned_loss = TRADE_SIZE - stop_proceeds

    if planned_loss > account["max_planned_loss_per_trade"]:
        print(f"{symbol}: Planned loss exceeds limit")
        return

    account["cash_balance"] = round(
        account["cash_balance"] - TRADE_SIZE, 8
    )

    account["open_positions"].append({
        "symbol": symbol,
        "opened_at": now.isoformat(),
        "entry_price": prices["ask"],
        "quantity": quantity,
        "cost": TRADE_SIZE,
        "entry_fee": entry_fee,
        "stop_price": stop_price,
        "target_price": prices["ask"] * (1 + TAKE_PROFIT),
    })

    print(
        f"PAPER BUY: {symbol} | "
        f"£{TRADE_SIZE:.2f} at £{prices['ask']:.4f}"
    )


def close_trade(account, position, prices, now, reason):
    proceeds_before_fee = (
        position["quantity"] * prices["bid"]
    )
    exit_fee = proceeds_before_fee * FEE_RATE
    proceeds = proceeds_before_fee - exit_fee
    profit = proceeds - position["cost"]

    account["cash_balance"] = round(
        account["cash_balance"] + proceeds, 8
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
        f"PAPER SELL: {position['symbol']} | "
        f"{reason} | P/L: £{profit:+.2f}"
    )


def check_exits(account, symbol, prices, now, signal):
    for position in list(account["open_positions"]):
        if position["symbol"] != symbol:
            continue

        held_hours = (
            now - parse_time(position["opened_at"])
        ).total_seconds() / 3600

        reason = None

        if prices["bid"] <= position["stop_price"]:
            reason = "STOP LOSS"
        elif prices["bid"] >= position["target_price"]:
            reason = "TAKE PROFIT"
        elif held_hours >= MAX_HOLD_HOURS:
            reason = "TIME EXIT"
        elif signal == "SELL WATCH":
            reason = "MOMENTUM EXIT"

        if reason:
            close_trade(
                account, position, prices, now, reason
            )


def show_account(account, latest_prices):
    open_value = 0.0

    for position in account["open_positions"]:
        symbol = position["symbol"]

        if symbol in latest_prices:
            bid = latest_prices[symbol]["bid"]
            open_value += (
                position["quantity"]
                * bid
                * (1 - FEE_RATE)
            )
        else:
            open_value += position["cost"]

    equity = account["cash_balance"] + open_value

    realised_profit = sum(
        trade["profit"]
        for trade in account["closed_trades"]
    )

    print("\n===== VIRTUAL ACCOUNT =====")
    print(f"Cash: £{account['cash_balance']:.2f}")
    print(f"Estimated equity: £{equity:.2f}")
    print(f"Realised P/L: £{realised_profit:+.2f}")
    print(f"Open trades: {len(account['open_positions'])}")
    print(f"Closed trades: {len(account['closed_trades'])}")
    print("===========================")


def main():
    now = datetime.now(timezone.utc)

    account = read_json(ACCOUNT_FILE)
    history = read_json(HISTORY_FILE)

    if account.get("mode") != "PAPER":
        raise RuntimeError(
            "Safety check: account must be PAPER"
        )

    if account["max_planned_loss_per_trade"] > 1.0:
        raise RuntimeError(
            "Safety check: risk limit too high"
        )

    print("KRAKEN AUTOMATIC PAPER TRADING BOT")
    print("----------------------------------")
    print("REAL ORDERS: DISABLED")
    print("Starting test bankroll: £30")
    print(f"Time: {now.isoformat()}")

    latest_prices = {}
    signals = {}

    for symbol, pair in PAIRS.items():
        history.setdefault(symbol, [])

        try:
            prices = get_prices(pair)
            latest_prices[symbol] = prices

            history[symbol].append({
                "time": now.isoformat(),
                **prices,
            })

            cutoff = now - timedelta(days=14)

            history[symbol] = [
                entry
                for entry in history[symbol]
                if parse_time(entry["time"]) >= cutoff
            ]

            signal, changes = get_signal(
                history[symbol], now
            )

            signals[symbol] = signal

            print(f"\n{symbol} / GBP")
            print(f"Buy: £{prices['ask']:.4f}")
            print(f"Sell: £{prices['bid']:.4f}")
            print(f"Readings: {len(history[symbol])}")

            for timeframe, change in changes.items():
                if change is None:
                    result = "Not enough history"
                else:
                    result = f"{change:+.3f}%"

                print(f"{timeframe}: {result}")

            print(f"Signal: {signal}")

        except Exception as error:
            print(f"{symbol}: Scanner error: {error}")

    for symbol, prices in latest_prices.items():
        check_exits(
            account,
            symbol,
            prices,
            now,
            signals[symbol]
        )

    for symbol, prices in latest_prices.items():
        if signals[symbol] == "BUY WATCH":
            open_trade(
                account,
                symbol,
                prices,
                now
            )

    save_json(HISTORY_FILE, history)
    save_json(ACCOUNT_FILE, account)

    show_account(account, latest_prices)
    print("\nPaper trading run completed.")


if __name__ == "__main__":
    main()
