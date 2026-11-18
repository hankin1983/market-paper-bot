import copy
import unittest

from equities import D, replay, timestamp


DAYS = ("2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30")


def sample():
    costs = dict(spread_bps=20, slippage_bps=5, commission_bps=10,
                 fx_bps=25, buy_charge_bps=0, sell_charge_bps=0)
    data = dict(capital_gbp={"US": 50, "UK": 50}, stake_gbp=20,
                costs={}, sessions={}, fx=[], instruments=[])
    for venue, symbol, unit, start in (
        ("NASDAQ", "TEST", "USD", "14:30"),
        ("LSE", "TEST", "GBX", "08:00"),
    ):
        days = DAYS if venue == "NASDAQ" else ("2026-11-24", "2026-11-25", "2026-11-26",
                                               "2026-11-27", "2026-11-30")
        data["sessions"][venue] = []
        instrument = dict(venue=venue, symbol=symbol, kind="stock", unit=unit, bars=[], actions=[])
        data["instruments"].append(instrument)
        data["costs"][f"{venue}:{symbol}"] = costs.copy()
        for day, price in zip(days, (100, 105, 102, 99, 97)):
            close = "16:30" if venue == "LSE" else ("18:00" if day == DAYS[2] else "21:00")
            opened, closed = f"{day}T{start}:00Z", f"{day}T{close}:00Z"
            data["sessions"][venue].append(dict(open=opened, close=closed))
            instrument["bars"].append(dict(date=day, open=price + 1, close=price,
                                           open_available_at=opened, close_available_at=closed))
    data["fx"] = [
        dict(available_at="2026-11-25T16:00:00Z", gbp_per_usd="0.8"),
        dict(available_at="2026-11-27T16:00:00Z", gbp_per_usd="0.7"),
    ]
    return data


def run(data, at="2026-11-30T22:00:00Z"):
    return replay(data, timestamp(at))["accounts"]


class ReplayTests(unittest.TestCase):
    def test_exchange_holiday_and_early_close(self):
        report = run(sample())
        us = report["US"]["events"]
        uk = report["UK"]["events"]
        self.assertEqual([(event["event"], event["at"]) for event in us], [
            ("BUY", "2026-11-27T14:30:00+00:00"),
            ("SELL", "2026-11-30T14:30:00+00:00"),
        ])
        self.assertEqual(uk[0]["at"], "2026-11-26T08:00:00+00:00")
        self.assertEqual(D(uk[0]["price"]), D("1.03") * D("1.0015"))
        self.assertEqual(us[0]["gbp_per_quote_unit"], "0.8")
        self.assertEqual(us[1]["gbp_per_quote_unit"], "0.7")
        self.assertGreater(D(report["US"]["fees_gbp"]), 0)
        self.assertLess(D(report["US"]["realized_gbp"]), 0)

    def test_late_signal_cannot_fill_at_elapsed_open(self):
        data = sample()
        data["instruments"][0]["bars"][1]["close_available_at"] = "2026-11-27T14:31:00Z"
        us = run(data)["US"]
        self.assertEqual(us["trades"], 0)
        self.assertTrue(any("later session" in e["reason"] for e in us["events"]))

    def test_future_open_is_unavailable_at_cutoff(self):
        data = sample()
        data["instruments"][0]["bars"][2]["open_available_at"] = "2026-11-27T14:31:00Z"
        self.assertEqual(run(data, "2026-11-27T14:30:00Z")["US"]["trades"], 0)
        self.assertEqual(run(data, "2026-11-27T14:31:00Z")["US"]["trades"], 1)

    def test_missing_fx_and_missing_open_skip(self):
        data = sample()
        data["fx"] = data["fx"][1:]
        data["instruments"][1]["bars"].pop(2)
        result = run(data)
        self.assertEqual(result["US"]["trades"], 0)
        self.assertEqual(result["UK"]["trades"], 0)
        self.assertIn("USD/GBP", result["US"]["events"][0]["reason"])
        self.assertIn("opening price", result["UK"]["events"][0]["reason"])

    def test_replay_is_repeatable_and_etfs_opt_in(self):
        data = sample()
        etf = copy.deepcopy(data["instruments"][0])
        etf.update(symbol="FUND", kind="etf")
        data["instruments"].append(etf)
        data["costs"]["NASDAQ:FUND"] = data["costs"]["NASDAQ:TEST"]
        self.assertNotIn("ETF", run(data))
        data["capital_gbp"]["ETF"] = 50
        self.assertEqual(run(data), run(data))
        self.assertEqual(run(data)["ETF"]["trades"], 2)
        self.assertEqual(run(data)["US"]["trades"], 2)

    def test_dividend_and_split_update_existing_position(self):
        data = sample()
        uk = data["instruments"][1]
        uk["actions"] = [
            dict(type="split", date="2026-11-27", amount=2, available_at="2026-11-26T18:00:00Z"),
            dict(type="dividend", date="2026-11-30", amount=5, available_at="2026-11-27T18:00:00Z"),
        ]
        report = run(data)["UK"]
        buy, split, dividend = report["events"]
        self.assertEqual(split["ratio"], "2")
        self.assertEqual(D(report["positions"]["LSE:TEST"]["shares"]), D(buy["shares"]) * 2)
        self.assertEqual(D(dividend["gbp"]), D(buy["shares"]) * 2 * D("0.05"))

    def test_rejects_weekend_and_wrong_quote_unit(self):
        data = sample()
        data["sessions"]["NASDAQ"].append(dict(open="2026-11-28T14:30:00Z",
                                               close="2026-11-28T21:00:00Z"))
        with self.assertRaisesRegex(ValueError, "Invalid or duplicate session"):
            run(data)
        data = sample()
        data["instruments"][1]["unit"] = "USD"
        with self.assertRaisesRegex(ValueError, "Unexpected quote unit"):
            run(data)


if __name__ == "__main__":
    unittest.main()
