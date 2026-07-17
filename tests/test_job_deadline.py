"""Regression tests for the job deadline and the LLM retry budget.

Both defended a specific, shipped failure: the watchdog was set *below* the
runtime of a healthy job, so every AI bundle was force-failed mid-run and the
user was told to try a smaller file; and each provider SDK's own retry loop
nested underneath ours, multiplying the real retry budget by 3x invisibly.
"""

import os
import unittest
from unittest import mock

from pbicompass.service.jobs import DEFAULT_JOB_TIMEOUT_SECONDS, JobStore, JobStatus


class JobDeadlineTest(unittest.TestCase):
    def test_default_exceeds_slowest_measured_healthy_run(self):
        """A 50-page/250-measure AI bundle measures ~626s of critical path.
        The watchdog must clear that with room for provider variance, or it
        kills working jobs. The old 600s did not clear even the 822s bundle
        that was actually observed end-to-end."""
        slowest_measured_healthy = 626
        self.assertGreater(DEFAULT_JOB_TIMEOUT_SECONDS, slowest_measured_healthy)
        self.assertGreaterEqual(
            DEFAULT_JOB_TIMEOUT_SECONDS / slowest_measured_healthy, 1.3,
            "want >=30% headroom over the slowest healthy run")

    def test_api_and_worker_agree_on_the_deadline(self):
        """The API polls against it and the Celery worker is bounded by it;
        they read the same env var and must share the same fallback."""
        from pbicompass.service.app import _job_timeout_seconds as api_timeout
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PBICOMPASS_JOB_TIMEOUT_SECONDS", None)
            self.assertEqual(api_timeout(), DEFAULT_JOB_TIMEOUT_SECONDS)

    def test_env_override_still_wins(self):
        from pbicompass.service.app import _job_timeout_seconds as api_timeout
        with mock.patch.dict(os.environ, {"PBICOMPASS_JOB_TIMEOUT_SECONDS": "1234"}):
            self.assertEqual(api_timeout(), 1234)

    def test_store_defaults_to_the_shared_constant(self):
        self.assertEqual(JobStore(":memory:").processing_timeout,
                         DEFAULT_JOB_TIMEOUT_SECONDS)

    def test_a_job_inside_the_deadline_is_left_alone(self):
        """The bug in miniature: a healthy in-flight job must survive a poll."""
        store = JobStore(":memory:", processing_timeout_seconds=60)
        job = store.create("r.pbix")
        store.mark_processing(job.id)
        self.assertIs(store.get(job.id).status, JobStatus.PROCESSING)
        self.assertTrue(store.is_active(job.id))

    def test_a_genuinely_stuck_job_is_still_force_failed_on_poll(self):
        store = JobStore(":memory:", processing_timeout_seconds=0.01)
        job = store.create("r.pbix")
        store.mark_processing(job.id)
        import time
        time.sleep(0.05)
        stuck = store.get(job.id)   # a plain status poll sweeps
        self.assertIs(stuck.status, JobStatus.FAILED)
        self.assertIn("timed out", stuck.error)
        # ...and the worker's own checkpoints stop the work.
        self.assertFalse(store.is_active(job.id))


class CeleryTimeLimitTest(unittest.TestCase):
    def test_soft_limit_trails_the_store_watchdog(self):
        """Ordering is load-bearing: the store must fail the row first so its
        specific message reaches the user, rather than the generic
        'generation failed' that a SoftTimeLimitExceeded would record."""
        try:
            from pbicompass.service.celery_app import _job_timeout_seconds, celery_app
        except ImportError:
            self.skipTest("celery not installed")
        soft = celery_app.conf.task_soft_time_limit
        hard = celery_app.conf.task_time_limit
        self.assertGreater(soft, _job_timeout_seconds())
        self.assertGreater(hard, soft)


class LlmRetryBudgetTest(unittest.TestCase):
    def test_sdk_retry_loop_is_disabled_so_ours_is_the_only_one(self):
        """Left at their defaults the SDK loops nest under ``_call_with_retries``
        and multiply: 3 x 3 = 9 HTTP attempts per agent call, ~27 min at the
        180s per-attempt ceiling."""
        from pbicompass.agents.llm import _SDK_MAX_RETRIES
        self.assertEqual(_SDK_MAX_RETRIES, 0)

    def test_meshapi_client_pins_the_sdk_to_one_attempt(self):
        try:
            from pbicompass.agents.llm import MeshAPIClient
        except ImportError:
            self.skipTest("openai not installed")
        client = MeshAPIClient(model="deepseek/deepseek-v4-flash", api_key="rsk_test")
        self.assertEqual(client._client.max_retries, 0)

    def test_one_call_makes_exactly_the_configured_attempts(self):
        try:
            import httpx
            import openai
            from pbicompass.agents.llm import MeshAPIClient
        except ImportError:
            self.skipTest("openai/httpx not installed")

        attempts = {"n": 0}

        def handler(_request):
            attempts["n"] += 1
            return httpx.Response(429, json={"error": "rate limited"})

        client = MeshAPIClient(model="deepseek/deepseek-v4-flash", api_key="rsk_test")
        client._client = openai.OpenAI(
            base_url="https://api.meshapi.ai/v1", api_key="rsk_test", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with mock.patch.dict(os.environ, {"PBICOMPASS_LLM_MAX_RETRIES": "2"}):
            with self.assertRaises(Exception):
                client.complete_json("s", "u", {"type": "object", "properties": {},
                                                "required": []})
        # 2 retries + 1 initial = 3. Not 9.
        self.assertEqual(attempts["n"], 3)


if __name__ == "__main__":
    unittest.main()
