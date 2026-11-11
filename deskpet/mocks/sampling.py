"""Controllable raw measurements, independent of any state-estimation policy."""
from __future__ import annotations

from datetime import timedelta
import math
import re

from deskpet.contracts import RawSignals

_UNSET = object()


class MissingSampleError(LookupError):
    """No measurement has been submitted yet; this is not a fake desktop."""


class SamplingUnavailableError(RuntimeError):
    def __init__(self, error_code):
        self.error_code = error_code
        super().__init__(error_code)


def _error_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", value) else "SAMPLING_FAILED"


class ScriptedSamplingSource:
    """An explicit sample retains its capture time until the next set_signals call."""
    def __init__(self, clock):
        self.clock = clock
        self._sequence = 0
        self._sample = None
        self._failure = None
        self.set_signals()

    def sample(self) -> RawSignals:
        if self._failure:
            raise SamplingUnavailableError(self._failure)
        return self._sample.model_copy(deep=True)

    def submit_failure(self, error_code: str) -> None:
        self._failure = _error_code(error_code)
        self._sample = None

    def submit(self, raw: RawSignals) -> None:
        """Replay an explicit scripted capture without renewing its time or identity."""
        data = raw.model_dump(mode="python") if isinstance(raw, RawSignals) else raw
        sample = RawSignals.model_validate(data)
        if sample.source != "scripted":
            raise ValueError("scripted sampler cannot claim live captures; use an explicit buffer")
        self._sample = sample.model_copy(deep=True)
        self._failure = None
        if sample.sample_id.startswith("scripted-sample-"):
            try:
                self._sequence = max(self._sequence, int(sample.sample_id.rsplit("-", 1)[1]))
            except ValueError:
                pass

    def set_signals(self, *, application=_UNSET, idle_s=_UNSET, input_active=_UNSET,
                    cursor_position=_UNSET, window_count=_UNSET, capabilities=None,
                    ttl_s=30.0, errors=()) -> RawSignals:
        if not math.isfinite(ttl_s) or ttl_s <= 0:
            raise ValueError("sample TTL must be finite and positive")
        now = self.clock.wall_now()
        try:
            until = now + timedelta(seconds=ttl_s)
        except OverflowError as exc:
            raise ValueError("sample TTL is outside datetime range") from exc
        previous = self._sample
        data = {"application": None, "idle_s": None, "input_active": None,
                "cursor_position": None, "window_count": None}
        caps = {"foreground": False, "idle": False, "input_activity": False,
                "cursor": False, "windows": False}
        if previous:
            data.update({key: getattr(previous, key) for key in data})
            caps.update(previous.capabilities)
        updates = {"application": application, "idle_s": idle_s, "input_active": input_active,
                   "cursor_position": cursor_position, "window_count": window_count}
        cap_names = {"application": "foreground", "idle_s": "idle", "input_active": "input_activity",
                     "cursor_position": "cursor", "window_count": "windows"}
        for key, value in updates.items():
            if value is not _UNSET:
                data[key] = value
                caps[cap_names[key]] = value is not None
        # Explicit capability loss wins even when the last numeric value is retained.
        if capabilities is not None:
            caps = dict(capabilities)
        sample = RawSignals(sample_id=f"scripted-sample-{self._sequence + 1}",
                            sampler_id=type(self).__name__, sampled_at=now, valid_until=until,
                            source="scripted", capabilities=caps, errors=tuple(errors), **data)
        self._sequence += 1
        self._sample = sample
        self._failure = None
        return self.sample()


class BufferedSamplingSource:
    """Worker-side buffer for measurements captured by the native GUI thread."""
    source = "live"
    def __init__(self, clock):
        self.clock = clock
        self._sample: RawSignals | None = None
        self._failure = None

    def submit(self, raw: RawSignals) -> None:
        # Revalidate model copies too; callers cannot sneak invalid timestamps through model_copy.
        data = raw.model_dump(mode="python") if isinstance(raw, RawSignals) else raw
        self._sample = RawSignals.model_validate(data).model_copy(deep=True)
        self._failure = None

    def submit_failure(self, error_code: str) -> None:
        self._failure = _error_code(error_code)
        self._sample = None

    def sample(self) -> RawSignals:
        if self._failure:
            raise SamplingUnavailableError(self._failure)
        if self._sample is None:
            raise MissingSampleError("no raw sample has arrived")
        return self._sample.model_copy(deep=True)
