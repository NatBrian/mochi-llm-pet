"""C widgets exercise the real queued gateway, not direct domain calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def _run(code, tmp_path, *arguments):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=str(tmp_path)+os.pathsep+str(ROOT))
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), *map(str, arguments)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout+result.stderr


def test_embedded_ui_and_replacement_sessions_share_state_actions_and_receipts(tmp_path):
    (tmp_path/"c_backend.py").write_text('''
from deskpet.mocks.work_session import MemoryWorkSessionService
def factory(*, clock, emit):
    emit('c.adapter.loaded', source='test', simulated=True)
    return MemoryWorkSessionService(clock=clock, emit=emit)
''')
    adapters = tmp_path/"adapters.json"
    adapters.write_text(json.dumps({"ui_surface":"deskpet.m0.example_surface:create_surface",
                                    "work_sessions":"c_backend:factory"}))
    code = r'''
import json, sys
from pathlib import Path
from types import SimpleNamespace
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QPushButton
from deskpet.contracts import UiAction
from deskpet.m0.gui import Workbench
app = QApplication([])
w = Workbench(SimpleNamespace(profile='scenario', scenario='c-embedded', output=sys.argv[1],
              auto_smoke=False, adapters=sys.argv[2]))
w.show()
progress = {'step':0, 'ticks':0, 'session':None, 'delivery':None}
timer = QTimer()
def step():
    progress['ticks'] += 1
    assert progress['ticks'] < 300, (progress, w.errors, w.latest)
    p = w.ui_surface
    sessions = list(w.session_views.values())
    view = sessions[0] if sessions else None
    n = progress['step']
    if n == 0 and w.ready_for_actions:
        assert w.tabs.count() == 3
        assert 'Observation' in w.tabs.tabText(1)
        assert w.tabs.widget(2) is p
        before = w.latest['observation']['context_revision']
        copy = p.context.snapshot
        copy['observation']['context_revision'] = 999999
        assert w.latest['observation']['context_revision'] == before
        assert not p.context.report_display('unknown-delivery')
        p.session_buttons['start'].click()
        progress['step'] = 1
    elif n == 1 and view and view['state'] == 'focusing' and p.snapshot['sessions']:
        progress['session'] = view['session_id']
        assert p.snapshot['sessions'][0] == view
        assert view['remaining_s'] == 60
        p.session_buttons['pause'].click()
        progress['step'] = 2
    elif n == 2 and view['state'] == 'paused' and p.snapshot['sessions'][0]['state'] == 'paused':
        assert 'paused' in w.session_list.item(0).text()
        w.advance_button.click()
        progress['step'] = 3
    elif n == 3 and w.latest['now'].endswith('10+00:00'):
        assert view['remaining_s'] == 60
        assert p.snapshot['sessions'][0]['remaining_s'] == 60
        p.session_buttons['resume'].click()
        progress['step'] = 4
    elif n == 4 and view['state'] == 'focusing' and view['version'] == 3:
        w.action.emit({'kind':'advance', 'seconds':5})
        progress['step'] = 5
    elif n == 5 and view['remaining_s'] == 55:
        p.outcome.setText('来自 C 界面的用户确认成果')
        p.next_step.setText('替换动态 renderer')
        p.session_buttons['end'].click()
        progress['step'] = 6
    elif n == 6 and view['state'] == 'ended' and p.snapshot['sessions'][0]['state'] == 'ended':
        assert view['confirmed_outcomes'] == ['来自 C 界面的用户确认成果']
        assert '来自 C 界面' in w.review.text() and '来自 C 界面' in p.review.text()
        p.context.submit_action(UiAction(event_id='c-stale-session', action_id='session.pause', surface_revision=1,
            payload={'session_id':progress['session'], 'expected_session_version':1}))
        progress['step'] = 7
    elif n == 7 and any(r.get('command_id') == 'c-stale-session' and r.get('status') == 'rejected' for r in w.results):
        assert w.session_views[progress['session']]['state'] == 'ended'
        assert 'rejected' in p.result.text()
        w.mode.setCurrentText('hard'); w.delay.setValue(0)
        w.create_button.click()
        progress['step'] = 8
    elif n == 8 and 'gui-1' in w.views:
        w.confirm_button.click()
        progress['step'] = 9
    elif n == 9 and w.views['gui-1']['displayed_at']:
        assert not w.cards, 'default renderer also received the official reminder'
        assert len(p.cards) == 1
        cmd = next(iter(p.cards.values())).command
        progress['delivery'] = cmd.delivery_id
        assert w.receipts[cmd.delivery_id].source == 'offscreen'
        assert not p.context.report_display(cmd.delivery_id), 'duplicate success was accepted'
        w._grab_evidence(Path(sys.argv[1], 'c-ui-surface.png'))
        done = next(b for b in next(iter(p.cards.values())).findChildren(QPushButton) if b.text() == '已完成')
        done.click()
        progress['step'] = 10
    elif n == 10 and w.views['gui-1']['status'] == 'completed' and not p.cards:
        assert not w._surface_commands
        p.context.submit_action(UiAction(event_id='c-stale-task', action_id='reminder.done', task_id='gui-1',
            expected_task_version=2, surface_revision=2))
        progress['step'] = 11
    elif n == 11 and any(r.get('event_id') == 'c-stale-task' and r.get('error_code') == 'STALE_SURFACE' for r in w.results):
        assert not p.context.report_display(progress['delivery']), 'dismissed receipt was accepted'
        assert not w.errors, w.errors
        timer.stop()
        w.close()
        try:
            p.context.submit_action(UiAction(event_id='closed', action_id='session.start', surface_revision=1))
        except RuntimeError: pass
        else: raise AssertionError('closed context accepted an action')
        Path(sys.argv[1], 'embedded.json').write_text(json.dumps({'status':'passed', 'checks':progress,
            'session':view, 'task':w.views['gui-1'], 'tabs':w.tabs.count(), 'receipts':len(w.receipts)}))
timer.timeout.connect(step)
timer.start(30)
app.exec()
'''
    _run(code, tmp_path, adapters)
    report = json.loads((tmp_path/"embedded.json").read_text())
    assert report["status"] == "passed"
    assert report["session"]["remaining_s"] == 55
    assert report["receipts"] == 1
    events = [json.loads(line) for line in (tmp_path/"trace.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "c.adapter.loaded" for e in events)
    shown = [e for e in events if e["kind"] == "delivery.displayed"]
    assert len(shown) == 1 and shown[0]["source"] == "offscreen"
    assert (tmp_path/"c-ui-surface.png").stat().st_size > 1000


def test_surface_report_guards_and_plugin_failure_are_explicit(tmp_path):
    code = r'''
import sys, time
from datetime import datetime, timedelta
from types import SimpleNamespace
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication
from deskpet.contracts import DisplayCommand, ErrorCode
from deskpet.m0.gui import Workbench
app = QApplication([])
w = Workbench(SimpleNamespace(profile='scenario', scenario='guards', output=sys.argv[1],
    auto_smoke=False, adapters={'ui_surface':'deskpet.m0.example_surface:create_surface'}))
w.show()
deadline = time.monotonic()+5
while not w.ready_for_actions and time.monotonic() < deadline:
    app.processEvents()
assert w.ready_for_actions
now = datetime.fromisoformat(w.latest['now'])
command = DisplayCommand(delivery_id='guard', task_id='guard-task', task_version=2, occurrence=1,
    content='guarded', requested_at=now, expires_at=now+timedelta(seconds=30), reason='hard_due')
w._surface_commands[command.delivery_id] = command
context = w.surface_context
w.tabs.setCurrentIndex(0)
assert not context.report_display('guard'), 'hidden widget created receipt'
w.tabs.setCurrentWidget(w.ui_surface)
w._capturing = True
assert not context.report_display('guard'), 'evidence capture created receipt'
w._capturing = False
try: context.report_display('guard', error_code=ErrorCode.FAILED)
except ValueError: pass
else: raise AssertionError('success with an error accepted')
w._surface_commands['guard'] = command.model_copy(update={'expires_at':now})
assert not context.report_display('guard'), 'expiry boundary accepted'
assert w.receipts['guard'].outcome == 'rejected'
def broken(command): raise ValueError('C widget failed')
w.ui_surface.show_reminder = broken
w._display(command.model_copy(update={'delivery_id':'broken'}))
assert w._surface_failed
assert not w.ui_surface.isEnabled(), 'failed widget remained interactive'
assert w.receipts['broken'].outcome == 'failed'
assert not w.cards, 'host silently fell back to default cards'
assert any('C widget failed' in e for e in w.errors)
w.close()
w2 = Workbench(SimpleNamespace(profile='scenario', scenario='destroyed', output=sys.argv[1],
    auto_smoke=False, adapters={'ui_surface':'deskpet.m0.example_surface:create_surface'}))
w2.show()
context2 = w2.surface_context
w2.ui_surface.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
assert w2.ui_surface is None
assert w2._surface_failed
assert not context2.can_report_display
w2._snapshot(w.latest)  # a late queued snapshot cannot call a deleted QWidget
w2.close()
'''
    _run(code, tmp_path)


def test_headless_replay_does_not_import_embedded_widget():
    code = r'''
import sys
from deskpet.m0.replay import run_suite
r = run_suite(adapters={'ui_surface':'does_not_exist:factory'})
assert r['status'] == 'passed'
assert not any(name.startswith('PyQt6') for name in sys.modules)
'''
    process = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert process.returncode == 0, process.stdout+process.stderr


def test_whole_gui_and_embedded_ui_are_not_ambiguous():
    from deskpet.m0.composition import resolve_gui
    import pytest
    with pytest.raises(ValueError, match="do not configure both"):
        resolve_gui({"gui":"missing:entry", "ui_surface":"missing:widget"})
