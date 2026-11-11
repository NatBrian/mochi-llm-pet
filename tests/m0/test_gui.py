"""Qt integration runs in fresh processes, isolated from legacy global QApps."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def _gui(output: Path, profile="scenario", adapters=None):
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(ROOT)
    code = (
        "from types import SimpleNamespace; from deskpet.m0.gui import run_gui; "
        f"raise SystemExit(run_gui(SimpleNamespace(profile={profile!r}, scenario='reminder-flow', "
        f"output={str(output)!r}, auto_smoke=True, adapters={adapters!r})))"
    )
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=45)


def test_qt_queued_workflow_uses_paint_receipts(tmp_path):
    process = _gui(tmp_path)
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads((tmp_path / "native-gui-report.json").read_text())
    assert report["status"] == "passed"
    assert report["receipt_source"] == "offscreen"
    assert report["native_gui_verified"] is False
    assert report["real_model_calls"] == 0
    assert all(report["checks"].values())
    assert set(report["checks"]) >= {"paint_receipt", "snoozed", "user_completed", "cancelled",
                                     "stale_rejected", "session_paused", "session_review"}
    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    shown = [event for event in events if event["kind"] == "delivery.displayed"]
    assert len(shown) == 2
    assert all(e["source"] == "offscreen" and e["simulated"] for e in shown)
    assert len({e["delivery_id"] for e in shown}) == 2
    assert (tmp_path / "native-gui.png").stat().st_size > 1000
    assert report["errors"] == []
    assert set(report["checks"]) >= {"raw_to_estimate", "user_correction_applied", "raw_does_not_override_user",
                                     "clear_restores_estimate", "expired_raw_unknown", "expired_context_rejected",
                                     "correction_without_fresh_raw", "correction_expired"}
    frame = report["observation_frame"]
    assert frame["raw_status"] == "expired"
    assert frame["correction_status"] == "expired"
    assert frame["correction"]["origin"] == "automation"
    checkpoint = report["observation_checkpoints"]["raw_to_estimate"]
    assert checkpoint["raw_status"] == "fresh"
    assert checkpoint["estimated"]["evidence_details"]
    assert checkpoint["estimated"]["valid_until"] == checkpoint["raw"]["valid_until"]
    assert (tmp_path / "observation-raw_to_estimate.png").stat().st_size > 1000
    assert frame["observation"]["estimate"]["state"] == "unknown"
    assert frame["raw"]["sampled_at"] < frame["raw"]["valid_until"]
    assert (tmp_path / "observation.png").stat().st_size > 1000


def test_live_profile_cannot_silently_fall_back_to_offscreen(tmp_path):
    process = _gui(tmp_path, "live-smoke")
    assert process.returncode == 2, process.stdout + process.stderr
    report = json.loads((tmp_path / "native-gui-report.json").read_text())
    assert report["status"] == "unsupported"
    assert report["native_gui_verified"] is False
    assert "native QApplication" in report["reason"]
    assert not (tmp_path / "trace.jsonl").exists()


def test_invalid_replacement_reports_failure_and_shuts_down(tmp_path):
    process = _gui(tmp_path, adapters={"task_service": "missing_m0_adapter:factory"})
    assert process.returncode == 1, process.stdout + process.stderr
    report = json.loads((tmp_path / "native-gui-report.json").read_text())
    assert report["status"] == "failed"
    assert report["native_gui_verified"] is False
    assert any("ModuleNotFoundError" in error for error in report["errors"])


def test_stale_ui_action_and_late_display_cannot_reopen_cancelled_task(tmp_path):
    """Exercise the public action gateway with an old surface and a late delivery."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(ROOT))
    code = r'''
import json, sys
from pathlib import Path
from types import SimpleNamespace
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from deskpet.contracts import UiAction
from deskpet.m0.gui import Workbench
app = QApplication([])
w = Workbench(SimpleNamespace(profile="scenario", scenario="stale-ui", output=sys.argv[1], auto_smoke=False, adapters=None))
w.show()
w.delay.setValue(0)
w.mode.setCurrentText("hard")
state = {"step": 0, "ticks": 0, "late": None}
timer = QTimer()
def step():
    state["ticks"] += 1
    if state["ticks"] > 240:
        Path(sys.argv[1], "negative.json").write_text(json.dumps({"timeout": True, "errors": w.errors}))
        timer.stop(); w.close(); return
    n = state["step"]
    if n == 0 and w.ready_for_actions:
        w.create_button.click(); state["step"] = 1
    elif n == 1 and "gui-1" in w.views:
        w.confirm_button.click(); state["step"] = 2
    elif n == 2 and w.cards and w.views["gui-1"]["displayed_at"]:
        state["late"] = next(iter(w.cards.values())).command
        w._task("edit", {"content": "新版本", "mode": "hard", "delay_s": 10, "window_s": 0})
        state["step"] = 3
    elif n == 3 and w.views["gui-1"]["version"] == 3:
        stale = UiAction(event_id="old-ui", action_id="reminder.done", task_id="gui-1", expected_task_version=2, surface_revision=2)
        w.action.emit({"kind": "ui", "action": stale.model_dump(mode="json")})
        state["step"] = 4
    elif n == 4 and any(r.get("error_code") == "STALE_SURFACE" for r in w.results):
        assert w.views["gui-1"]["status"] == "registered"
        w.cancel_button.click(); state["step"] = 5
    elif n == 5 and w.views["gui-1"]["status"] == "cancelled":
        w._display(state["late"]); state["step"] = 6
    elif n == 6:
        Path(sys.argv[1], "negative.json").write_text(json.dumps({"status": w.views["gui-1"]["status"], "cards": len(w.cards), "errors": w.errors, "stale_rejected": any(r.get("error_code") == "STALE_SURFACE" for r in w.results)}))
        timer.stop(); w.close()
timer.timeout.connect(step)
timer.start(50)
app.exec()
'''
    process = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT,
                             env=env, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads((tmp_path / "negative.json").read_text())
    assert report == {"status": "cancelled", "cards": 0, "errors": [], "stale_rejected": True}


