
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

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
    with open(filename, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json(filename, data):
    temporary = filename + ".tmp"

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)

    os.replace(temporary, filename)


def parse_time(value):
    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def get_prices(pair):
    query = urllib.parse.urlencode({"pair": pair})
    url = f"{API_URL}?{query}"

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "KrakenPaperBot/1.0"},
    )

    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        data = json.load(response)

    if data.get("error"):
        raise RuntimeError(str(data["error"]))

    ticker = next(iter(data["result"].values()))

    return {
        "bid": float(ticker["b"][0]),
        "ask": float(ticker["a"][0]),
        "last": float(ticker["c"][0]),
    }


def percentage_change(readings, hours, now):
    if not readings:
        return None

    target = now - timedelta(hours=hours)
    candidates = []

    for reading in readings[:-1]:
        try:
            recorded = parse_time(reading["time"])
            difference = abs(
                (recorded - target).total_seconds()
            )

            if difference <= 45 * 60:
                candidates.append(
                    (difference, reading)
                )
        except (KeyError, ValueError, TypeError):
            continue

    if not candidates:
        return None

    previous = min(
        candidates, key=lambda item: item[0]
    )[1]

    old_price = float(previous["last"])
    current_price = float(readings[-1]["last"])

    if old_price <= 0:
        return None

    return (
        (current_price - old_price) / old_price
    ) * 100


def get_signal(readings, now):
    one_hour = percentage_change(
        readings, 1, now
    )
    six_hours = percentage_change(
        readings, 6, now
    )
    day = percentage_change(
        readings, 24, now
    )

    changes = {
        "1h": one_hour,
        "6h": six_hours,
        "24h": day,
    }

    if any(
        value is None for value in changes.values()
    ):
        return "COLLECTING DATA", changes

    if (
        one_hour > 0.2
        and six_hours > 0.5
        and day > 0
    ):
        return "BUY WATCH", changes

    if (
        one_hour < -0.2
        and six_hours < -0.5
        and day < 0
    ):
        return "SELL WATCH", changes

    return "WAIT", changes


def recently_closed(account, symbol, now):
    for trade in account["closed_trades"]:
        if trade["symbol"] != symbol:
            continue

        elapsed = now - parse_time(
            trade["closed_at"]
        )

        if timedelta(0) <= elapsed < timedelta(
            hours=COOLDOWN_HOURS
        ):
            return True

    return False


def open_trade(account, symbol, prices, now):
    if len(account["open_positions"]) >= MAX_OPEN_POSITIONS:
        return

    if any(
        position["symbol"] == symbol
        for position in account["open_positions"]
    ):
        return

    if float(account["cash_balance"]) < TRADE_SIZE:
        return

    if recently_closed(account, symbol, now):
        return

    spread = (
        (prices["ask"] - prices["bid"])
        / prices["ask"] * 100
    )

    if spread > MAX_SPREAD_PERCENT:
        print(f"{symbol}: Spread too wide")
        return

    entry_fee = TRADE_SIZE * FEE_RATE
    quantity = (
        TRADE_SIZE - entry_fee
    ) / prices["ask"]

    stop_price = (
        prices["ask"] * (1 - STOP_LOSS)
    )
    target_price = (
        prices["ask"] * (1 + TAKE_PROFIT)
    )

    estimated_stop_proceeds = (
        quantity
        * stop_price
        * (1 - FEE_RATE)
    )

    planned_loss = (
        TRADE_SIZE - estimated_stop_proceeds
    )

    risk_limit = float(
        account["max_planned_loss_per_trade"]
    )

    if planned_loss > risk_limit:
        print(f"{symbol}: Risk limit exceeded")
        return

    position = {
        "symbol": symbol,
        "opened_at": now.isoformat(),
        "entry_price": prices["ask"],
        "quantity": quantity,
        "cost": TRADE_SIZE,
        "entry_fee": entry_fee,
        "stop_price": stop_price,
        "target_price": target_price,
    }

    account["cash_balance"] = round(
        float(account["cash_balance"])
        - TRADE_SIZE,
        8,
    )

    account["open_positions"].append(
        position
    )

    print(
        f"PAPER BUY: {symbol} "
        f"£{TRADE_SIZE:.2f} "
        f"at £{prices['ask']:.4f}"
    )


