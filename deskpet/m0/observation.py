"""Sampling -> validated estimation -> bounded user correction.

The pipeline owns semantic revisions. It never refreshes capture timestamps to
make stale data appear fresh, and never starts an OS/model client itself.
"""
from __future__ import annotations

from datetime import timedelta
import json

from deskpet.contracts import Observation, ObservationFrame, RawSignals, StateEvidence, UserCorrection, UserStateEstimate
from deskpet.mocks.sampling import BufferedSamplingSource, MissingSampleError, SamplingUnavailableError

_UNSET = object()


def _canonical(model):
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


class ObservationPipeline:
    def __init__(self, clock, sampling_source, estimator, emit):
        self.clock = clock
        self.sampling_source = sampling_source
        self.estimator = estimator
        self.emit = emit
        self._revision = 1
        self._semantic = None
        self._correction: UserCorrection | None = None
        self._correction_status = "none"
        self._frame: ObservationFrame | None = None
        self._last_raw_key = None
        self._last_estimate_key = None
        self._legacy_estimate: UserStateEstimate | None = None
        self._sampling_display_name = None
        self._legacy_sequence = 0

    def submit_sample(self, raw: RawSignals, sampling_name=None) -> None:
        submit = getattr(self.sampling_source, "submit", None)
        if not callable(submit):
            raise ValueError("configured sampling source does not accept pushed samples")
        submit(raw)
        self._legacy_estimate = None
        if sampling_name:
            self._sampling_display_name = sampling_name

    def submit_sampling_error(self, error_code: str, sampling_name=None) -> None:
        fail = getattr(self.sampling_source, "submit_failure", None)
        if not callable(fail):
            raise ValueError("configured source has no external failure input channel")
        fail(error_code)
        self._legacy_estimate = None
        if sampling_name:
            self._sampling_display_name = sampling_name

    def set_signals(self, **kwargs) -> RawSignals:
        setter = getattr(self.sampling_source, "set_signals", None)
        if not callable(setter):
            raise ValueError("configured sampling source is read-only; replace it or use its own input channel")
        raw = setter(**kwargs)
        self._legacy_estimate = None
        return raw

    def apply_correction(self, correction: UserCorrection) -> Observation:
        data = correction.model_dump(mode="python") if isinstance(correction, UserCorrection) else correction
        candidate = UserCorrection.model_validate(data)
        now = self.clock.wall_now()
        if candidate.created_at > now or candidate.valid_until <= now:
            raise ValueError("correction is future-dated or already expired")
        if not candidate.reason.strip():
            raise ValueError("correction needs a reason")
        if self._correction and self._correction.correction_id == candidate.correction_id:
            if self._correction != candidate:
                raise ValueError("correction_id was reused with different content")
            return self.read()
        self._correction = candidate
        self._correction_status = "active"
        self.emit("observation.correction.applied", source="user",
                  simulated=(candidate.origin != "user" or getattr(self.clock, "kind", "virtual") == "virtual"),
                  payload={"correction_id": candidate.correction_id, "state": candidate.state,
                           "reason": candidate.reason, "valid_until": candidate.valid_until.isoformat(),
                           "origin": candidate.origin, "automation": candidate.origin == "automation"})
        return self.read()

    def clear_override(self) -> Observation:
        if self._correction is not None and self._correction_status != "cleared":
            self._correction_status = "cleared"
            self.emit("observation.correction.cleared", source="user",
                      simulated=(self._correction.origin != "user" or getattr(self.clock, "kind", "virtual") == "virtual"),
                      payload={"correction_id": self._correction.correction_id, "origin": self._correction.origin,
                               "automation": self._correction.origin == "automation"})
        return self.read()

    def read(self) -> Observation:
        now = self.clock.wall_now()
        raw, raw_status, error = None, "missing", None
        try:
            value = self.sampling_source.sample()
            data = value.model_dump(mode="python") if isinstance(value, RawSignals) else value
            raw = RawSignals.model_validate(data)
            raw_status = "future" if raw.sampled_at > now else ("expired" if raw.valid_until <= now else "fresh")
            if raw_status != "fresh":
                error = f"raw_{raw_status}"
        except MissingSampleError:
            error = "raw_missing"
        except SamplingUnavailableError as exc:
            raw_status, error = "error", f"sampling_error:{exc.error_code}"
        except Exception as exc:
            raw_status, error = "error", f"sampling_error:{type(exc).__name__}"
        raw_key = _canonical(raw) if raw else (raw_status, error)
        if raw_key != self._last_raw_key:
            self._last_raw_key = raw_key
            self.emit("observation.sampled", source=raw.source if raw else "sampling",
                      simulated=getattr(self.clock, "kind", "virtual") == "virtual" or not raw or raw.source != "live",
                      payload={"sample_id": raw.sample_id if raw else None, "status": raw_status,
                               "sampled_at": raw.sampled_at.isoformat() if raw else None,
                               "valid_until": raw.valid_until.isoformat() if raw else None,
                               "sampler": self._sampling_name(raw), "error": error})
        estimated = self._unknown(error or "insufficient_signals", raw)
        if raw_status == "fresh":
            try:
                value = self._legacy_estimate if self._legacy_estimate is not None else self.estimator.estimate(raw)
                estimated = self._validated_estimate(value, raw, now)
            except Exception as exc:
                error = f"estimator_error:{type(exc).__name__}:{str(exc) if isinstance(exc, _EstimateError) else ''}".rstrip(":")
                estimated = self._unknown("invalid_estimate", raw)
        correction = self._correction
        if self._correction_status == "active" and correction and correction.valid_until <= now:
            self._correction_status = "expired"
            self.emit("observation.correction.expired", source="user",
                      simulated=(correction.origin != "user" or getattr(self.clock, "kind", "virtual") == "virtual"),
                      payload={"correction_id": correction.correction_id,
                               "valid_until": correction.valid_until.isoformat(), "origin": correction.origin,
                               "automation": correction.origin == "automation"})
        effective = estimated
        if self._correction_status == "active" and correction:
            detail = StateEvidence(ref="user_correction", signal="user_state", value=correction.state,
                                   sampled_at=correction.created_at, valid_until=correction.valid_until, source="user")
            effective = UserStateEstimate(estimator_id="user_correction", based_on_sample_id=raw.sample_id if raw else None,
                                          state=correction.state, source="user", evidence=("user_correction",),
                                          evidence_details=(detail,), valid_until=correction.valid_until)
        semantic = (effective.state, effective.source, effective.evidence,
                    raw.application if raw else None,
                    tuple(sorted(raw.capabilities.items())) if raw else (),
                    raw.source if raw else None, raw_status,
                    self._correction_status,
                    correction.correction_id if correction and self._correction_status == "active" else None)
        if self._semantic is not None and semantic != self._semantic:
            self._revision += 1
        self._semantic = semantic
        estimate_data = estimated.model_dump(mode="json")
        if raw_status != "fresh":
            estimate_data.pop("valid_until", None)
        estimate_key = (raw.sample_id if raw else None, json.dumps(estimate_data, sort_keys=True), error)
        if estimate_key != self._last_estimate_key:
            self._last_estimate_key = estimate_key
            self.emit("observation.estimated", source=estimated.source, simulated=getattr(self.clock, "kind", "virtual") == "virtual" or estimated.source != "rule" or not raw or raw.source != "live",
                      payload={"sample_id": raw.sample_id if raw else None, "state": estimated.state,
                               "estimator": self._estimator_name(), "evidence": list(estimated.evidence),
                               "valid_until": estimated.valid_until.isoformat() if estimated.valid_until else None,
                               "error": error})
        # A future sample is invalid but its own capture time remains inspectable in raw.
        observed_at = raw.sampled_at if raw and raw.sampled_at <= now else now
        obs = Observation(raw=raw, correction=correction if self._correction_status == "active" else None,
                          context_revision=self._revision, observed_at=observed_at, estimate=effective,
                          capabilities=raw.capabilities if raw else {}, application=raw.application if raw else None,
                          idle_s=raw.idle_s if raw else None, source=raw.source if raw else ("live" if isinstance(self.sampling_source, BufferedSamplingSource) else "scripted"))
        self._frame = ObservationFrame(observation=obs, raw=raw, raw_status=raw_status, estimated=estimated,
                                       correction=correction, correction_status=self._correction_status,
                                       sampling_name=self._sampling_name(raw), estimator_name=self._estimator_name(), error=error)
        return obs

    def snapshot(self) -> ObservationFrame:
        # Return the exact frame already supplied to consumers. Sampling here
        # could make a custom source alternate between service state and GUI state.
        if self._frame is None:
            self.read()
        return self._frame.model_copy(deep=True)

    def _validated_estimate(self, value, raw, now):
        data = value.model_dump(mode="python") if isinstance(value, UserStateEstimate) else value
        result = UserStateEstimate.model_validate(data)
        if result.source == "user":
            raise _EstimateError("estimator_cannot_forge_user_correction")
        if result.based_on_sample_id not in (None, raw.sample_id):
            raise _EstimateError("wrong_sample_id")
        until = result.valid_until or raw.valid_until
        if until > raw.valid_until:
            raise _EstimateError("estimate_outlives_raw_sample")
        if until <= now:
            raise _EstimateError("estimate_expired")
        for detail in result.evidence_details:
            if detail.sampled_at != raw.sampled_at or detail.sampled_at > now:
                raise _EstimateError("evidence_timestamp_does_not_match_sample")
            if detail.valid_until > raw.valid_until or detail.valid_until <= now:
                raise _EstimateError("invalid_evidence_validity")
            if detail.ref not in result.evidence:
                raise _EstimateError("unreferenced_evidence")
            if detail.source != raw.source:
                raise _EstimateError("evidence_source_does_not_match_sample")
            signal = detail.signal
            if signal.startswith("capabilities."):
                key = signal.split(".", 1)[1]
                if key not in raw.capabilities:
                    raise _EstimateError("unknown_evidence_signal")
                measured = raw.capabilities[key]
            elif signal in {"application", "idle_s", "input_active", "cursor_position", "window_count"}:
                measured = getattr(raw, signal)
            else:
                raise _EstimateError("unknown_evidence_signal")
            if not _same_measurement(measured, detail.value):
                raise _EstimateError("evidence_value_does_not_match_sample")
            until = min(until, detail.valid_until)
        return result.model_copy(update={"based_on_sample_id": raw.sample_id, "valid_until": until,
                                         "estimator_id": result.estimator_id if result.estimator_id != "unspecified" else self._estimator_name()})

    def _unknown(self, reason, raw):
        # Missing/invalid measurements cannot support a renewed validity interval.
        # The current unknown context remains usable for bounded planning; task/request
        # envelopes supply their own expiry, and the semantic revision rejects old plans.
        now = self.clock.wall_now()
        until = raw.valid_until if raw and raw.sampled_at <= now < raw.valid_until else None
        return UserStateEstimate(estimator_id=self._estimator_name(), based_on_sample_id=raw.sample_id if raw else None,
                                 state="unknown", source="scripted" if self._legacy_estimate is not None else "rule",
                                 evidence=(reason,), valid_until=until)

    def _sampling_name(self, raw):
        if raw is not None and raw.sampler_id != "unspecified":
            return raw.sampler_id
        return self._sampling_display_name or type(self.sampling_source).__name__

    def _estimator_name(self):
        return "legacy-state-injection" if self._legacy_estimate is not None else type(self.estimator).__name__

    def set(self, state, source="scripted", *, evidence=None, valid_until=None,
            capabilities=_UNSET, application=_UNSET, idle_s=_UNSET, observation_source=None):
        """Compatibility for v1 state-only fixtures. New integrations use raw/correction ports."""
        now = self.clock.wall_now()
        if source == "user":
            if capabilities is not _UNSET or application is not _UNSET or idle_s is not _UNSET:
                self._legacy_raw(capabilities=capabilities, application=application, idle_s=idle_s)
            self._legacy_sequence += 1
            return self.apply_correction(UserCorrection(correction_id=f"legacy-user-{self._legacy_sequence}", state=state,
                                                        reason="legacy user correction", origin="scripted", created_at=now,
                                                        valid_until=valid_until or now + timedelta(seconds=300)))
        if observation_source == "live":
            raise ValueError("state-only injection cannot claim live sampling; submit RawSignals instead")
        UserStateEstimate(state=state, source=source, evidence=tuple(evidence or ()), valid_until=valid_until)
        raw = self._legacy_raw(capabilities=capabilities, application=application, idle_s=idle_s)
        self._legacy_estimate = UserStateEstimate(estimator_id="legacy-state-injection", based_on_sample_id=raw.sample_id,
                                                  state=state, source=source, evidence=tuple(evidence or ()),
                                                  valid_until=valid_until or raw.valid_until)
        return self.read()

    def _legacy_raw(self, *, capabilities=_UNSET, application=_UNSET, idle_s=_UNSET):
        kwargs = {"ttl_s": 300.0}
        if capabilities is not _UNSET:
            kwargs["capabilities"] = capabilities
        if application is not _UNSET:
            kwargs["application"] = application
        if idle_s is not _UNSET:
            kwargs["idle_s"] = idle_s
        setter = getattr(self.sampling_source, "set_signals", None)
        if callable(setter):
            return setter(**kwargs)
        # A native read-only source cannot be silently replaced by a scripted source.
        raise ValueError("legacy state injection requires a controllable scripted sampler")


def _same_measurement(first, second):
    if type(first) in (int, float) and type(second) in (int, float):
        return first == second
    if isinstance(first, (tuple, list)) and isinstance(second, (tuple, list)):
        return len(first) == len(second) and all(_same_measurement(a, b) for a, b in zip(first, second))
    return type(first) is type(second) and first == second


class _EstimateError(ValueError):
    pass
