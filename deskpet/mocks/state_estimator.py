"""Two estimators over the same raw contract; neither performs desktop sampling."""
from __future__ import annotations

from datetime import timedelta
import math

from deskpet.contracts import RawSignals, StateEvidence, UserStateEstimate


class RuleStateEstimator:
    """A deliberately coarse baseline, not a productivity or attention classifier."""
    def __init__(self, clock):
        self.clock = clock

    def estimate(self, signals: RawSignals) -> UserStateEstimate:
        state = "unknown"
        refs, details = [], []
        idle_known = signals.capabilities.get("idle", False) and signals.idle_s is not None
        foreground_known = signals.capabilities.get("foreground", False) and bool(signals.application)
        if idle_known:
            refs.append("idle_seconds")
            details.append(self._evidence(signals, "idle_seconds", "idle_s", signals.idle_s))
            if signals.idle_s >= 120:
                state = "away"
            elif foreground_known:
                refs.append("external_foreground")
                details.append(self._evidence(signals, "external_foreground", "application", signals.application))
                state = "working" if signals.idle_s < 10 else "available"
        if state == "unknown" and not refs:
            refs.append("insufficient_signals")
        return UserStateEstimate(estimator_id=type(self).__name__, based_on_sample_id=signals.sample_id,
                                 state=state, source="rule", evidence=tuple(refs),
                                 evidence_details=tuple(details), valid_until=signals.valid_until)

    @staticmethod
    def _evidence(raw, ref, signal, value):
        return StateEvidence(ref=ref, signal=signal, value=value, sampled_at=raw.sampled_at,
                             valid_until=raw.valid_until, source=raw.source)


class ScriptedStateEstimator:
    """Replace only the estimator while retaining real/scripted raw measurements."""
    def __init__(self, clock):
        self.clock = clock
        self._state = "unknown"
        self._evidence = ("scripted_estimator",)
        self._valid_until = None

    def set_estimate(self, state: str, evidence=None, ttl_s=30.0) -> None:
        if not math.isfinite(ttl_s) or ttl_s <= 0:
            raise ValueError("estimate TTL must be finite and positive")
        try:
            until = self.clock.wall_now() + timedelta(seconds=ttl_s)
        except OverflowError as exc:
            raise ValueError("estimate TTL is outside datetime range") from exc
        candidate = UserStateEstimate(state=state, source="scripted", evidence=tuple(evidence or ("scripted_estimator",)),
                                      valid_until=until)
        self._state, self._evidence, self._valid_until = candidate.state, candidate.evidence, until

    def estimate(self, signals: RawSignals) -> UserStateEstimate:
        until = min(self._valid_until, signals.valid_until) if self._valid_until else signals.valid_until
        return UserStateEstimate(estimator_id=type(self).__name__, based_on_sample_id=signals.sample_id,
                                 state=self._state, source="scripted", evidence=self._evidence,
                                 valid_until=until)