def close_trade(
    account, position, prices, now, reason
):
    gross = (
        float(position["quantity"])
        * prices["bid"]
    )

    exit_fee = gross * FEE_RATE
    proceeds = gross - exit_fee
    profit = (
        proceeds - float(position["cost"])
    )

    account["cash_balance"] = round(
        float(account["cash_balance"])
        + proceeds,
        8,
    )

    closed_trade = {
        **position,
        "closed_at": now.isoformat(),
        "exit_price": prices["bid"],
        "exit_fee": exit_fee,
        "proceeds": proceeds,
        "profit": profit,
        "reason": reason,
    }

    account["closed_trades"].append(
        closed_trade
    )

    account["open_positions"].remove(
        position
    )

    print(
        f"PAPER SELL: {position['symbol']} "
        f"{reason} "
        f"P/L £{profit:+.2f}"
    )


def check_exits(
    account, symbol, prices, now, signal
):
    for position in list(
        account["open_positions"]
    ):
        if position["symbol"] != symbol:
            continue

        held_hours = (
            now - parse_time(
                position["opened_at"]
            )
        ).total_seconds() / 3600

        reason = None

        if prices["bid"] <= float(
            position["stop_price"]
        ):
            reason = "STOP LOSS"

        elif prices["bid"] >= float(
            position["target_price"]
        ):
            reason = "TAKE PROFIT"

        elif held_hours >= MAX_HOLD_HOURS:
            reason = "TIME EXIT"

        elif signal == "SELL WATCH":
            reason = "MOMENTUM EXIT"

        if reason:
            close_trade(
                account,
                position,
                prices,
                now,
                reason,
            )


def show_account(account, latest_prices):
    cash = float(account["cash_balance"])
    open_value = 0.0

    for position in account["open_positions"]:
        symbol = position["symbol"]
        prices = latest_prices.get(symbol)

        if prices:
            open_value += (
                float(position["quantity"])
                * prices["bid"]
                * (1 - FEE_RATE)
            )
        else:
            open_value += float(
                position["cost"]
            )

    equity = cash + open_value

    realised = sum(
        float(trade["profit"])
        for trade in account["closed_trades"]
    )

    print("\n===== VIRTUAL ACCOUNT =====")
    print(f"Cash: £{cash:.2f}")
    print(f"Estimated equity: £{equity:.2f}")
    print(f"Realised P/L: £{realised:+.2f}")
    print(
        f"Open trades: "
        f"{len(account['open_positions'])}"
    )
    print(
        f"Closed trades: "
        f"{len(account['closed_trades'])}"
    )
    print("===========================")


def main():
    now = datetime.now(timezone.utc)

    account = read_json(ACCOUNT_FILE)
    history = read_json(HISTORY_FILE)

    if account.get("mode") != "PAPER":
        raise RuntimeError(
            "Bot must remain in PAPER mode"
        )

    if float(
        account["max_planned_loss_per_trade"]
    ) > 1.0:
        raise RuntimeError(
            "Risk limit cannot exceed £1"
        )

    latest_prices = {}
    signals = {}

    print("KRAKEN PAPER TRADING BOT")
    print("REAL TRADING DISABLED")
    print(f"Time: {now.isoformat()}")

    for symbol, pair in PAIRS.items():
        try:
            prices = get_prices(pair)
            latest_prices[symbol] = prices

            readings = history.setdefault(
                symbol, []
            )

            readings.append({
                "time": now.isoformat(),
                **prices,
            })

            history[symbol] = readings[-1500:]

            signal, changes = get_signal(
                history[symbol], now
            )

            signals[symbol] = signal

            print(f"\n{symbol} / GBP")
            print(
                f"Buy: £{prices['ask']:.4f}"
            )
            print(
                f"Sell: £{prices['bid']:.4f}"
            )
            print(
                f"Readings: "
                f"{len(history[symbol])}"
            )

            for period, change in changes.items():
                if change is None:
                    print(
                        f"{period}: COLLECTING DATA"
                    )
                else:
                    print(
                        f"{period}: {change:+.3f}%"
                    )

            print(f"Signal: {signal}")

        except Exception as error:
            print(
                f"{symbol}: ERROR - {error}"
            )

    for symbol, prices in latest_prices.items():
        check_exits(
            account,
            symbol,
            prices,
            now,
            signals[symbol],
        )

    for symbol, prices in latest_prices.items():
        if signals[symbol] == "BUY WATCH":
            open_trade(
                account,
                symbol,
                prices,
                now,
            )

    save_json(
        HISTORY_FILE, history
    )
    save_json(
        ACCOUNT_FILE, account
    )

    show_account(
        account, latest_prices
    )


if __name__ == "__main__":
    main()
