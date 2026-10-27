
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
