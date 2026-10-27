
import json
import urllib.request
from datetime import datetime, timezone

# Our virtual trading account
with open("account.json", "r") as file:
    account = json.load(file)

STARTING_BALANCE = account["cash_balance"]
MAX_PLANNED_LOSS = 2.00

# Public prediction market data
API_URL = "https://gamma-api.polymarket.com/markets"


def get_markets():
    url = API_URL + "?active=true&closed=false&limit=100"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MarketPaperBot/1.0"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def main():
    print("AI MARKET PAPER BOT")
    print("-------------------")
    print("Mode: PAPER TRADING ONLY")
    print(f"Virtual balance: £{STARTING_BALANCE:.2f}")
    print(f"Planned loss limit: £{MAX_PLANNED_LOSS:.2f}")
    print("Scanning prediction markets...\n")

    try:
        markets = get_markets()

        for market in markets:
            for market in markets:
    end_date = market.get("endDate")

    if not end_date:
        continue

    try:
        closing_time = datetime.fromisoformat(
            end_date.replace("Z", "+00:00")
        )
    except ValueError:
        continue

    hours_left = (
        closing_time - datetime.now(timezone.utc)
    ).total_seconds() / 3600

    if not 0 < hours_left <= 24:
        continue

    question = market.get("question", "Unknown market")            question = market.get("question", "Unknown market")
            prices = market.get("outcomePrices", "[]")

            if isinstance(prices, str):
                prices = json.loads(prices)

            print(f"Market: {question}")
            print(f"Outcome prices: {prices}")
            print("-------------------")

        print(f"\nScanned {len(markets)} markets.")

    except Exception as error:
        print(f"Scanner error: {error}")
        raise


if __name__ == "__main__":
    main()
