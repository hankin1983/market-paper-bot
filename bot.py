
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# PAPER TRADING ONLY
API_URL = "https://api.kraken.com/0/public/Ticker"
HISTORY_FILE = "price_history.json"
ACCOUNT_FILE = "account.json"

PAIRS = {
    "BTC": "XBTGBP",
    "ETH": "ETHGBP",
    "SOL": "SOLGBP",
    "XRP": "XRPGBP",
    "ADA": "ADAGBP",
    "DOGE": "DOGEGBP",
    "LINK": "LINKGBP",
    "AVAX": "AVAXGBP",
    "DOT": "DOTGBP",
    "LTC": "LTCGBP",
    "BCH": "BCHGBP",
    "XLM": "XLMGBP",
    "UNI": "UNIGBP",
    "AAVE": "AAVEGBP",
    "ATOM": "ATOMGBP",
    "ALGO": "ALGOGBP",
    "ETC": "ETCGBP",
    "FIL": "FILGBP",
    "NEAR": "NEARGBP",
    "TRX": "TRXGBP",
}

TRADE_SIZE = 5.00
FEE_RATE = 0.004
STOP_LOSS = 0.03
TAKE_PROFIT = 0.05
MAX_HOLD_HOURS = 48
MAX_OPEN_POSITIONS = 2
MAX_SPREAD_PERCENT = 0.5
COOLDOWN_HOURS = 6
MIN_BUY_SCORE = 1.0


def read_json(filename):
    with open(filename, encoding="utf-8") as file:
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
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": "KrakenPaperBot/3.0"},
    )

    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        data = json.load(response)

    if data.get("error"):
        raise RuntimeError(str(data["error"]))

    ticker = next(iter(data["result"].values()))

    prices = {
        "bid": float(ticker["b"][0]),
        "ask": float(ticker["a"][0]),
        "last": float(ticker["c"][0]),
    }

    if not (
        0 < prices["bid"] <= prices["ask"]
        and prices["last"] > 0
    ):
        raise ValueError("Invalid prices")

    return prices


def percentage_change(readings, hours, now):
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

    old = float(previous["last"])
    current = float(readings[-1]["last"])

    if old <= 0:
        return None

    return (current / old - 1) * 100


def calculate_score(changes, spread):
    if any(value is None for value in changes.values()):
        return None

    hour = changes["1h"]
    six = changes["6h"]
    day = changes["24h"]

    # A balanced trend score, not simply biggest gain.
    score = (
        0.5 * hour
        + 0.3 * six
        + 0.2 * day
        - 2.0 * spread
    )

    # Avoid chasing sharp one-hour spikes.
    if hour > 2.0:
        score -= (hour - 2.0) * 2

    return score


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


def open_trade(account, symbol, prices, now, score):
    if len(account["open_positions"]) >= MAX_OPEN_POSITIONS:
        return False

    if any(
        p["symbol"] == symbol
        for p in account["open_positions"]
    ):
        return False

    if float(account["cash_balance"]) < TRADE_SIZE:
        return False

    if recently_closed(account, symbol, now):
        return False

    entry_fee = TRADE_SIZE * FEE_RATE
    quantity = (
        TRADE_SIZE - entry_fee
    ) / prices["ask"]

    stop_price = prices["ask"] * (1 - STOP_LOSS)
    target_price = prices["ask"] * (1 + TAKE_PROFIT)

    estimated_stop_proceeds = (
        quantity * stop_price * (1 - FEE_RATE)
    )

    planned_loss = TRADE_SIZE - estimated_stop_proceeds

    if planned_loss > float(
        account["max_planned_loss_per_trade"]
    ):
        return False

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
        "strategy": "scoring-v3",
        "entry_score": score,
    })

    print(
        f"PAPER BUY: {symbol} "
        f"£{TRADE_SIZE:.2f} "
        f"Score {score:+.3f}"
    )
    return True


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


