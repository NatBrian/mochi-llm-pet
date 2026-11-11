"""Runnable C-side widget. It knows only SurfaceContext and public UI models.

Load with examples/m0/c-ui.adapters.json; no QApplication, service, clock,
thread, model or OS sampler is created here. C can replace this whole module.
"""
from __future__ import annotations

from uuid import uuid4

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from deskpet.contracts import UiAction


class ReminderView(QFrame):
    def __init__(self, command, context, act, parent=None):
        super().__init__(parent)
        self.command = command
        self.context = context
        self._reported = False
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        title = QLabel(f"提醒：{command.content}")
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(QLabel(f"{command.task_id} · v{command.task_version}"))
        row = QHBoxLayout()
        for title, action, payload in (("已完成", "done", {}), ("10秒后", "snooze", {"seconds": 10}),
                                       ("取消", "cancel", {})):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, a=action, p=payload: act(
                f"reminder.{a}", task_id=command.task_id, version=command.task_version, payload=p))
            row.addWidget(button)
        layout.addLayout(row)
        self._paint_timer = QTimer(self)
        self._paint_timer.setSingleShot(True)
        self._paint_timer.timeout.connect(self._acknowledge)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._reported and self.context.can_report_display and not self._paint_timer.isActive():
            self._paint_timer.start(0)

    def _acknowledge(self):
        if self.isVisible():
            self._reported = self.context.report_display(self.command.delivery_id)


class ExampleSurface(QWidget):
    def __init__(self, *, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.snapshot = {}
        self.cards = {}
        self._session_rows = None
        self.setObjectName("c-example-surface")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("C 界面样例 · 与联调台共用任务、工作段和时钟"))
        self.summary = QLabel("等待服务快照…")
        layout.addWidget(self.summary)
        self.card_layout = QVBoxLayout()
        layout.addLayout(self.card_layout)
        self.goal = QLineEdit("验证 C 的工作段界面")
        layout.addWidget(self.goal)
        self.duration = QSpinBox()
        self.duration.setRange(1, 86400)
        self.duration.setValue(60)
        layout.addWidget(QLabel("工作段时长（秒）"))
        layout.addWidget(self.duration)
        self.session_list = QListWidget()
        layout.addWidget(self.session_list)
        row = QHBoxLayout()
        self.session_buttons = {}
        for action, title in (("start", "开始"), ("pause", "暂停"), ("resume", "继续"), ("end", "结束并提交回顾")):
            button = QPushButton(title)
            button.setObjectName(f"c-session-{action}")
            button.clicked.connect(lambda checked=False, a=action: self._session(a))
            self.session_buttons[action] = button
            row.addWidget(button)
        layout.addLayout(row)
        self.outcome = QLineEdit()
        self.outcome.setPlaceholderText("确认的成果（可留空）")
        layout.addWidget(self.outcome)
        self.next_step = QLineEdit()
        self.next_step.setPlaceholderText("下一步（可留空）")
        layout.addWidget(self.next_step)
        self.review = QLabel("回顾：尚无结束的工作段")
        self.review.setWordWrap(True)
        layout.addWidget(self.review)
        self.result = QLabel("在任务与工作段页创建提醒，到期后会在本页展示。")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        context.result_received.connect(lambda value: self.result.setText(str(value)))

    def _act(self, action_id, *, task_id=None, version=None, payload=None):
        return self.context.submit_action(UiAction(
            event_id=f"c-ui-{uuid4().hex}", action_id=action_id, task_id=task_id,
            expected_task_version=version, surface_revision=max(1, version or 1), payload=payload or {}))

    def _session(self, action):
        current = self.session_list.currentItem()
        session_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        view = next((v for v in self.snapshot.get("sessions", []) if v["session_id"] == session_id), None)
        if action != "start" and view is None:
            self.result.setText("请先选择工作段")
            return
        payload = {"session_id": f"c-session-{uuid4().hex}", "expected_session_version": 0,
                   "goal": self.goal.text(), "duration_s": self.duration.value()} if action == "start" else {
                       "session_id": view["session_id"], "expected_session_version": view["version"]}
        if action == "end":
            payload.update(confirmed_outcomes=[self.outcome.text()] if self.outcome.text().strip() else [],
                           next_steps=[self.next_step.text()] if self.next_step.text().strip() else [])
        self._act(f"session.{action}", payload=payload)

    def render_snapshot(self, snapshot):
        self.snapshot = snapshot
        state = snapshot["observation"]["estimate"]["state"]
        self.summary.setText(f'{snapshot["now"]} · {snapshot["clock"]} · 状态 {state} · {snapshot["event_count"]} 个事件')
        sessions = snapshot["sessions"]
        rows = [(v["session_id"], v["version"], v["state"], round(v["remaining_s"], 1)) for v in sessions]
        if rows != self._session_rows:
            selected = self.session_list.currentItem()
            selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
            self.session_list.clear()
            for view in sessions:
                item = QListWidgetItem(f'{view["goal"]} · {view["state"]} · {view["remaining_s"]:.1f}s · v{view["version"]}')
                item.setData(Qt.ItemDataRole.UserRole, view["session_id"])
                self.session_list.addItem(item)
                if view["session_id"] == selected_id:
                    self.session_list.setCurrentItem(item)
            if self.session_list.currentRow() < 0 and self.session_list.count():
                self.session_list.setCurrentRow(self.session_list.count()-1)
            self._session_rows = rows
        ended = [v for v in sessions if v["state"] == "ended"]
        if ended:
            latest = ended[-1]
            self.review.setText("确认成果：" + "；".join(latest["confirmed_outcomes"]) + "\n下一步：" + "；".join(latest["next_steps"]))

    def show_reminder(self, command):
        if command.delivery_id in self.cards:
            return
        card = ReminderView(command, self.context, self._act, self)
        self.cards[command.delivery_id] = card
        self.card_layout.addWidget(card)
        card.show()

    def dismiss_reminder(self, task_id):
        for key, card in list(self.cards.items()):
            if card.command.task_id == task_id:
                self.card_layout.removeWidget(card)
                card.hide()
                card.deleteLater()
                del self.cards[key]


def create_surface(*, context, parent):
    return ExampleSurface(context=context, parent=parent)
