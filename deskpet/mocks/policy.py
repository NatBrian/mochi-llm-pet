"""Scripted policies demonstrate the public fast/slow ports without networking."""
from __future__ import annotations

from datetime import timedelta

from deskpet.contracts import ProposedPlan, ToolCall


class ScriptedFastPolicy:
    def decide(self, observation, task) -> str:
        state = observation.estimate.state
        if state == "unknown":
            return "request_slow"
        if state == "available" or task.draft.mode == "hard":
            return "react"
        return "defer"


class ScriptedSlowPlanner:
    """One bounded next step, with explicit failure feedback rather than chat text.

    A completed or pending display stops this plan; failed display/unknown state
    schedules a bounded recheck. The deterministic scheduler still owns deadlines.
    """
    def __init__(self, clock=None):
        self.clock = clock

    def plan(self, observation, task, envelope, results=()):
        if task.status != "registered":
            return ProposedPlan(envelope=envelope)
        if results and any(r.status in ("succeeded", "pending")
                           or (r.status == "duplicate" and r.error_code is None) for r in results):
            return ProposedPlan(envelope=envelope)
        if not results and observation.estimate.state == "available":
            name, args = "show_reminder", {}
        else:
            delay = 5 if results else 30
            base_time = self.clock.wall_now() if self.clock is not None else max(observation.observed_at, envelope.created_at)
            when = min(base_time + timedelta(seconds=delay),
                       task.draft.latest_first_delivery_at)
            name, args = "schedule_recheck", {"run_at": when.isoformat()}
        suffix = len(results)
        call = ToolCall(call_id=f"{envelope.request_id}:{suffix}:{name}", tool_name=name,
                        args=args, envelope=envelope,
                        idempotency_key=f"{envelope.request_id}:{suffix}:{name}")
        return ProposedPlan(envelope=envelope, calls=(call,))
