"""In-process, content-free operational metrics (Day 20, §9/§11).

Tracks job throughput/failure counts and AI token usage so an operator can
answer the roadmap's own questions — jobs/min, failure rate, cost/job, 429
rate — without any report content ever touching this module. "Cost/job" is
reported as **average LLM token counts per job**, not a dollar figure:
per-token pricing varies by provider/model and changes over time, so a
hard-coded price table would go stale silently; an operator who knows their
own provider's rate can multiply these counts themselves. Everything here is
already content-free per ``Job.usage``'s own contract (agent names + integer
call/token counts only).

One process-wide instance backs the ``/metrics`` endpoint (naturally
per-instance/per-process, the same scoping every other in-memory piece of
this service has — a multi-instance deployment aggregates by scraping each
instance, the standard Prometheus pattern, not by this module reaching across
processes). Tests construct their own ``MetricsRegistry()`` for isolation.
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class MetricsRegistry:
    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._now = now
        self._lock = threading.Lock()
        self.jobs_created = 0
        self.jobs_done = 0
        self.jobs_failed = 0
        self.quota_rejected = 0
        self.rate_limited = 0
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.jobs_with_usage = 0
        self._completions: list[float] = []  # timestamps of done+failed, for jobs/min

    def record_job_created(self) -> None:
        with self._lock:
            self.jobs_created += 1

    def record_job_done(self, usage: dict | None = None) -> None:
        with self._lock:
            self.jobs_done += 1
            self._completions.append(self._now())
            if usage:
                self.jobs_with_usage += 1
                for agent_usage in usage.values():
                    self.total_calls += int(agent_usage.get("calls", 0) or 0)
                    self.total_input_tokens += int(agent_usage.get("input_tokens", 0) or 0)
                    self.total_output_tokens += int(agent_usage.get("output_tokens", 0) or 0)

    def record_job_failed(self) -> None:
        with self._lock:
            self.jobs_failed += 1
            self._completions.append(self._now())

    def record_quota_rejected(self) -> None:
        with self._lock:
            self.quota_rejected += 1

    def record_rate_limited(self) -> None:
        with self._lock:
            self.rate_limited += 1

    def _trim(self, cutoff: float) -> None:
        while self._completions and self._completions[0] < cutoff:
            self._completions.pop(0)

    def snapshot(self, window_seconds: float = 60.0) -> dict:
        """Point-in-time metrics. ``window_seconds`` controls the trailing
        window ``jobs_per_minute`` is computed over (default: the last minute).
        """
        with self._lock:
            now = self._now()
            # An hour of history is plenty for any window callers might pass,
            # and keeps this list from growing unbounded across a long-lived
            # process without needing a background sweeper of its own.
            self._trim(now - max(3600.0, window_seconds))
            recent = sum(1 for t in self._completions if t >= now - window_seconds)
            finished = self.jobs_done + self.jobs_failed
            failure_rate = (self.jobs_failed / finished) if finished else 0.0
            with_usage = self.jobs_with_usage
            return {
                "jobs_created": self.jobs_created,
                "jobs_done": self.jobs_done,
                "jobs_failed": self.jobs_failed,
                "jobs_per_minute": round(recent / (window_seconds / 60.0), 3),
                "failure_rate": round(failure_rate, 4),
                "quota_rejected_total": self.quota_rejected,
                "rate_limited_total": self.rate_limited,
                "http_429_total": self.quota_rejected + self.rate_limited,
                "avg_input_tokens_per_job": round(self.total_input_tokens / with_usage, 1) if with_usage else 0.0,
                "avg_output_tokens_per_job": round(self.total_output_tokens / with_usage, 1) if with_usage else 0.0,
                "avg_llm_calls_per_job": round(self.total_calls / with_usage, 2) if with_usage else 0.0,
            }

    def to_prometheus_text(self) -> str:
        """Minimal hand-rolled Prometheus text exposition format — no
        ``prometheus_client`` dependency needed for a dozen counters/gauges."""
        snap = self.snapshot()
        metrics = [
            ("pbicompass_jobs_created_total", "counter", "Jobs accepted.", snap["jobs_created"]),
            ("pbicompass_jobs_done_total", "counter", "Jobs completed successfully.", snap["jobs_done"]),
            ("pbicompass_jobs_failed_total", "counter", "Jobs that failed or timed out.", snap["jobs_failed"]),
            ("pbicompass_jobs_per_minute", "gauge", "Jobs finished per minute (trailing 60s).", snap["jobs_per_minute"]),
            ("pbicompass_failure_rate", "gauge", "Fraction of finished jobs that failed.", snap["failure_rate"]),
            ("pbicompass_http_429_total", "counter", "Requests rejected with 429 (quota + rate limit).", snap["http_429_total"]),
            ("pbicompass_quota_rejected_total", "counter", "429s from the per-plan daily quota.", snap["quota_rejected_total"]),
            ("pbicompass_rate_limited_total", "counter", "429s from the per-IP upload rate limiter.", snap["rate_limited_total"]),
            ("pbicompass_avg_input_tokens_per_job", "gauge", "Average LLM input tokens per completed job (cost proxy).", snap["avg_input_tokens_per_job"]),
            ("pbicompass_avg_output_tokens_per_job", "gauge", "Average LLM output tokens per completed job (cost proxy).", snap["avg_output_tokens_per_job"]),
            ("pbicompass_avg_llm_calls_per_job", "gauge", "Average LLM calls per completed job.", snap["avg_llm_calls_per_job"]),
        ]
        lines: list[str] = []
        for name, mtype, help_text, value in metrics:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {mtype}")
            lines.append(f"{name} {value}")
        return "\n".join(lines) + "\n"
