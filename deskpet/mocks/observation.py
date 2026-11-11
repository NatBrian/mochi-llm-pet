"""Controllable observations; an explicit user override wins until cleared/expired."""
from __future__ import annotations

from deskpet.contracts import Observation, UserStateEstimate

_UNSET = object()


class ScriptedObservationSource:
    def __init__(self, clock):
        self.clock = clock
        self._revision = 1
        self._observation_source = "scripted"
        self._base = UserStateEstimate()
        self._override: UserStateEstimate | None = None
        self._metadata = {"capabilities": {}, "application": None, "idle_s": None}

    def set(self, state: str, source: str = "scripted", *, evidence=None,
            valid_until=None, capabilities=_UNSET, application=_UNSET,
            idle_s=_UNSET, observation_source: str | None = None) -> Observation:
        if observation_source not in (None, "scripted", "live"):
            raise ValueError("observation_source must be scripted or live")
        estimate = UserStateEstimate(state=state, source=source,
                                     evidence=tuple(evidence or ()), valid_until=valid_until)
        if observation_source is not None:
            self._observation_source = observation_source
        if source == "user":
            self._override = estimate
        else:
            self._base = estimate
        for key, value in (("capabilities", capabilities), ("application", application), ("idle_s", idle_s)):
            if value is not _UNSET:
                self._metadata[key] = value
        return self.read()

    def clear_override(self) -> Observation:
        self._override = None
        return self.read()

    def read(self) -> Observation:
        now = self.clock.wall_now()
        if self._override and self._override.valid_until and now >= self._override.valid_until:
            self._override = None
        estimate = self._override or self._base
        if estimate.valid_until is not None and now >= estimate.valid_until:
            estimate = UserStateEstimate(state="unknown", source=estimate.source,
                                         evidence=("evidence_expired",))
        # Application changes affect context; idle/cursor sampling noise does not.
        semantic = (estimate.state, estimate.source, estimate.evidence, self._metadata["application"])
        previous = getattr(self, "_last_semantic", ("unknown", "scripted", (), None))
        if semantic != previous:
            self._revision += 1
        self._last_semantic = semantic
        return Observation(context_revision=self._revision, observed_at=now,
                           estimate=estimate, source=self._observation_source, **self._metadata)
