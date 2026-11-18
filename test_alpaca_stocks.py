import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import alpaca_stocks as adapter
from equities_forward import initial, scan


NOW = datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc)


class AdapterTests(unittest.TestCase):
    def test_splits_dividends_and_unsupported_actions(self):
        grouped = {
            "forward_splits": [dict(symbol="AAPL", ex_date="2026-11-18", new_rate=4, old_rate=1)],
            "cash_dividends": [dict(symbol="AAPL", ex_date="2026-11-18", rate="0.25")],
        }
        actions, blocked = adapter.actions_for("AAPL", grouped, NOW)
        self.assertFalse(blocked)
        self.assertEqual([a["amount"] for a in actions], ["4", "0.25"])
        grouped["spin_offs"] = [dict(source_symbol="AAPL")]
        self.assertTrue(adapter.actions_for("AAPL", grouped, NOW)[1])

    def test_calendar_closed_never_creates_trades(self):
        state = initial()
        with patch.object(adapter, "get", return_value=[]) as get:
            data = adapter.snapshot(state, NOW)
            get.assert_called_once()
        scan(data, state, NOW)
        self.assertEqual(state["accounts"]["US"]["cash"], "50")
        self.assertEqual(state["accounts"]["US"]["events"], [])

    def test_live_snapshot_has_point_in_time_prices(self):
        def get(base, path, params=None):
            if path == "/v2/calendar":
                return [dict(date="2026-11-18", open="09:30", close="16:00")]
            if path == "/v2/stocks/trades/latest":
                return dict(trades={"AAPL": dict(t=NOW.isoformat(), p=100)})
            if base == "https://api.frankfurter.dev":
                return dict(date="2026-11-17", rates={"GBP": 0.8})
            if path == "/v1/corporate-actions":
                return dict(corporate_actions={}, next_page_token=None)
            if path == "/v2/assets/AAPL":
                return dict(exchange="NASDAQ", **{"class": "us_equity"}, tradable=True)
            self.fail(path)
        state = initial()
        with patch.object(adapter, "get", side_effect=get), patch.object(adapter, "WATCHLIST", ("AAPL",)):
            data = adapter.snapshot(state, NOW)
        self.assertEqual(data["instruments"][0]["quote"]["price"], "100")
        self.assertEqual(data["fx"][0]["available_at"], "2026-11-18T00:00:00+00:00")
        scan(data, state, datetime.now(timezone.utc))
        self.assertEqual(state["accounts"]["US"]["events"], [])


if __name__ == "__main__":
    unittest.main()
