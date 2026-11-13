"""Deterministic fake display, explicitly labelled simulated in each receipt."""
from __future__ import annotations

from deskpet.contracts import DisplayCommand, DisplayReceipt, ErrorCode


class FakeDeliveryPort:
    def __init__(self, clock, outcome="displayed"):
        self.clock = clock
        self.outcome = outcome
        self.commands: list[DisplayCommand] = []
        self.visible: dict[str, DisplayCommand] = {}
        self.attempts: dict[str, int] = {}
        self._seen: dict[str, DisplayCommand] = {}
        self._receipts: dict[str, DisplayReceipt] = {}
        self.dismissed: list[str] = []

    def show(self, command: DisplayCommand) -> DisplayReceipt | None:
        previous = self._seen.get(command.delivery_id)
        # requested_at/reason can differ during an idempotent delivery retry.
        same_occurrence = previous is None or (
            previous.task_id == command.task_id
            and previous.task_version == command.task_version
            and previous.occurrence == command.occurrence
            and previous.content == command.content
        )
        if not same_occurrence:
            return self._receipt(command, "rejected", ErrorCode.INVALID_ARGUMENT)
        self.attempts[command.delivery_id] = self.attempts.get(command.delivery_id, 0) + 1
        if command.delivery_id in self._receipts:
            return self._receipts[command.delivery_id]
        if previous is None:
            self._seen[command.delivery_id] = command
            self.commands.append(command)
        if self.clock.wall_now() > command.expires_at:
            return self._receipt(command, "rejected", ErrorCode.EXPIRED_REQUEST)
        if self.outcome == "missing":
            return None
        if self.outcome == "failed":
            return self._receipt(command, "failed", ErrorCode.FAILED)
        if self.outcome != "displayed":
            raise ValueError("outcome must be displayed, failed or missing")
        receipt = self._receipt(command, "displayed")
        self.visible[command.delivery_id] = command
        self._receipts[command.delivery_id] = receipt
        return receipt

    def dismiss(self, task_id: str) -> None:
        self.dismissed.append(task_id)
        self.visible = {key: value for key, value in self.visible.items() if value.task_id != task_id}

    def _receipt(self, command, outcome, error=None):
        return DisplayReceipt(delivery_id=command.delivery_id, task_id=command.task_id,
                              task_version=command.task_version,
                              rendered_at=self.clock.wall_now(), outcome=outcome,
                              source="simulated", error_code=error)
