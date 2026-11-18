import unittest
from equities_forward import initial, scan
from equities import timestamp
from test_equities_forward import snapshot


class TargetsTests(unittest.TestCase):
    def step(self, state, at, price):
        data = snapshot(at, price)
        data["instruments"] = data["instruments"][:1]
        data["instruments"][0]["risk"] = dict(stop="0.01", target="0.02", max_hours=2, min_rise="0.01")
        return scan(data, state, timestamp(at))

    def opened(self):
        state = initial()
        for time, price in (("14:31",100), ("14:41",102), ("14:51",102)):
            self.step(state,"2026-11-12T"+time+":00Z",price)
        self.assertEqual(len(state["accounts"]["US"]["positions"]),1)
        return state

    def test_targets_and_timeout(self):
        for at, price, reason in (("15:01",100,"STOP LOSS"),("15:01",105,"TAKE PROFIT"),
                                  ("16:51",102,"TIME EXIT")):
            with self.subTest(reason=reason):
                s=self.opened()
                result=self.step(s,"2026-11-12T"+at+":00Z",price)
                self.assertEqual(result["instruments"]["NYSE:TEST"],reason)
                self.assertEqual(s["accounts"]["US"]["events"][-1]["reason"],reason)
                self.assertEqual(s["accounts"]["US"]["positions"],{})

    def test_small_rise_and_small_fall(self):
        s=initial()
        self.step(s,"2026-11-12T14:31:00Z",100)
        self.step(s,"2026-11-12T14:41:00Z",100.1)
        self.assertIsNone(s["instruments"]["NYSE:TEST"]["signal"])
        s=self.opened()
        self.step(s,"2026-11-12T15:01:00Z",101.9)
        self.step(s,"2026-11-12T15:11:00Z",101.9)
        self.assertEqual(len(s["accounts"]["US"]["positions"]),1)

    def test_legacy_position_gets_targets(self):
        s=self.opened()
        del s["instruments"]["NYSE:TEST"]["entry_price"]
        del s["instruments"]["NYSE:TEST"]["opened_at"]
        self.step(s,"2026-11-12T15:01:00Z",105)
        self.assertEqual(s["accounts"]["US"]["events"][-1]["reason"],"TAKE PROFIT")


if __name__=="__main__":
    unittest.main()
