import json
import tempfile
import unittest
from pathlib import Path

from equities import D, timestamp
from equities_forward import atomic_save, initial, scan


def snapshot(at, price, quote_at=None):
    session = dict(open="2026-11-12T14:30:00Z", close="2026-11-12T21:00:00Z")
    costs = dict(spread_bps=20, slippage_bps=5, commission_bps=10,
                 fx_bps=25, buy_charge_bps=0, sell_charge_bps=0)
    data = dict(version=1, generated_at=at, sessions={"NYSE": [session], "LSE": [
        dict(open="2026-11-12T08:00:00Z", close="2026-11-12T16:30:00Z")]},
        fx=[dict(available_at="2026-11-12T13:00:00Z", gbp_per_usd="0.8")],
        instruments=[], costs={})
    for venue, unit in (("NYSE", "USD"), ("LSE", "GBX")):
        data["instruments"].append(dict(
            venue=venue, unit=unit, symbol="TEST", status="trading",
            quote=dict(at=quote_at or at, available_at=at, price=price), actions=[],
            actions_complete_from="2026-11-12T08:00:00Z", actions_complete_through=at,
        ))
        data["costs"][venue + ":TEST"] = costs.copy()
    return data


def run(state, hour_min, price, quote_time=None):
    now = f"2026-11-12T{hour_min}:00Z"
    quote = f"2026-11-12T{quote_time}:00Z" if quote_time else None
    return scan(snapshot(now, price, quote), state, timestamp(now))


class ForwardTests(unittest.TestCase):
    def test_persists_buys_and_sells_without_duplicate_fill(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            state = initial()
            for at, price in (("14:31", 100), ("14:41", 105), ("14:51", 108),
                              ("15:01", 107), ("15:11", 106), ("15:11", 106)):
                run(state, at, price)
                atomic_save(path, state)
                state = json.loads(path.read_text())
            for account in state["accounts"].values():
                self.assertEqual([e["event"] for e in account["events"]], ["BUY", "SELL"])
                self.assertEqual(account["positions"], {})
                self.assertGreater(D(account["fees"]), 0)
            us, uk = [state["accounts"][key]["events"][0] for key in ("US", "UK")]
            self.assertEqual(us["gbp_per_quote_unit"], "0.8")
            self.assertLess(D(uk["price"]), D(2))

    def test_late_quote_cannot_fill_an_existing_signal(self):
        state = initial()
        run(state, "14:31", 100)
        run(state, "14:41", 105)
        run(state, "14:51", 106, "14:40")
        run(state, "15:01", 107, "14:41")
        self.assertEqual(state["accounts"]["US"]["events"], [])
        run(state, "15:11", 108, "14:50")
        self.assertEqual(len(state["accounts"]["US"]["events"]), 1)

    def test_closed_and_missing_fx_fail_safe(self):
        state = initial()
        for at, price in (("16:01", 100), ("16:21", 105)):
            run(state, at, price)
        now = "2026-11-12T16:31:00Z"
        data = snapshot(now, 110)
        data["fx"] = []
        result = scan(data, state, timestamp(now))
        self.assertEqual(result["instruments"]["LSE:TEST"], "Outside regular session")
        self.assertIn("Missing or stale", result["instruments"]["NYSE:TEST"])
        self.assertEqual(state["accounts"]["US"]["events"], [])
        self.assertEqual(state["accounts"]["UK"]["events"], [])

    def test_missing_corporate_action_coverage_skips_fill(self):
        state = initial()
        run(state, "14:31", 100)
        run(state, "14:41", 105)
        now = "2026-11-12T14:51:00Z"
        data = snapshot(now, 108)
        data["instruments"][0]["actions_complete_through"] = "2026-11-12T14:40:00Z"
        result = scan(data, state, timestamp(now))
        self.assertIn("coverage incomplete", result["instruments"]["NYSE:TEST"])
        self.assertEqual(state["accounts"]["US"]["events"], [])
        self.assertEqual(len(state["accounts"]["UK"]["events"]), 1)
