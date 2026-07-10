"""Day 20: operational metrics (§9/§11) — jobs/min, failure rate, a token-
count cost proxy, and 429 rate.

The pure ``MetricsRegistry`` unit tests always run (stdlib only, injectable
clock for determinism). The ``/metrics`` endpoint wiring tests need the
service extras and skip cleanly without them.
"""

from __future__ import annotations

import unittest

from pbicompass.service.metrics import MetricsRegistry

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import JobStore, create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class MetricsRegistrySnapshotTest(unittest.TestCase):
    def test_empty_snapshot_has_no_divide_by_zero(self):
        reg = MetricsRegistry()
        snap = reg.snapshot()
        self.assertEqual(snap["jobs_created"], 0)
        self.assertEqual(snap["failure_rate"], 0.0)
        self.assertEqual(snap["jobs_per_minute"], 0.0)
        self.assertEqual(snap["avg_input_tokens_per_job"], 0.0)

    def test_jobs_created_done_failed_counts(self):
        reg = MetricsRegistry()
        reg.record_job_created()
        reg.record_job_created()
        reg.record_job_done(usage={})
        reg.record_job_failed()
        snap = reg.snapshot()
        self.assertEqual(snap["jobs_created"], 2)
        self.assertEqual(snap["jobs_done"], 1)
        self.assertEqual(snap["jobs_failed"], 1)
        self.assertEqual(snap["failure_rate"], 0.5)

    def test_jobs_per_minute_only_counts_the_trailing_window(self):
        clock = _FakeClock()
        reg = MetricsRegistry(now=clock)
        reg.record_job_done()
        clock.advance(30)
        reg.record_job_done()
        # both completions (t=0, t=30) are within the last 60s as of t=30
        self.assertEqual(reg.snapshot(window_seconds=60.0)["jobs_per_minute"], 2.0)

        clock.advance(90)  # both prior completions now outside a 60s window
        reg.record_job_done()
        # only the newest completion is within the last 60s
        self.assertEqual(reg.snapshot(window_seconds=60.0)["jobs_per_minute"], 1.0)

    def test_avg_tokens_per_job_is_averaged_only_over_jobs_with_usage(self):
        reg = MetricsRegistry()
        reg.record_job_done(usage={"Column Describer": {"calls": 2, "input_tokens": 100, "output_tokens": 40}})
        reg.record_job_done(usage={"Column Describer": {"calls": 2, "input_tokens": 300, "output_tokens": 80}})
        reg.record_job_done(usage=None)  # offline job -- no usage, must not skew the average
        snap = reg.snapshot()
        self.assertEqual(snap["avg_input_tokens_per_job"], 200.0)
        self.assertEqual(snap["avg_output_tokens_per_job"], 60.0)
        self.assertEqual(snap["avg_llm_calls_per_job"], 2.0)

    def test_429_counters_are_independent_and_summed(self):
        reg = MetricsRegistry()
        reg.record_quota_rejected()
        reg.record_quota_rejected()
        reg.record_rate_limited()
        snap = reg.snapshot()
        self.assertEqual(snap["quota_rejected_total"], 2)
        self.assertEqual(snap["rate_limited_total"], 1)
        self.assertEqual(snap["http_429_total"], 3)

    def test_prometheus_text_is_well_formed_and_content_free(self):
        reg = MetricsRegistry()
        reg.record_job_created()
        reg.record_job_done(usage={"Agent": {"calls": 1, "input_tokens": 10, "output_tokens": 5}})
        text = reg.to_prometheus_text()
        self.assertIn("pbicompass_jobs_created_total 1", text)
        self.assertIn("# TYPE pbicompass_jobs_done_total counter", text)
        # only integers/floats after each metric name -- no report content possible
        for line in text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            name, value = line.rsplit(" ", 1)
            float(value)  # raises if it isn't a bare number


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class MetricsEndpointTest(unittest.TestCase):
    def test_metrics_endpoint_requires_admin_token_like_other_operator_routes(self):
        client = TestClient(create_app(JobStore(), require_auth=False))
        self.assertEqual(client.get("/metrics").status_code, 503)  # no admin token configured

    def test_metrics_endpoint_reports_job_counts_json(self):
        client = TestClient(create_app(JobStore(), require_auth=False, admin_token="t"))
        res = client.get("/metrics", headers={"X-Admin-Token": "t"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("jobs_created", body)
        self.assertIn("jobs_per_minute", body)
        self.assertIn("http_429_total", body)

    def test_metrics_endpoint_wrong_token_is_401(self):
        client = TestClient(create_app(JobStore(), require_auth=False, admin_token="t"))
        self.assertEqual(client.get("/metrics", headers={"X-Admin-Token": "wrong"}).status_code, 401)

    def test_metrics_endpoint_prometheus_format(self):
        client = TestClient(create_app(JobStore(), require_auth=False, admin_token="t"))
        res = client.get("/metrics", params={"format": "prometheus"}, headers={"X-Admin-Token": "t"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("pbicompass_jobs_created_total", res.text)

    def test_job_creation_via_the_real_endpoint_increments_metrics(self):
        import io
        import zipfile
        from pathlib import Path

        fixture_dir = Path(__file__).parent / "fixtures" / "SampleSales"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in fixture_dir.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(fixture_dir.parent))

        client = TestClient(create_app(JobStore(), require_auth=False, admin_token="t"))
        res = client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", buf.getvalue(), "application/zip")},
            data={"provider": "none"},
        )
        self.assertEqual(res.status_code, 200)
        snap = client.get("/metrics", headers={"X-Admin-Token": "t"}).json()
        self.assertEqual(snap["jobs_created"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
