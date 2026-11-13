"""Boundary checks shared by producers, consumers and mock adapters."""
from datetime import datetime, timedelta, timezone
import subprocess
import sys
import pytest
from pydantic import ValidationError
from deskpet.contracts import (TaskDraft, DecisionEnvelope, ToolCall, ProposedPlan,
                               UiSpec, UiAction, Observation, UserStateEstimate)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_contract_imports_never_load_native_or_network_layers():
    code = '''
import sys
import deskpet.contracts
assert not any(n.startswith(("PyQt6", "httpx", "anthropic", "Quartz", "AppKit")) for n in sys.modules)
'''
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize('kwargs', [
    {'earliest_at': NOW.replace(tzinfo=None), 'latest_first_delivery_at': NOW},
    {'earliest_at': NOW, 'latest_first_delivery_at': NOW-timedelta(seconds=1)},
    {'earliest_at': NOW, 'latest_first_delivery_at': NOW+timedelta(seconds=1), 'mode':'hard'},
    {'earliest_at': NOW, 'latest_first_delivery_at': NOW, 'unknown_field':1},
])
def test_draft_rejects_ambiguous_or_invalid_boundaries(kwargs):
    with pytest.raises(ValidationError):
        TaskDraft(content='test', **kwargs)


def test_plan_cannot_mix_different_task_versions():
    env = DecisionEnvelope(request_id='r', task_id='t', task_version=1, context_revision=1,
                           created_at=NOW, expires_at=NOW+timedelta(seconds=30))
    call = ToolCall(call_id='c',tool_name='show_reminder', idempotency_key='i',
                    envelope=env.model_copy(update={'task_version':2}))
    with pytest.raises(ValidationError):
        ProposedPlan(envelope=env,calls=(call,))


def test_gui_vocabulary_rejects_code_and_unknown_actions():
    with pytest.raises(ValidationError):
        UiSpec(surface_id='s',surface_revision=1,components=[{'component_id':'x','type':'html','code':'...'}])
    with pytest.raises(ValidationError):
        UiAction(event_id='e',action_id='run_shell',surface_revision=1)


def test_unknown_remains_valid_without_claiming_focus():
    obs = Observation(observed_at=NOW, estimate=UserStateEstimate(state='unknown'))
    assert obs.estimate.evidence == ()


@pytest.mark.parametrize('seconds', [float('nan'), float('inf'), -1, 1e300])
def test_clock_rejects_invalid_advances_without_partial_state(seconds):
    from deskpet.mocks.clock import FakeClock
    clock = FakeClock(NOW)
    with pytest.raises(ValueError):
        clock.advance(seconds)
    assert clock.wall_now() == NOW
    assert clock.monotonic_now() == 0


def test_default_trace_file_is_one_run_and_sink_failure_preserves_business_fact(tmp_path):
    from deskpet.mocks.clock import FakeClock
    from deskpet.m0.trace import TraceRecorder, JsonlMetricsSink
    path = tmp_path/'trace.jsonl'
    sink = JsonlMetricsSink(path)
    recorder = TraceRecorder(FakeClock(NOW), sink=sink)
    recorder.emit('test.fact', payload={'first':True})
    assert len(path.read_text().splitlines()) == 1
    JsonlMetricsSink(path)  # A new output run must not append unrelated sequences.
    assert path.read_text() == ''
    class BrokenSink:
        def record(self, event):
            raise OSError('disk unavailable')
    recorder = TraceRecorder(FakeClock(NOW), sink=BrokenSink())
    recorder.emit('task.confirmed', task_id='t', task_version=2)
    assert recorder.events[0].kind == 'task.confirmed'
    assert recorder.sink_errors == ['OSError']


def test_recovery_fixture_is_valid_and_explicitly_pending_real_persistence():
    import json
    from pathlib import Path
    from deskpet.contracts import RecoveryFixture
    data=json.loads(Path('docs/contracts/recovery-fixture.json').read_text())
    fixture=RecoveryFixture.model_validate(data)
    assert fixture.expected_after_restart['m0_persistence'] is False
    assert fixture.expected_after_restart['verification']=='pending_real_SQLite_process_restart'
    assert fixture.pending_deliveries[0].delivery_id=='persisted-delivery-1'
    examples=json.loads(Path('docs/contracts/examples.json').read_text())
    assert examples['models']['RecoveryFixture'][0]==data
