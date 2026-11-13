"""Behavioral contracts for independently replaceable sampling and estimation."""
from datetime import timedelta
import subprocess
import sys

import pytest

from deskpet.contracts import (DecisionEnvelope, ErrorCode, RawSignals, StateEvidence, TaskCommand,
                               TaskDraft, UserCorrection, UserStateEstimate)
from deskpet.m0.observation import ObservationPipeline
from deskpet.m0.trace import TraceRecorder
from deskpet.mocks.clock import FakeClock
from deskpet.mocks.delivery import FakeDeliveryPort
from deskpet.mocks.sampling import BufferedSamplingSource, ScriptedSamplingSource
from deskpet.mocks.state_estimator import RuleStateEstimator, ScriptedStateEstimator
from deskpet.mocks.task_service import MemoryTaskService


@pytest.fixture
def bench():
    clock = FakeClock()
    trace = TraceRecorder(clock)
    source = ScriptedSamplingSource(clock)
    pipeline = ObservationPipeline(clock, source, RuleStateEstimator(clock), trace.emit)
    return clock, trace, source, pipeline


def correct(clock, state='working', ttl=10, identity='c1'):
    now = clock.wall_now()
    return UserCorrection(correction_id=identity, state=state, reason='用户明确确认',
                          created_at=now, valid_until=now+timedelta(seconds=ttl))


def task_service(clock, trace, pipeline):
    service = MemoryTaskService(clock, FakeDeliveryPort(clock), trace.emit)
    service.observe(pipeline.read())
    draft = TaskDraft(content='milk', earliest_at=clock.wall_now(),
                      latest_first_delivery_at=clock.wall_now()+timedelta(seconds=120))
    service.apply(TaskCommand(command_id='create', kind='create', task_id='t', payload={'draft': draft.model_dump(mode='json')}))
    service.apply(TaskCommand(command_id='confirm', kind='confirm', task_id='t', expected_version=1))
    return service


def envelope(clock, observation, request='r'):
    return DecisionEnvelope(request_id=request, task_id='t', task_version=2,
                            context_revision=observation.context_revision, created_at=clock.wall_now(),
                            expires_at=clock.wall_now()+timedelta(seconds=60))


def test_raw_capture_time_is_not_refreshed_by_reading(bench):
    clock, _, source, pipeline = bench
    raw = pipeline.set_signals(application='Editor', idle_s=15, ttl_s=30)
    first = pipeline.read()
    clock.advance(5)
    second = pipeline.read()
    assert source.sample() == raw
    assert second.observed_at == first.observed_at == raw.sampled_at
    assert second.estimate.valid_until == first.estimate.valid_until == raw.valid_until
    assert pipeline.snapshot().raw.sample_id == raw.sample_id
    new = pipeline.set_signals(idle_s=16)
    assert new.sample_id != raw.sample_id and new.sampled_at > raw.sampled_at


@pytest.mark.parametrize('idle,application,caps,expected', [
    (200,None,{'idle':True},'away'),
    (3,'Editor',{'idle':True,'foreground':True},'working'),
    (15,'Editor',{'idle':True,'foreground':True},'available'),
    (3,'Editor',{'idle':False,'foreground':True},'unknown'),
    (3,'Editor',{'idle':True,'foreground':False},'unknown'),
    (None,'Editor',{'idle':True,'foreground':True},'unknown'),
])
def test_rule_uses_only_available_signal_capabilities(bench, idle, application, caps, expected):
    clock, _, source, pipeline = bench
    source.set_signals(idle_s=idle,application=application,capabilities=caps)
    obs = pipeline.read()
    assert obs.estimate.state == expected
    assert obs.estimate.based_on_sample_id == obs.raw.sample_id
    for item in obs.estimate.evidence_details:
        assert item.sampled_at == obs.raw.sampled_at
        assert item.valid_until <= obs.raw.valid_until
        assert item.source == obs.raw.source


