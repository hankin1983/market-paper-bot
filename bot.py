
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_URL = "https://gamma-api.polymarket.com/markets"


def load_account():
    with open("account.json", "r") as file:
        return json.load(file)


def get_markets():
    params = urllib.parse.urlencode({
        "active": "true",
        "closed": "false",
        "limit": 100,
        "order": "endDate",
        "ascending": "true"
    })

    url = f"{API_URL}?{params}"

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

        return (
            closing_time - datetime.now(timezone.utc)
        ).total_seconds() / 3600

    except (ValueError, TypeError):
        return None


def main():
    account = load_account()

    print("AI MARKET PAPER BOT")
    print("-------------------")
    print("Mode: PAPER TRADING ONLY")
    print(f"Virtual balance: £{account['cash_balance']:.2f}")
    print(
        f"Planned loss limit: "
        f"£{account['max_planned_loss_per_trade']:.2f}"
    )
    print("Searching for short-term markets...\n")

    markets = get_markets()
    matching_markets = []

    for market in markets:
        hours_left = hours_until_close(market)

        if hours_left is None or not 0 < hours_left <= 24:
            continue

        matching_markets.append((hours_left, market))

    matching_markets.sort(key=lambda item: item[0])

    for hours_left, market in matching_markets:
        question = market.get("question", "Unknown market")
        prices = market.get("outcomePrices", "[]")

        if isinstance(prices, str):
            prices = json.loads(prices)

        print(f"Market: {question}")
        print(f"Outcome prices: {prices}")
        print(f"Hours until close: {hours_left:.1f}")
        print("------------------------------------")

    print(f"\nTotal markets scanned: {len(markets)}")
    print(f"Markets closing within 24 hours: {len(matching_markets)}")

    if not matching_markets:
        print("No matching markets in this batch.")


if __name__ == "__main__":
    main()
