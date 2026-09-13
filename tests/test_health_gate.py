"""Health-gate (circuit breaker) tests.

The gate exists to stop one bad validation run from deleting a working playlist.
These tests pin both directions: it must fire on a mass regression, and it must
NOT fire when there is simply no baseline yet.
"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.validators.health import assess


class R:
    def __init__(self, cid, status):
        self.channel_id, self.status = cid, status


def results(n_ok, n_fail, prefix="c"):
    out = [R(f"{prefix}{i}", "ACTIVE") for i in range(n_ok)]
    out += [R(f"{prefix}{i}", "OFFLINE") for i in range(n_ok, n_ok + n_fail)]
    return out


class TestGate(unittest.TestCase):
    def test_first_run_with_no_baseline_is_always_accepted(self):
        """Every channel UNVERIFIED: a low pass rate is news, not a malfunction."""
        prev = {f"c{i}": "UNVERIFIED" for i in range(100)}
        v = assess(previous_status=prev, results=results(5, 95))
        self.assertTrue(v.ok)
        self.assertIn("no baseline", v.reason)

    def test_mass_regression_from_a_healthy_baseline_shuts_the_gate(self):
        prev = {f"c{i}": "ACTIVE" for i in range(100)}
        v = assess(previous_status=prev, results=results(30, 70))
        self.assertFalse(v.ok)
        self.assertGreater(v.regression_fraction, 0.4)
        self.assertIn("validation run", v.reason)

    def test_small_regression_is_allowed_through(self):
        prev = {f"c{i}": "ACTIVE" for i in range(100)}
        v = assess(previous_status=prev, results=results(90, 10))
        self.assertTrue(v.ok)
        self.assertEqual(v.regression_fraction, 0.10)

    def test_regression_is_measured_only_against_previously_active_channels(self):
        """Channels that were already OFFLINE must not count as a regression."""
        prev = {f"c{i}": ("ACTIVE" if i < 20 else "OFFLINE") for i in range(100)}
        v = assess(previous_status=prev, results=results(20, 80))
        self.assertTrue(v.ok, v.reason)
        self.assertEqual(v.regression_fraction, 0.0)

    def test_absolute_failure_ceiling_applies_when_a_baseline_exists(self):
        prev = {f"c{i}": "ACTIVE" for i in range(20)}
        prev.update({f"c{i}": "UNVERIFIED" for i in range(20, 100)})
        # every baseline channel still passes, but almost everything else fails
        res = [R(f"c{i}", "ACTIVE") for i in range(20)]
        res += [R(f"c{i}", "OFFLINE") for i in range(20, 100)]
        v = assess(previous_status=prev, results=res)
        self.assertFalse(v.ok)
        self.assertIn("healthy baseline", v.reason)

    def test_empty_run_is_refused(self):
        self.assertFalse(assess(previous_status={}, results=[]).ok)

    def test_thresholds_are_configurable(self):
        prev = {f"c{i}": "ACTIVE" for i in range(100)}
        strict = assess(previous_status=prev, results=results(85, 15),
                        max_regression_fraction=0.10)
        self.assertFalse(strict.ok)
        loose = assess(previous_status=prev, results=results(85, 15),
                       max_regression_fraction=0.90)
        self.assertTrue(loose.ok)


if __name__ == "__main__":
    unittest.main()