def test_estimator_can_change_without_replacing_sampling(bench):
    clock, trace, source, rules = bench
    source.set_signals(application='Editor', idle_s=3)
    assert rules.read().estimate.state == 'working'
    classifier = ScriptedStateEstimator(clock)
    classifier.set_estimate('available', evidence=['scripted_classifier'], ttl_s=5)
    other = ObservationPipeline(clock, source, classifier, trace.emit)
    obs = other.read()
    assert obs.raw == rules.snapshot().raw
    assert obs.estimate.state == 'available'
    assert other.snapshot().estimator_name == 'ScriptedStateEstimator'
    assert obs.estimate.valid_until == clock.wall_now()+timedelta(seconds=5)


def test_sampling_can_change_without_replacing_estimator_and_buffer_keeps_live_identity(bench):
    clock, trace, source, _ = bench
    raw = source.set_signals(application='Editor', idle_s=15)
    live = raw.model_copy(update={'source':'live','sampler_id':'IndependentNativeSampler'})
    buffer = BufferedSamplingSource(clock)
    pipeline = ObservationPipeline(clock, buffer, RuleStateEstimator(clock), trace.emit)
    assert pipeline.read().estimate.state == 'unknown'
    assert pipeline.snapshot().raw_status == 'missing'
    pipeline.submit_sample(live)
    obs = pipeline.read()
    assert obs.source == 'live' and obs.estimate.state == 'available'
    assert pipeline.snapshot().sampling_name == 'IndependentNativeSampler'
    with pytest.raises(ValueError, match='read-only'):
        pipeline.set_signals(idle_s=0)
    assert pipeline.snapshot().raw == live


def test_snapshot_does_not_resample_or_change_service_consumed_frame(bench):
    clock, trace, source, _ = bench
    one = source.set_signals(application='Editor', idle_s=15)
    two = source.set_signals(application='Browser', idle_s=2)
    class AlternatingSource:
        calls = 0
        def sample(self):
            value = (one,two)[self.calls % 2]
            self.calls += 1
            return value
    plugin = AlternatingSource()
    pipeline = ObservationPipeline(clock, plugin, RuleStateEstimator(clock), trace.emit)
    assert pipeline.snapshot().observation.estimate.state == 'available'
    assert plugin.calls == 1
    frame = pipeline.snapshot()
    assert plugin.calls == 1 and frame.observation.raw == one
    consumed = pipeline.read()
    assert consumed.estimate.state == 'working'
    assert pipeline.snapshot().observation == consumed
    assert plugin.calls == 2


def test_semantic_revision_ignores_new_sample_and_idle_noise(bench):
    clock, _, source, pipeline = bench
    source.set_signals(application='Editor',idle_s=15)
    first = pipeline.read()
    clock.advance(1)
    source.set_signals(idle_s=16)
    same = pipeline.read()
    assert same.context_revision == first.context_revision
    assert same.raw.sample_id != first.raw.sample_id
    source.set_signals(application='Browser')
    assert pipeline.read().context_revision > same.context_revision


def test_expired_raw_rejects_old_context_but_current_unknown_request_remains_possible(bench):
    clock, trace, source, pipeline = bench
    source.set_signals(application='Editor',idle_s=15,ttl_s=5)
    service = task_service(clock,trace,pipeline)
    old = envelope(clock,pipeline.snapshot().observation)
    clock.advance(5)
    unknown = pipeline.read()
    service.observe(unknown)
    frame = pipeline.snapshot()
    assert frame.raw_status == 'expired'
    assert frame.raw.valid_until <= clock.wall_now()
    assert frame.estimated.state == unknown.estimate.state == 'unknown'
    assert service.validate(old) == ErrorCode.STALE_CONTEXT
    assert service.validate(envelope(clock,unknown,'new')) is None
    service.pump()
    assert service.get('t').displayed_at is None
    clock.advance(115)
    service.observe(pipeline.read())
    service.pump()
    assert service.get('t').displayed_at is not None  # Deadline fallback does not depend on inference.


