
import json
import urllib.request
from datetime import datetime, timezone

API_URL = "https://gamma-api.polymarket.com/markets"


def load_account():
    with open("account.json", "r") as file:
        return json.load(file)


def get_markets():
    url = API_URL + "?active=true&closed=false&limit=100"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MarketPaperBot/1.0"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def hours_until_close(market):
    end_date = market.get("endDate")

    if not end_date:
        return None

    try:
        closing_time = datetime.fromisoformat(
            end_date.replace("Z", "+00:00")
        )

        hours_left = (
            closing_time - datetime.now(timezone.utc)
        ).total_seconds() / 3600

        return hours_left

    except (ValueError, TypeError):
        return None


def main():
    account = load_account()

    balance = account["cash_balance"]
    max_loss = account["max_planned_loss_per_trade"]

    print("AI MARKET PAPER BOT")
    print("-------------------")
    print("Mode: PAPER TRADING ONLY")
    print(f"Virtual balance: £{balance:.2f}")
    print(f"Planned loss limit: £{max_loss:.2f}")
    print("Scanning prediction markets...\n")

    try:
        markets = get_markets()
        matching_markets = 0

        for market in markets:
            hours_left = hours_until_close(market)

            # Only show markets ending within 24 hours
            if hours_left is None or not 0 < hours_left <= 24:
                continue

            question = market.get("question", "Unknown market")
            prices = market.get("outcomePrices", "[]")

            if isinstance(prices, str):
                prices = json.loads(prices)

            matching_markets += 1

            print(f"Market: {question}")
            print(f"Outcome prices: {prices}")
            print(f"Hours until close: {hours_left:.1f}")
            print("------------------------------------")

        print(f"\nTotal markets scanned: {len(markets)}")
        print(f"Markets closing within 24 hours: {matching_markets}")

        if matching_markets == 0:
            print("No short-term markets found in this batch.")
            print("The scanner is working correctly.")

    except Exception as error:
        print(f"Scanner error: {error}")
        raise


if __name__ == "__main__":
    main()
