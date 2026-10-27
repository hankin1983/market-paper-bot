
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


def get_prices(pair):
    query = urllib.parse.urlencode({"pair": pair})
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": "KrakenHistoryBot/1.0"},
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


def main():
    print("KRAKEN PRICE HISTORY BOT")
    print("------------------------")
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

            # Keep only the most recent 14 days
            history[symbol] = [
                entry
                for entry in history[symbol]
                if datetime.fromisoformat(
                    entry["time"].replace("Z", "+00:00")
                ) >= cutoff
            ]

            print(f"\n{symbol} / GBP")
            print(f"Buy price: £{prices['ask']:,.4f}")
            print(f"Sell price: £{prices['bid']:,.4f}")
            print(f"Saved readings: {len(history[symbol])}")

            if len(history[symbol]) >= 2:
                previous = history[symbol][-2]["last"]
                current = prices["last"]

                if previous > 0:
                    change = (current / previous - 1) * 100
                    print(f"Change since previous scan: {change:+.3f}%")

        except Exception as error:
            print(f"{symbol}: ERROR - {error}")

    save_history(history)
    print("\nPrice history updated successfully.")


if __name__ == "__main__":
    main()