def test_user_correction_is_independent_of_raw_and_restores_base_on_clear_and_expiry(bench):
    clock, trace, source, pipeline = bench
    source.set_signals(application='Editor',idle_s=15)
    base = pipeline.read()
    active = pipeline.apply_correction(correct(clock))
    frame = pipeline.snapshot()
    assert frame.estimated.state == 'available'
    assert frame.observation.estimate.state == 'working'
    assert frame.correction_status == 'active' and frame.correction.reason == '用户明确确认'
    source.set_signals(idle_s=200)
    assert pipeline.read().estimate.state == 'working'
    assert pipeline.snapshot().estimated.state == 'away'
    restored = pipeline.clear_override()
    assert restored.estimate.state == 'away'
    assert pipeline.snapshot().correction_status == 'cleared'
    assert restored.context_revision > active.context_revision > base.context_revision
    pipeline.apply_correction(correct(clock, identity='c2'))
    until = pipeline.read().estimate.valid_until
    clock.advance(5)
    assert pipeline.read().estimate.valid_until == until
    clock.advance(5)
    assert pipeline.read().estimate.state == 'away'
    assert pipeline.snapshot().correction_status == 'expired'
    for _ in range(3):pipeline.read()
    assert len([e for e in trace.events if e.kind=='observation.correction.expired']) == 1


def test_user_correction_works_when_no_raw_measurement_exists(bench):
    clock, trace, _, _ = bench
    pipeline = ObservationPipeline(clock, BufferedSamplingSource(clock), RuleStateEstimator(clock), trace.emit)
    effective = pipeline.apply_correction(correct(clock,'available'))
    frame = pipeline.snapshot()
    assert frame.raw_status == 'missing' and frame.raw is None
    assert frame.estimated.state == 'unknown' and effective.estimate.state == 'available'
    assert effective.estimate.source == 'user' and effective.correction is not None
    assert effective.estimate.evidence_details[0].source == 'user'


def test_correction_idempotency_and_invalid_correction_leave_state_intact(bench):
    clock, trace, _, pipeline = bench
    correction = correct(clock)
    first = pipeline.apply_correction(correction)
    second = pipeline.apply_correction(correction)
    assert first == second
    assert len([e for e in trace.events if e.kind=='observation.correction.applied']) == 1
    with pytest.raises(ValueError,match='reused'):
        pipeline.apply_correction(correction.model_copy(update={'state':'available'}))
    with pytest.raises(ValueError,match='future'):
        pipeline.apply_correction(correction.model_copy(update={'correction_id':'future','created_at':clock.wall_now()+timedelta(seconds=1)}))
    assert pipeline.snapshot().observation.estimate.state == 'working'


@pytest.mark.parametrize('failure', ['wrong_sample','outlive','future_evidence','wrong_source','wrong_value','unknown_signal','user_source','shape','exception'])
def test_malformed_estimator_output_is_visible_unknown_and_never_promoted_to_fact(bench,failure):
    clock, trace, source, _ = bench
    raw=source.set_signals(application='Editor',idle_s=15)
    class FaultyEstimator:
        def estimate(self, raw):
            if failure=='exception':raise RuntimeError('private credentials must not appear')
            if failure=='shape':return {'state':'made_up'}
            result=RuleStateEstimator(clock).estimate(raw)
            if failure=='wrong_sample':return result.model_copy(update={'based_on_sample_id':'another'})
            if failure=='outlive':return result.model_copy(update={'valid_until':raw.valid_until+timedelta(seconds=1)})
            if failure=='user_source':return result.model_copy(update={'source':'user'})
            detail=result.evidence_details[0]
            updates={'future_evidence':{'sampled_at':raw.sampled_at+timedelta(seconds=1)},
                     'wrong_source':{'source':'live'},'wrong_value':{'value':99},'unknown_signal':{'signal':'screen_title'}}
            return result.model_copy(update={'evidence_details':(detail.model_copy(update=updates[failure]),)})
    pipeline=ObservationPipeline(clock,source,FaultyEstimator(),trace.emit)
    obs=pipeline.read()
    frame=pipeline.snapshot()
    assert obs.estimate.state=='unknown' and frame.error.startswith('estimator_error:')
    assert frame.raw_status=='fresh' and frame.raw==raw
    assert 'private credentials' not in frame.error


