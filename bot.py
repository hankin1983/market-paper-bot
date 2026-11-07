
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# KRAKEN PAPER TRADING BOT V3.1
# No real orders, API keys, borrowing or leverage.

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

# No fixed open-position limit.
# Available virtual cash determines how many trades fit.
MAX_SPREAD_PERCENT = 0.5
COOLDOWN_HOURS = 6

# More selective buying rules.
MIN_BUY_SCORE = 1.5
MAX_ONE_HOUR_RISE = 2.0
REQUIRED_CONFIRMATIONS = 2
MAX_CONFIRMATION_GAP_MINUTES = 90


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
        headers={"User-Agent": "KrakenPaperBot/3.1"},
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
        "ask": float(ticker