def test_evidence_grab_does_not_create_a_display_receipt(tmp_path):
    """Capturing an obscured/failed window must not manufacture presentation proof."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(ROOT))
    code = r'''
from datetime import datetime, timedelta, timezone
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from deskpet.contracts import DisplayCommand
from deskpet.m0.gui import ReminderCard
app = QApplication([])
w = QWidget()
w._capturing = True
w._finishing = False
layout = QVBoxLayout(w)
now = datetime.now(timezone.utc)
cmd = DisplayCommand(delivery_id="d", task_id="t", task_version=2, occurrence=1,
                     content="native proof", requested_at=now, expires_at=now+timedelta(seconds=30), reason="hard_due")
card = ReminderCard(cmd, [])
seen = []
card.painted.connect(seen.append)
layout.addWidget(card)
w.show()
for _ in range(5): app.processEvents()
w.grab()
for _ in range(5): app.processEvents()
assert not seen, "evidence capture manufactured a receipt"
w._capturing = False
card.update()
for _ in range(10): app.processEvents()
assert len(seen) == 1, "normal visible paint must still report once"
w._finishing = True
other = ReminderCard(cmd.model_copy(update={"delivery_id":"late"}), [])
other.painted.connect(seen.append)
layout.addWidget(other)
other.show()
w.grab()
for _ in range(10): app.processEvents()
assert len(seen) == 1, "final failure screenshot manufactured a late receipt"
w.close()
'''
    process = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=15)
    assert process.returncode == 0, process.stdout + process.stderr


def test_observation_panel_independently_replaces_sampler_and_estimator(tmp_path):
    """Public GUI controls exercise two independent factories and bounded correction."""
    (tmp_path / 'replacement_obs.py').write_text('''
from deskpet.mocks.sampling import ScriptedSamplingSource
from deskpet.mocks.state_estimator import ScriptedStateEstimator
class IndependentSampler(ScriptedSamplingSource): pass
class IndependentEstimator(ScriptedStateEstimator): pass
def make_sampler(*, clock): return IndependentSampler(clock)
def make_estimator(*, clock): return IndependentEstimator(clock)
''')
    adapters = tmp_path / 'adapters.json'
    adapters.write_text(json.dumps({'sampling_source':'replacement_obs:make_sampler',
                                    'state_estimator':'replacement_obs:make_estimator'}))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen',
               PYTHONPATH=str(tmp_path)+os.pathsep+str(ROOT))
    code = r'''
import json, sys
from pathlib import Path
from types import SimpleNamespace
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from deskpet.m0.gui import Workbench
app = QApplication([])
w = Workbench(SimpleNamespace(profile='scenario', scenario='independent-observation',
              output=sys.argv[1], auto_smoke=False, adapters=sys.argv[2]))
w.show()
timer = QTimer()
progress = {'step':0, 'ticks':0, 'first_sample':None, 'estimated_same_sample':False}
def frame(): return w.latest.get('observation_frame', {})
def step():
    progress['ticks'] += 1
    if progress['ticks'] > 240:
        Path(sys.argv[1], 'observation-panel.json').write_text(json.dumps({'timeout':True,'progress':progress,'errors':w.errors}))
        timer.stop();w.close();return
    n = progress['step']
    f = frame()
    if n == 0 and w.ready_for_actions:
        w.tabs.setCurrentIndex(1)
        w.raw_application.setText('Reader')
        w.raw_idle.setValue(15)
        w.raw_input.setCurrentText('true')
        w.raw_ttl.setValue(10)
        w.raw_submit.click()
        progress['step'] = 1
    elif n == 1 and f.get('raw', {}).get('application') == 'Reader':
        assert 'IndependentSampler' in f['sampling_name']
        assert f['raw']['input_active'] is True
        assert f['raw']['capabilities']['input_activity'] is True
        assert 'IndependentEstimator' in f['estimator_name']
        assert w.estimator_controls.isEnabled()
        assert w.observation_advance_button.isEnabled()
        progress['first_sample'] = f['raw']['sample_id']
        w.estimator_state.setCurrentText('available')
        w.estimator_evidence.setText('independent_estimator_evidence')
        w.estimator_ttl.setValue(4)
        w.estimator_apply.click()
        progress['step'] = 2
    elif n == 2 and f.get('estimated', {}).get('state') == 'available':
        assert f['raw']['sample_id'] == progress['first_sample'], 'estimator edit changed raw facts'
        progress['estimated_same_sample'] = True
        w.state.setCurrentText('working')
        w.correction_reason.setText('manual correction reason')
        w.correction_ttl.setValue(3)
        w.correction_apply.click()
        progress['step'] = 3
    elif n == 3 and f.get('correction_status') == 'active':
        assert f['estimated']['state'] == 'available'
        assert f['observation']['estimate']['state'] == 'working'
        assert f['correction']['reason'] == 'manual correction reason'
        w.raw_idle.setValue(20)
        w.raw_submit.click()
        progress['step'] = 4
    elif n == 4 and f.get('raw', {}).get('sample_id') != progress['first_sample']:
        assert f['observation']['estimate']['state'] == 'working'
        w.correction_clear.click()
        progress['step'] = 5
    elif n == 5 and f.get('correction_status') == 'cleared':
        assert f['observation']['estimate']['state'] == 'available'
        w.action.emit({'kind':'advance','seconds':5})
        progress['step'] = 6
    elif n == 6 and f.get('estimated', {}).get('state') == 'unknown':
        assert f['raw_status'] == 'fresh', 'estimate TTL must not edit raw TTL'
        w.state.setCurrentText('away')
        w.correction_ttl.setValue(1)
        w.correction_apply.click()
        progress['step'] = 7
    elif n == 7 and f.get('correction_status') == 'active':
        w.action.emit({'kind':'advance','seconds':2})
        progress['step'] = 8
    elif n == 8 and f.get('correction_status') == 'expired':
        result = {'status':'passed', 'estimated_same_sample':progress['estimated_same_sample'],
                  'frame':f, 'errors':w.errors, 'raw_text':w.raw_view.toPlainText(),
                  'estimate_text':w.estimate_view.toPlainText(), 'correction_text':w.correction_view.toPlainText()}
        Path(sys.argv[1], 'observation-panel.json').write_text(json.dumps(result))
        timer.stop();w.close()
timer.timeout.connect(step)
timer.start(50)
app.exec()
'''
    process = subprocess.run([sys.executable, '-c', code, str(tmp_path), str(adapters)],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stdout+process.stderr
    report = json.loads((tmp_path/'observation-panel.json').read_text())
    assert report['status'] == 'passed'
    assert report['errors'] == []
    assert report['estimated_same_sample']
    assert report['frame']['observation']['estimate']['state'] == 'unknown'
    assert report['frame']['correction_status'] == 'expired'
    for field in ('sampled_at', 'valid_until', 'capabilities', 'application', 'idle_s'):
        assert field in report['raw_text']
    assert 'independent_estimator_evidence' in report['estimate_text'] or 'invalid_estimate' in report['estimate_text']
    assert 'manual correction reason' in report['correction_text']  # expired correction remains inspectable
    assert 'user' in report['correction_text'] or 'correction' in report['correction_text']


def test_live_sampling_failure_reaches_observation_frame_and_recovers(tmp_path):
    """A mocked native host exercises GUI->buffer errors without claiming OS evidence."""
    (tmp_path/'flaky_host.py').write_text('''
from datetime import timedelta
from deskpet.contracts import RawSignals
class FlakyHost:
    def __init__(self, clock): self.clock=clock; self.calls=0
    def sample(self):
        self.calls+=1
        if self.calls==2: raise LookupError('private exception detail must not be logged')
        now=self.clock.wall_now()
        return RawSignals(sample_id='host-'+str(self.calls),sampler_id='test-host',
            sampled_at=now,valid_until=now+timedelta(seconds=30),source='live',
            capabilities={'foreground':True,'idle':True,'native_desktop':False},
            application='Reader',idle_s=15)
def factory(*, clock): return FlakyHost(clock)
''')
    adapters=tmp_path/'adapters.json'
    adapters.write_text(json.dumps({'sampling_source':'flaky_host:factory'}))
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONPATH=str(tmp_path)+os.pathsep+str(ROOT))
    code=r'''
import json,sys
from pathlib import Path
from types import SimpleNamespace
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from deskpet.m0.gui import Workbench
app=QApplication([])
w=Workbench(SimpleNamespace(profile='live-smoke',scenario='buffer-test',output=sys.argv[1],
    auto_smoke=False,adapters=sys.argv[2]))
w.show()
timer=QTimer()
state={'step':0,'ticks':0}
def step():
    state['ticks']+=1
    if state['ticks']>200:
        Path(sys.argv[1],'buffer-test.json').write_text(json.dumps({'timeout':True,'state':state,'errors':w.errors}))
        timer.stop();w.close();return
    frame=w.latest.get('observation_frame',{})
    if state['step']==0 and frame.get('raw_status')=='fresh':
        assert not w.observation_advance_button.isEnabled()
        assert frame['observation']['estimate']['state']=='available'
        if hasattr(w,'_live_timer'):w._live_timer.stop()
        w._sample_live();state['step']=1
    elif state['step']==1 and frame.get('raw_status')=='error':
        assert frame['observation']['estimate']['state']=='unknown'
        assert 'LookupError' in frame['error']
        assert 'private exception detail' not in w.log.toPlainText()
        w._sample_live();state['step']=2
    elif state['step']==2 and frame.get('raw_status')=='fresh':
        assert frame['observation']['estimate']['state']=='available'
        Path(sys.argv[1],'buffer-test.json').write_text(json.dumps({'status':'passed','errors':w.errors,'frame':frame}))
        timer.stop();w.close()
timer.timeout.connect(step)
timer.start(50)
app.exec()
'''
    process=subprocess.run([sys.executable,'-c',code,str(tmp_path),str(adapters)],cwd=ROOT,env=env,
                           capture_output=True,text=True,timeout=20)
    assert process.returncode==0,process.stdout+process.stderr
    result=json.loads((tmp_path/'buffer-test.json').read_text())
    assert result['status']=='passed'
    assert result['frame']['raw']['sample_id']=='host-3'
    assert any('LookupError' in e for e in result['errors'])


def test_live_json_updates_preserve_inspector_scroll_position():
    """Refreshing age/timestamps must not pull a reader back to the JSON top."""
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=str(ROOT))
    code = r'''
from PyQt6.QtWidgets import QApplication, QTextEdit
from deskpet.m0.gui import Workbench
app=QApplication([])
w=QTextEdit()
w.setReadOnly(True)
w.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
w.resize(320,200)
w.show()
data={'age_s':1.0,'signals':{f'field-{i}':'x'*200 for i in range(100)}}
Workbench._render_json(w,data)
for _ in range(5):app.processEvents()
assert w.verticalScrollBar().maximum()>400
assert w.horizontalScrollBar().maximum()>100
w.verticalScrollBar().setValue(400)
w.horizontalScrollBar().setValue(100)
for i in range(3):
    data['age_s']=2.0+i
    Workbench._render_json(w,data)
    for _ in range(5):app.processEvents()
    assert w.verticalScrollBar().value()==400, 'vertical reading position reset'
    assert w.horizontalScrollBar().value()==100, 'horizontal reading position reset'
w.close()
'''
    process = subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,
                             capture_output=True,text=True,timeout=15)
    assert process.returncode==0,process.stdout+process.stderr