def check_exits(account, prices_by_coin, scores, now):
    for position in list(account["open_positions"]):
        symbol = position["symbol"]
        prices = prices_by_coin.get(symbol)

        if prices is None:
            print(f"{symbol}: Cannot check exit - no price")
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
        else:
            score = scores.get(symbol)
            weak = score is not None and score < -1.0

            # Two consecutive weak readings before
            # selling on momentum alone.
            count = int(position.get("weak_scans", 0))
            position["weak_scans"] = (
                count + 1 if weak else 0
            )

            if position["weak_scans"] >= 2:
                reason = "SCORE EXIT"

        if reason:
            close_trade(
                account, position, prices, now, reason
            )


def show_account(account, latest_prices):
    cash = float(account["cash_balance"])
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
            # Cost basis is only a fallback estimate.
            open_value += float(position["cost"])
            print(
                f"WARNING: {position['symbol']} "
                "valuation uses cost basis"
            )

    realised = sum(
        float(trade["profit"])
        for trade in account["closed_trades"]
    )

    print("\n===== VIRTUAL ACCOUNT =====")
    print(f"Cash: £{cash:.2f}")
    print(f"Estimated equity: £{cash + open_value:.2f}")
    print(f"Realised P/L: £{realised:+.2f}")
    print(f"Open trades: {len(account['open_positions'])}")
    print(f"Closed trades: {len(account['closed_trades'])}")
    print("===========================")


def main():
    now = datetime.now(timezone.utc)

    account = read_json(ACCOUNT_FILE)
    history = read_json(HISTORY_FILE)

    if account.get("mode") != "PAPER":
        raise RuntimeError("PAPER mode required")

    if float(
        account.get("max_planned_loss_per_trade", 999)
    ) > 1.0:
        raise RuntimeError("Risk limit exceeds £1")

    print("KRAKEN SCORING PAPER BOT V3")
    print("REAL ORDERS DISABLED")
    print(f"Time: {now.isoformat()}")

    latest_prices = {}
    scores = {}
    candidates = []

    for symbol, pair in PAIRS.items():
        try:
            prices = get_prices(pair)
            latest_prices[symbol] = prices

            readings = history.setdefault(symbol, [])
            readings.append({
                "time": now.isoformat(),
                **prices,
            })
            history[symbol] = readings[-1500:]

            changes = {
                f"{hours}h": percentage_change(
                    history[symbol], hours, now
                )
                for hours in (1, 6, 24)
            }

            spread = (
                (prices["ask"] - prices["bid"])
                / prices["ask"] * 100
            )

            score = calculate_score(changes, spread)
            scores[symbol] = score

            if score is None:
                print(f"{symbol}: COLLECTING DATA")
                continue

            print(
                f"{symbol}: Score {score:+.3f} "
                f"| Spread {spread:.3f}%"
            )

            if (
                spread <= MAX_SPREAD_PERCENT
                and score >= MIN_BUY_SCORE
                and changes["6h"] > 0
                and changes["24h"] > -2.0
            ):
                candidates.append((score, symbol))

        except Exception as error:
            print(f"{symbol}: SKIPPED - {error}")

    # Protective exits always come before entries.
    check_exits(account, latest_prices, scores, now)

    candidates.sort(reverse=True)

    print("\n===== TOP OPPORTUNITIES =====")
    if not candidates:
        print("No coins qualified for a buy")
    else:
        for score, symbol in candidates[:5]:
            print(f"{symbol}: {score:+.3f}")

    # Rank first, then consider virtual purchases.
    for score, symbol in candidates:
        if len(account["open_positions"]) >= MAX_OPEN_POSITIONS:
            break

        open_trade(
            account,
            symbol,
            latest_prices[symbol],
            now,
            score,
        )

    save_json(HISTORY_FILE, history)
    save_json(ACCOUNT_FILE, account)

    show_account(account, latest_prices)


if __name__ == "__main__":
    main()
