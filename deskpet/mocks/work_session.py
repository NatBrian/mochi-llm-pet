"""In-memory work-session reference adapter using the shared injected Clock."""
from __future__ import annotations

from datetime import timedelta
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from deskpet.contracts import ErrorCode, SessionCommand, SessionResult, WorkSession


class _StartArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=500)
    duration_s: float = Field(default=1500, gt=0, allow_inf_nan=False)
    task_id: str | None = None


class _EndArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmed_outcomes: list[str] | tuple[str, ...] = ()
    next_steps: list[str] | tuple[str, ...] = ()


class MemoryWorkSessionService:
    """Single-owner adapter. Mutating commands use CAS and command-ID deduplication.

    Reading a focusing session refreshes its remaining duration from monotonic time.
    Tick updates do not bump the version; automatic entry into break_due does.
    """

    def __init__(self, clock, emit):
        self.clock = clock
        self.emit = emit
        self._sessions: dict[str, WorkSession] = {}
        self._due: dict[str, float] = {}
        self._commands: dict[str, tuple[str, SessionResult]] = {}

    def get(self, session_id: str) -> WorkSession | None:
        self.pump()
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[WorkSession]:
        self.pump()
        return list(self._sessions.values())

    def apply(self, command: SessionCommand) -> SessionResult:
        self.pump()
        fingerprint = json.dumps(command.model_dump(mode="json"), sort_keys=True,
                                 separators=(",", ":"), ensure_ascii=False)
        previous = self._commands.get(command.command_id)
        if previous:
            if previous[0] != fingerprint:
                result = SessionResult(command_id=command.command_id, status="rejected",
                                       view=self._sessions.get(command.session_id),
                                       error_code=ErrorCode.INVALID_ARGUMENT)
            else:
                result = previous[1].model_copy(update={"status": "duplicate", "error_code": ErrorCode.DUPLICATE})
            self._record(command, result)
            return result

        current = self._sessions.get(command.session_id)
        error: ErrorCode | None = None
        view = current
        if command.kind == "start":
            if current is not None:
                error = ErrorCode.INVALID_TRANSITION
            elif command.expected_version != 0:
                error = ErrorCode.STALE_TASK
            else:
                try:
                    args = _StartArgs.model_validate(command.payload)
                    if not args.goal.strip():
                        raise ValueError("goal is blank")
                    view = WorkSession(session_id=command.session_id, version=1,
                                       goal=args.goal.strip(), task_id=args.task_id,
                                       remaining_s=args.duration_s,
                                       phase_due_at=self.clock.wall_now() + timedelta(seconds=args.duration_s))
                    self._due[command.session_id] = self.clock.monotonic_now() + args.duration_s
                except (ValidationError, ValueError, OverflowError):
                    error = ErrorCode.INVALID_ARGUMENT
        elif current is None:
            error = ErrorCode.NOT_FOUND
        elif command.expected_version != current.version:
            error = ErrorCode.STALE_TASK
        elif command.kind == "pause":
            if command.payload:
                error = ErrorCode.INVALID_ARGUMENT
            elif current.state != "focusing":
                error = ErrorCode.INVALID_TRANSITION
            else:
                self._due.pop(command.session_id, None)
                view = current.model_copy(update={"version": current.version + 1,
                                                 "state": "paused", "phase_due_at": None})
        elif command.kind == "resume":
            if command.payload:
                error = ErrorCode.INVALID_ARGUMENT
            elif current.state != "paused":
                error = ErrorCode.INVALID_TRANSITION
            else:
                self._due[command.session_id] = self.clock.monotonic_now() + current.remaining_s
                view = current.model_copy(update={"version": current.version + 1,
                                                 "state": "focusing",
                                                 "phase_due_at": self.clock.wall_now() + timedelta(seconds=current.remaining_s)})
        elif command.kind == "end":
            if current.state == "ended":
                error = ErrorCode.INVALID_TRANSITION
            else:
                try:
                    args = _EndArgs.model_validate(command.payload)
                    view = current.model_copy(update={"version": current.version + 1,
                                                     "state": "ended", "phase_due_at": None,
                                                     "confirmed_outcomes": tuple(args.confirmed_outcomes),
                                                     "next_steps": tuple(args.next_steps)})
                    self._due.pop(command.session_id, None)
                except ValidationError:
                    error = ErrorCode.INVALID_ARGUMENT
        if error is None and view is not None:
            self._sessions[command.session_id] = view
        result = SessionResult(command_id=command.command_id,
                               status="rejected" if error else "applied", view=view,
                               error_code=error)
        self._commands[command.command_id] = (fingerprint, result)
        self._record(command, result)
        return result

    def pump(self) -> None:
        now = self.clock.monotonic_now()
        for session_id, due in list(self._due.items()):
            current = self._sessions[session_id]
            remaining = max(0.0, due - now)
            if remaining == 0:
                updated = current.model_copy(update={"version": current.version + 1,
                                                     "remaining_s": 0.0, "state": "break_due",
                                                     "phase_due_at": None})
                self._sessions[session_id] = updated
                self._due.pop(session_id, None)
                self.emit("session.break_due", session_id=session_id,
                          payload={"version": updated.version, "state": updated.state})
            else:
                self._sessions[session_id] = current.model_copy(update={"remaining_s": remaining})

    def _record(self, command: SessionCommand, result: SessionResult) -> None:
        self.emit("session." + result.status, session_id=command.session_id,
                  payload={"command_id": command.command_id, "kind": command.kind,
                           "version": result.view.version if result.view else None,
                           "state": result.view.state if result.view else None,
                           "error_code": result.error_code.value if result.error_code else None})


FakeWorkSessionService = MemoryWorkSessionService
