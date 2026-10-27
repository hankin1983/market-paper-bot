
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HISTORY_FILE = "price_history.json"
API_URL = "https://api.kraken.com/0/public/Ticker"

PAIRS = {
    "BTC": "XBTGBP",
    "ETH": "ETHGBP",
    "SOL": "SOLGBP",
    "XRP": "XRPGBP",
}

TIMEFRAMES = {
    "1h": 1,
    "6h": 6,
    "24h": 24,
}


def get_prices(pair):
    query = urllib.parse.urlencode({"pair": pair})
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": "KrakenTrendBot/1.0"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)

    if data.get("error"):
        raise RuntimeError(str(data["error"]))

    ticker = next(iter(data["result"].values()))

    return {
        "bid": float(ticker["b"][0]),
        "ask": float(ticker["a"][0]),
        "last": float(ticker["c"][0]),
    }


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {symbol: [] for symbol in PAIRS}

    with open(HISTORY_FILE, "r") as file:
        history = json.load(file)

    for symbol in PAIRS:
        history.setdefault(symbol, [])

    return history


def save_history(history):
    temporary_file = HISTORY_FILE + ".tmp"

    with open(temporary_file, "w") as file:
        json.dump(history, file, indent=2)

    os.replace(temporary_file, HISTORY_FILE)


def calculate_change(readings, hours, now):
    target = now - timedelta(hours=hours)

    # Use the most recent reading at or before the target.
    eligible = []

    for entry in readings:
        try:
            recorded = datetime.fromisoformat(
                entry["time"].replace("Z", "+00:00")
            )
            if recorded <= target:
                eligible.append((recorded, entry))
        except (ValueError, TypeError, KeyError):
            continue

    if not eligible:
        return None

    _, old_entry = max(eligible, key=lambda item: item[0])
    old_price = float(old_entry["last"])
    new_price = float(readings[-1]["last"])

    if old_price <= 0:
        return None

    return (new_price / old_price - 1) * 100


def analyse(readings, now):
    changes = {
        label: calculate_change(readings, hours, now)
        for label, hours in TIMEFRAMES.items()
    }

    # Require all three timeframes before issuing signals.
    if any(value is None for value in changes.values()):
        return changes, "COLLECTING DATA"

    one_hour = changes["1h"]
    six_hour = changes["6h"]
    day = changes["24h"]

    if one_hour > 0.2 and six_hour > 0.5 and day > 0:
        signal = "BUY WATCH"
    elif one_hour < -0.2 and six_hour < -0.5 and day < 0:
        signal = "SELL WATCH"
    else:
        signal = "WAIT"

    return changes, signal


def main():
    print("KRAKEN MOMENTUM SCANNER")
    print("-----------------------")
    print("Mode: MONITORING ONLY")
    print("Real orders: DISABLED")

    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    cutoff = now - timedelta(days=14)

    history = load_history()

    for symbol, pair in PAIRS.items():
        try:
            prices = get_prices(pair)

            history[symbol].append({
                "time": timestamp,
                "bid": prices["bid"],
                "ask": prices["ask"],
                "last": prices["last"],
            })

            history[symbol] = [
                entry
                for entry in history[symbol]
                if datetime.fromisoformat(
                    entry["time"].replace("Z", "+00:00")
                ) >= cutoff
            ]

            changes, signal = analyse(history[symbol], now)

            spread_pct = (
                (prices["ask"] - prices["bid"])
                / prices["ask"] * 100
            ) if prices["ask"] > 0 else 0

            print(f"\n{symbol} / GBP")
            print(f"Buy price: £{prices['ask']:,.4f}")
            print(f"Sell price: £{prices['bid']:,.4f}")
            print(f"Spread: {spread_pct:.3f}%")
            print(f"Saved readings: {len(history[symbol])}")

            for label, change in changes.items():
                if change is None:
                    print(f"{label} change: Not enough history")
                else:
                    print(f"{label} change: {change:+.3f}%")

            print(f"Signal: {signal}")
            print("-----------------------")

        except Exception as error:
            print(f"{symbol}: ERROR - {error}")

    save_history(history)
    print("\nPrice history updated successfully.")


if __name__ == "__main__":
    main()
