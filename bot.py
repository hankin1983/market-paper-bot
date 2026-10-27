
import json
import urllib.request
from datetime import datetime, timezone

API_URL = "https://api.kraken.com/0/public/Ticker"

# Markets to watch against GBP
PAIRS = {
    "Bitcoin": "XBTGBP",
    "Ethereum": "ETHGBP",
    "Solana": "SOLGBP",
    "XRP": "XRPGBP",
}


def get_price(pair):
    url = f"{API_URL}?pair={pair}"

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MarketPaperBot/1.0"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)

    if data.get("error"):
        raise RuntimeError(data["error"])

    ticker = next(iter(data["result"].values()))

    return {
        "bid": float(ticker["b"][0]),
        "ask": float(ticker["a"][0]),
        "last": float(ticker["c"][0]),
    }


def main():
    print("KRAKEN TRADING BOT")
    print("-------------------")
    print("Mode: PRICE MONITORING ONLY")
    print("Real orders: DISABLED")
    print("Time:", datetime.now(timezone.utc).isoformat())
    print()

    for name, pair in PAIRS.items():
        try:
            price = get_price(pair)

            print(f"{name} ({pair})")
            print(f"Last price: £{price['last']:,.4f}")
            print(f"Buy (ask): £{price['ask']:,.4f}")
            print(f"Sell (bid): £{price['bid']:,.4f}")
            print("-------------------")

        except Exception as error:
            print(f"{name}: ERROR - {error}")


if __name__ == "__main__":
    main()
