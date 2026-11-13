"""M0 v1 interchange models. This module has no GUI, OS or network dependencies."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def aware_datetimes(cls, value):
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timestamps must have a UTC offset")
            return value.astimezone(timezone.utc)
        return value


class ErrorCode(str, Enum):
    STALE_TASK = "STALE_TASK"
    STALE_CONTEXT = "STALE_CONTEXT"
    STALE_SURFACE = "STALE_SURFACE"
    EXPIRED_REQUEST = "EXPIRED_REQUEST"
    CANCELLED = "CANCELLED"
    OUTSIDE_WINDOW = "OUTSIDE_WINDOW"
    DUPLICATE = "DUPLICATE"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    UNSUPPORTED = "UNSUPPORTED"


class RawSignals(Model):
    """Timestamped measurements, never a conclusion about the user's state."""
    sample_id: str
    sampler_id: str = "unspecified"
    sampled_at: datetime
    valid_until: datetime
    source: Literal["scripted", "live"] = "scripted"
    capabilities: dict[str, bool] = Field(default_factory=dict)
    application: str | None = None
    idle_s: float | None = Field(default=None, ge=0)
    input_active: bool | None = None
    cursor_position: tuple[float, float] | None = None
    window_count: int | None = Field(default=None, ge=0)
    errors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def lifetime(self):
        if self.valid_until <= self.sampled_at:
            raise ValueError("raw signal validity must follow sampled_at")
        return self


class StateEvidence(Model):
    ref: str
    signal: str
    value: Any = None
    sampled_at: datetime
    valid_until: datetime
    source: str


class UserCorrection(Model):
    origin: Literal["user", "scripted", "automation"] = "user"
    correction_id: str
    state: Literal["working", "available", "away", "unknown"]
    reason: str = Field(min_length=1, max_length=500)
    created_at: datetime
    valid_until: datetime

    @model_validator(mode="after")
    def lifetime(self):
        if self.valid_until <= self.created_at:
            raise ValueError("correction validity must follow created_at")
        return self


class UserStateEstimate(Model):
    estimator_id: str = "unspecified"
    based_on_sample_id: str | None = None
    evidence_details: tuple[StateEvidence, ...] = ()
    state: Literal["working", "available", "away", "unknown"] = "unknown"
    evidence: tuple[str, ...] = ()
    source: Literal["rule", "scripted", "user", "model"] = "scripted"
    valid_until: datetime | None = None


class Observation(Model):
    raw: RawSignals | None = None
    correction: UserCorrection | None = None
    context_revision: int = Field(default=1, ge=1)
    observed_at: datetime
    estimate: UserStateEstimate = Field(default_factory=UserStateEstimate)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    application: str | None = None
    idle_s: float | None = Field(default=None, ge=0)
    source: Literal["scripted", "live"] = "scripted"


class ObservationFrame(Model):
    """Inspectable pipeline state, including the estimate before user correction."""
    observation: Observation
    raw: RawSignals | None = None
    raw_status: Literal["fresh", "expired", "missing", "future", "error"]
    estimated: UserStateEstimate
    correction: UserCorrection | None = None
    correction_status: Literal["none", "active", "expired", "cleared"] = "none"
    sampling_name: str
    estimator_name: str
    error: str | None = None


class TaskDraft(Model):
    content: str = Field(min_length=1, max_length=500)
    mode: Literal["hard", "soft"] = "soft"
    timezone: str = "UTC"
    earliest_at: datetime
    latest_first_delivery_at: datetime

    @model_validator(mode="after")
    def window_valid(self):
        if self.latest_first_delivery_at < self.earliest_at:
            raise ValueError("latest_first_delivery_at precedes earliest_at")
        if self.mode == "hard" and self.latest_first_delivery_at != self.earliest_at:
            raise ValueError("hard reminders have a single confirmed due time")
        return self


class TaskView(Model):
    task_id: str
    version: int = Field(ge=1)
    draft: TaskDraft
    status: Literal["draft", "registered", "completed", "cancelled"] = "draft"
    occurrence: int = Field(default=1, ge=1)
    confirmed_at: datetime | None = None
    displayed_at: datetime | None = None