def test_estimate_cannot_outlive_shortest_evidence_even_without_pipeline_refresh(bench):
    clock,trace,source,_=bench
    source.set_signals(application='Editor',idle_s=15,ttl_s=30)
    class ShortEvidenceEstimator:
        def estimate(self,raw):
            estimate=RuleStateEstimator(clock).estimate(raw)
            details=tuple(item.model_copy(update={'valid_until':raw.sampled_at+timedelta(seconds=5)}) for item in estimate.evidence_details)
            return estimate.model_copy(update={'evidence_details':details})
    pipeline=ObservationPipeline(clock,source,ShortEvidenceEstimator(),trace.emit)
    service=task_service(clock,trace,pipeline)
    obs=pipeline.snapshot().observation
    assert obs.estimate.valid_until==clock.wall_now()+timedelta(seconds=5)
    request=envelope(clock,obs)
    clock.advance(6)
    assert service.validate(request)==ErrorCode.STALE_CONTEXT


@pytest.mark.parametrize('kind', ['future','missing','bad_shape','exception'])
def test_unavailable_sampling_is_explicit_unknown(bench,kind):
    clock,trace,source,_=bench
    raw=source.sample()
    class FaultySource:
        def sample(self):
            if kind=='future':return raw.model_copy(update={'sampled_at':raw.sampled_at+timedelta(seconds=1)})
            if kind=='missing':
                from deskpet.mocks.sampling import MissingSampleError
                raise MissingSampleError()
            if kind=='bad_shape':return {'state':'working'}
            raise TimeoutError('secret endpoint')
    pipeline=ObservationPipeline(clock,FaultySource(),RuleStateEstimator(clock),trace.emit)
    obs=pipeline.read()
    assert obs.estimate.state=='unknown'
    assert pipeline.snapshot().raw_status=={'future':'future','missing':'missing','bad_shape':'error','exception':'error'}[kind]
    assert 'secret endpoint' not in pipeline.snapshot().error


@pytest.mark.parametrize('ttl',[0,-1,float('inf'),float('nan'),1e300])
def test_invalid_signal_update_has_no_partial_capture(bench,ttl):
    _,_,source,_=bench
    before=source.sample()
    with pytest.raises(ValueError):source.set_signals(application='Changed',ttl_s=ttl)
    assert source.sample()==before


def test_legacy_state_injection_is_named_and_does_not_claim_rule_testing(bench):
    _,_,_,pipeline=bench
    obs=pipeline.set('available',evidence=['legacy_state'])
    assert obs.estimate.estimator_id=='legacy-state-injection'
    assert pipeline.snapshot().estimator_name=='legacy-state-injection'
    with pytest.raises(ValueError,match='cannot claim live'):
        pipeline.set('working',observation_source='live')
    assert pipeline.read().estimate.state=='available'


