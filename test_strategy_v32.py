import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import strategy_v32 as strategy


NOW = datetime(2026, 11, 13, 18, 37, tzinfo=timezone.utc)
QUOTE = {"bid": 99.95, "ask": 100.0}


def candles():
    closes = [100 + 0.08 * i + i % 2 for i in range(50)]
    return closes, [10.0] * 49 + [13.0]


def baseline():
    return {"cash_balance": 30.0, "open_positions": [], "closed_trades": []}


class StrategyTests(unittest.TestCase):
    def test_filters_and_spread_boundary(self):
        trend = candles()
        self.assertTrue(all(strategy.entry_filters(trend, QUOTE, trend, "ETH").values()))
        self.assertFalse(strategy.entry_filters(trend, {"bid": 99.7, "ask": 100}, trend, "ETH")["spread"])
        self.assertFalse(strategy.entry_filters(trend, QUOTE, None, "ETH")["bitcoin"])
        self.assertTrue(strategy.entry_filters(trend, QUOTE, None, "BTC")["bitcoin"])
        trend[1][-1] = 12.99
        self.assertFalse(strategy.entry_filters(trend, QUOTE, trend, "BTC")["volume"])
        trend[0][-3] = trend[0][-1]
        self.assertFalse(strategy.entry_filters(trend, QUOTE, trend, "BTC")["momentum"])
        trend[0][-1] = 1
        self.assertFalse(strategy.entry_filters(trend, QUOTE, trend, "BTC")["trend"])
        rising = (list(range(1, 51)), trend[1])
        self.assertFalse(strategy.entry_filters(rising, QUOTE, rising, "BTC")["rsi"])

    def test_closed_candles_only_and_bad_data(self):
        current = int(NOW.timestamp()) // strategy.INTERVAL * strategy.INTERVAL
        rows = [[current - (50 - i) * strategy.INTERVAL, 0, 0, 0, 1, 0, 2, 1]
                for i in range(51)]
        rows[-1][6] = "uncommitted"
        data = {"error": [], "result": {"XXBTZGBP": rows, "last": current}}
        self.assertEqual(strategy.candles_from_response(data, NOW), ([1] * 50, [2] * 50))
        for bad_rows in (rows[:20] + rows[21:], rows[:-1], rows[:-2] + [rows[-3], rows[-1]]):
            with self.assertRaises(ValueError):
                strategy.candles_from_response({"error": [], "result": {"pair": bad_rows}}, NOW)
        rows[-2][6] = "NaN"
        with self.assertRaises(ValueError):
            strategy.candles_from_response(data, NOW)

    def test_account_isolation_and_exit_thresholds(self):
        for bid, hours, reason in [(97.5, 1, "STOP LOSS"), (105, 1, "TAKE PROFIT"),
                                   (100, 24, "TIME EXIT"), (101, 1, "TRAILING STOP")]:
            with self.subTest(reason=reason):
                original = baseline()
                before = copy.deepcopy(original)
                account = strategy.initial_account(30, NOW)
                strategy.run(account, original, {"BTC": QUOTE}, NOW, lambda *_: candles())
                self.assertEqual(account["cash_balance"], 25)
                strategy.run(account, original, {"BTC": QUOTE}, NOW, lambda *_: self.fail("duplicate scan"))
                if reason == "TRAILING STOP":
                    strategy.manage_exits(account, {"BTC": {"bid": 104, "ask": 104.1}},
                                          NOW + timedelta(minutes=15))
                strategy.run(account, original, {"BTC": {"bid": bid, "ask": bid + 0.05}},
                             NOW + timedelta(hours=hours), lambda *_: candles())
                self.assertEqual(account["closed_trades"][0]["reason"], reason)
                self.assertEqual(account["open_positions"], [])
                self.assertEqual(original, before)
                trade = account["closed_trades"][0]
                self.assertAlmostEqual(account["cash_balance"], 25 + trade["proceeds"])
                self.assertAlmostEqual(account["comparison"]["v32"]["fees"],
                                       trade["entry_fee"] + trade["exit_fee"])

    def test_candle_failure_keeps_exits_running(self):
        account = strategy.initial_account(30, NOW)
        strategy.open_trade(account, "BTC", QUOTE, NOW)
        with patch.dict(strategy.bot.PAIRS, {"BTC": "XBTGBP", "ETH": "ETHGBP"}, clear=True):
            def unavailable(*_):
                raise TimeoutError("offline")
            strategy.run(account, baseline(), {"BTC": {"bid": 90, "ask": 90.05}, "ETH": QUOTE},
                         NOW + timedelta(hours=1), unavailable)
        self.assertEqual(account["closed_trades"][0]["reason"], "STOP LOSS")
        self.assertEqual(account["open_positions"], [])

    def test_comparison_threshold_and_drawdown(self):
        original = baseline()
        account = strategy.initial_account(30, NOW)
        trade = {"opened_at": NOW.isoformat(), "profit": 0.10, "entry_fee": 0.02, "exit_fee": 0.02}
        for ledger in (original, account):
            ledger["closed_trades"] = [trade] * 50
        strategy.report(account, original, {}, NOW)
        self.assertEqual(account["comparison"]["sample"], "50+")
        account["cash_balance"] = 27
        strategy.report(account, original, {}, NOW + timedelta(minutes=15))
        self.assertAlmostEqual(account["comparison"]["v32"]["return"], -0.1)
        self.assertAlmostEqual(account["comparison"]["v32"]["max_drawdown"], 0.1)
        self.assertEqual(account["comparison"]["v32"]["win_rate"], 1)


if __name__ == "__main__":
    unittest.main()