class TaskCommand(Model):
    command_id: str
    kind: Literal["create", "confirm", "edit", "snooze", "cancel", "complete"]
    task_id: str
    expected_version: int = Field(default=0, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandResult(Model):
    command_id: str
    status: Literal["applied", "rejected", "duplicate"]
    actual_version: int | None = None
    error_code: ErrorCode | None = None
    view: TaskView | None = None
    message: str = ""


class DecisionEnvelope(Model):
    request_id: str
    task_id: str
    task_version: int = Field(ge=1)
    context_revision: int = Field(ge=1)
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def expiry_valid(self):
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must follow created_at")
        return self


class ToolCall(Model):
    call_id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    envelope: DecisionEnvelope
    idempotency_key: str


class ProposedPlan(Model):
    envelope: DecisionEnvelope
    calls: tuple[ToolCall, ...] = ()

    @model_validator(mode="after")
    def matching_envelopes(self):
        if any(c.envelope != self.envelope for c in self.calls):
            raise ValueError("all calls must carry the plan envelope")
        return self


class ToolResult(Model):
    call_id: str
    status: Literal["succeeded", "pending", "rejected", "failed", "timeout", "duplicate"]
    error_code: ErrorCode | None = None
    receipt_id: str | None = None
    state_version: int | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class DisplayCommand(Model):
    delivery_id: str
    task_id: str
    task_version: int
    occurrence: int
    content: str
    requested_at: datetime
    expires_at: datetime
    reason: Literal["hard_due", "opportunity", "deadline_fallback", "tool", "retry"]


class DisplayReceipt(Model):
    delivery_id: str
    task_id: str
    task_version: int
    rendered_at: datetime
    outcome: Literal["displayed", "failed", "rejected"]
    source: Literal["simulated", "native", "offscreen"]
    error_code: ErrorCode | None = None


class UiComponent(Model):
    component_id: str
    type: Literal["text", "card", "text_input", "select", "button", "list"]
    label: str = ""
    action_id: str | None = None
    options: tuple[str, ...] = ()
    data_key: str | None = None


class UiSpec(Model):
    schema_version: Literal[1] = 1
    surface_id: str
    surface_revision: int = Field(ge=1)
    task_id: str | None = None
    task_version: int | None = None
    components: tuple[UiComponent, ...] = ()
    data: dict[str, Any] = Field(default_factory=dict)


class UiAction(Model):
    event_id: str
    action_id: Literal["reminder.create", "reminder.confirm", "reminder.done", "reminder.snooze",
                       "reminder.edit", "reminder.cancel", "state.override", "session.start",
                       "session.pause", "session.resume", "session.end", "state.clear_override"]
    task_id: str | None = None
    expected_task_version: int | None = None
    surface_revision: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkSession(Model):
    session_id: str
    version: int = Field(ge=1)
    task_id: str | None = None
    goal: str
    state: Literal["focusing", "paused", "break_due", "ended"] = "focusing"
    remaining_s: float = Field(ge=0)
    phase_due_at: datetime | None = None
    confirmed_outcomes: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()


class SessionCommand(Model):
    command_id: str
    kind: Literal["start", "pause", "resume", "end"]
    session_id: str
    expected_version: int = Field(default=0, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionResult(Model):
    command_id: str
    status: Literal["applied", "rejected", "duplicate"]
    view: WorkSession | None = None
    error_code: ErrorCode | None = None


class TraceEvent(Model):
    schema_version: Literal[1] = 1
    seq: int = Field(ge=1)
    ts: datetime
    monotonic_s: float
    run_id: str
    scenario_id: str
    kind: str
    source: str
    simulated: bool
    clock: Literal["virtual", "wall"]
    task_id: str | None = None
    task_version: int | None = None
    request_id: str | None = None
    call_id: str | None = None
    delivery_id: str | None = None
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ScenarioEvent(Model):
    seq: int = Field(ge=1)
    at_s: float = Field(ge=0)
    kind: Literal["command", "observation", "advance", "receipt", "session", "tool", "fault", "decision", "raw_sample", "correction", "clear_correction"]
    payload: dict[str, Any] = Field(default_factory=dict)


class Scenario(Model):
    schema_version: Literal[1] = 1
    scenario_id: str
    description: str = ""
    initial_time: datetime
    events: tuple[ScenarioEvent, ...]
    expected_outcomes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ordered(self):
        order = [(e.at_s, e.seq) for e in self.events]
        if order != sorted(order) or len({e.seq for e in self.events}) != len(order):
            raise ValueError("scenario events must have unique seq and be ordered by (at_s, seq)")
        return self


class RecoveryFixture(Model):
    """Portable recovery boundary for B; not an SQL schema or M0 restore implementation."""
    schema_version: Literal[1] = 1
    captured_at: datetime
    tasks: tuple[TaskView, ...]
    pending_deliveries: tuple[DisplayCommand, ...] = ()
    job_examples: tuple[dict[str, Any], ...] = ()
    expected_after_restart: dict[str, Any]