def test_observation_pipeline_import_is_headless_and_network_free():
    result=subprocess.run([sys.executable,'-c',"import sys; import deskpet.m0.observation; import deskpet.mocks.sampling; import deskpet.mocks.state_estimator; assert not any(n.startswith(('PyQt6','httpx','Quartz','AppKit','anthropic')) for n in sys.modules)"],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_live_buffer_failure_invalidates_fresh_measurement_and_recovers(bench):
    clock,trace,scripted,_=bench
    raw=scripted.set_signals(application='Editor',idle_s=15,ttl_s=30).model_copy(
        update={'source':'live','sampler_id':'NativePlugin'})
    pipeline=ObservationPipeline(clock,BufferedSamplingSource(clock),RuleStateEstimator(clock),trace.emit)
    pipeline.submit_sample(raw)
    service=task_service(clock,trace,pipeline)
    old=envelope(clock,pipeline.snapshot().observation)
    assert pipeline.snapshot().estimated.state=='available'
    pipeline.submit_sampling_error('PermissionError',sampling_name='NativePlugin')
    failure=pipeline.read()
    service.observe(failure)
    frame=pipeline.snapshot()
    assert frame.raw is None and frame.raw_status=='error'
    assert frame.error=='sampling_error:PermissionError'
    assert frame.sampling_name=='NativePlugin' and failure.source=='live'
    assert failure.estimate.state=='unknown' and service.validate(old)==ErrorCode.STALE_CONTEXT
    assert service.validate(envelope(clock,failure,'fresh-unknown')) is None
    pipeline.submit_sample(raw)
    assert pipeline.read().estimate.state=='available'
    assert pipeline.snapshot().error is None


def test_failure_payload_is_sanitized_and_scripted_full_raw_preserves_capture(bench):
    clock,trace,source,pipeline=bench
    raw=source.set_signals(application='Editor',idle_s=15)
    clock.advance(2)
    source.submit(raw)
    assert source.sample()==raw
    pipeline.submit_sampling_error('secret api key=https://private.example')
    pipeline.read()
    assert pipeline.snapshot().error=='sampling_error:SAMPLING_FAILED'
    assert 'private.example' not in str(pipeline.snapshot())
    source.set_signals(application='Editor',idle_s=20)
    assert pipeline.read().estimate.state=='available'
    with pytest.raises(ValueError,match='cannot claim live'):
        source.submit(raw.model_copy(update={'source':'live'}))


def test_virtual_time_recording_cannot_claim_real_capture_or_human_interaction(bench):
    clock,trace,source,_=bench
    raw=source.set_signals(application='Editor',idle_s=15).model_copy(update={'source':'live'})
    pipeline=ObservationPipeline(clock,BufferedSamplingSource(clock),RuleStateEstimator(clock),trace.emit)
    pipeline.submit_sample(raw)
    pipeline.read()
    pipeline.apply_correction(correct(clock).model_copy(update={'origin':'automation'}))
    pipeline.clear_override()
    pipeline.apply_correction(correct(clock,identity='c2').model_copy(update={'origin':'scripted'}))
    clock.advance(10)
    pipeline.read()
    assert all(event.simulated for event in trace.events)
    corrections=[e for e in trace.events if e.kind.startswith('observation.correction.')]
    assert corrections and all('origin' in e.payload for e in corrections)


def test_custom_sampler_private_metadata_cannot_break_error_recovery(bench):
    clock,trace,_,_=bench
    class CustomSampler:
        @property
        def source(self):
            raise RuntimeError('plugin metadata must not be accessed')
        def sample(self):
            raise TimeoutError('private endpoint')
    pipeline=ObservationPipeline(clock,CustomSampler(),RuleStateEstimator(clock),trace.emit)
    obs=pipeline.read()
    assert obs.estimate.state=='unknown'
    assert pipeline.snapshot().raw_status=='error'
    assert pipeline.snapshot().error=='sampling_error:TimeoutError'


def test_unknown_after_expiry_has_no_sliding_validity_and_preserves_raw_capture(bench):
    clock,trace,source,pipeline=bench
    raw=source.set_signals(application='Editor',idle_s=15,ttl_s=5)
    service=task_service(clock,trace,pipeline)
    old=envelope(clock,pipeline.snapshot().observation)
    clock.advance(5)
    unknown=pipeline.read()
    service.observe(unknown)
    initial_frame=pipeline.snapshot()
    assert unknown.estimate.state=='unknown' and unknown.estimate.valid_until is None
    assert initial_frame.estimated.valid_until is None
    assert service.validate(old)==ErrorCode.STALE_CONTEXT
    assert service.validate(envelope(clock,unknown,'new-unknown')) is None
    for _ in range(3):
        clock.advance(1)
        current=pipeline.read()
        frame=pipeline.snapshot()
        assert current.estimate.valid_until is None
        assert current.context_revision==unknown.context_revision
        assert frame.estimated==initial_frame.estimated
        assert frame.raw==raw and frame.raw_status=='expired'
        assert frame.error=='raw_expired'
