"""Qt workbench with an optional embedded C surface. No model client is started.

Domain adapters are constructed in ServiceWorker.start, on their owning thread.
Widgets only exchange immutable views/actions/receipts through queued signals.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPushButton,
    QScrollArea, QSpinBox, QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from deskpet.contracts import (
    DecisionEnvelope, DisplayCommand, DisplayReceipt, ErrorCode, RawSignals, UserCorrection,
    ProposedPlan, SessionCommand, TaskCommand, TaskDraft, ToolCall, UiAction,
)


def _id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class QtDeliveryBridge(QObject):
    """Delivery port: queued dispatch is pending until a renderer receipt arrives."""
    requested = pyqtSignal(object)
    dismissed = pyqtSignal(str)

    def show(self, command: DisplayCommand) -> None:
        self.requested.emit(command)
        return None

    def dismiss(self, task_id: str) -> None:
        self.dismissed.emit(task_id)


class ServiceWorker(QObject):
    ready = pyqtSignal()
    snapshot_ready = pyqtSignal(object)
    result_ready = pyqtSignal(object)
    error = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, args, delivery):
        super().__init__()
        self.args = args
        self.delivery = delivery
        self.env = None
        self.timer = None
        self._saved_envelope = None
        self._observation = None
        self._qt_source = "offscreen" if QGuiApplication.platformName() in ("offscreen", "minimal") else "native"

    @pyqtSlot()
    def start(self):
        try:
            from .composition import build_environment
            self.env = build_environment(
                profile=self.args.profile, scenario_id=self.args.scenario,
                output=self.args.output, delivery=self.delivery, adapters=getattr(self.args, "adapters", None),
                external_sampling=self.args.profile == "live-smoke",
            )
            self._observation = self.env.observations.snapshot().observation
            self.timer = QTimer(self)
            self.timer.setInterval(100)
            self.timer.timeout.connect(self.pump)
            self.timer.start()
            self.snapshot()
            self.ready.emit()
        except Exception as exc:
            self.error.emit(f"初始化失败：{type(exc).__name__}: {exc}")

    def snapshot(self):
        if self.env is None:
            return
        sessions = self.env.sessions.list_sessions()
        frame = self.env.observations.snapshot()
        self._observation = frame.observation
        self.snapshot_ready.emit({
            "now": self.env.clock.wall_now().isoformat(),
            "clock": getattr(self.env.clock, "kind", "wall"),
            "tasks": [v.model_dump(mode="json") for v in self.env.tasks.list_tasks()],
            "sessions": [v.model_dump(mode="json") for v in sessions],
            "observation": frame.observation.model_dump(mode="json"),
            "observation_frame": frame.model_dump(mode="json"),
            "sampling_editable": self.args.profile == "scenario" and hasattr(self.env.sampling, "set_signals"),
            "estimator_editable": hasattr(self.env.estimator, "set_estimate"),
            "event_count": len(self.env.trace.events),
        })

    @pyqtSlot()
    def pump(self):
        if self.env is None:
            return
        try:
            self.env.pump()
            self.snapshot()
        except Exception as exc:
            self.error.emit(f"pump: {type(exc).__name__}: {exc}")

    @pyqtSlot(object)
    def act(self, action):
        if self.env is None:
            return
        try:
            if action.get("kind") == "ui":
                ui = UiAction.model_validate(action["action"])
                self.env.trace.emit("ui.action", task_id=ui.task_id,
                                    task_version=ui.expected_task_version,
                                    source=self._qt_source, simulated=self._qt_source != "native",
                                    payload={"event_id": ui.event_id, "action_id": ui.action_id,
                                             "surface_revision": ui.surface_revision,
                                             "automation": bool(getattr(self.args, "auto_smoke", False))})
                prefix, verb = ui.action_id.split(".", 1)
                if prefix == "reminder":
                    task = self.env.tasks.get(ui.task_id) if ui.task_id else None
                    if verb != "create" and task and ui.surface_revision != task.version:
                        self.env.trace.emit("ui.rejected", task_id=ui.task_id,
                                            task_version=task.version, source=self._qt_source,
                                            simulated=self._qt_source != "native",
                                            payload={"event_id": ui.event_id, "error_code": "STALE_SURFACE"})
                        self.result_ready.emit({"status": "rejected", "error_code": "STALE_SURFACE",
                                                "event_id": ui.event_id})
                        return
                    action = {"kind": "task", "command": "complete" if verb == "done" else verb,
                              "task_id": ui.task_id, "version": ui.expected_task_version or 0,
                              "event_id": ui.event_id, "payload": dict(ui.payload)}
                elif prefix == "state":
                    action = ({"kind": "clear_override"} if verb == "clear_override" else
                              {"kind": "state", "state": ui.payload["state"],
                               "reason": ui.payload.get("reason", "用户确认当前状态"),
                               "ttl_s": ui.payload.get("ttl_s", 30)})
                elif prefix == "session":
                    payload = dict(ui.payload)
                    action = {"kind": "session", "command": verb,
                              "session_id": payload.pop("session_id"),
                              "version": payload.pop("expected_session_version", 0),
                              "event_id": ui.event_id, "payload": payload}
            kind = action["kind"]
            result = None
            if kind == "task":
                cmd_kind = action["command"]
                payload = dict(action.get("payload", {}))
                if cmd_kind in ("create", "edit"):
                    delay = float(payload.pop("delay_s", 10))
                    window = float(payload.pop("window_s", 20))
                    mode = payload.pop("mode", "soft")
                    start = self.env.clock.wall_now() + timedelta(seconds=delay)
                    draft = TaskDraft(content=payload.pop("content"), mode=mode,
                                      earliest_at=start,
                                      latest_first_delivery_at=start + timedelta(
                                          seconds=0 if mode == "hard" else window))
                    payload = {"draft": draft.model_dump(mode="json")}
                result = self.env.tasks.apply(TaskCommand(
                    command_id=action.get("event_id") or _id("command"), kind=cmd_kind,
                    task_id=action["task_id"], expected_version=action.get("version", 0),
                    payload=payload))
            elif kind == "advance":
                if not hasattr(self.env.clock, "advance"):
                    raise ValueError("真实时钟不支持快进")
                self.env.clock.advance(float(action.get("seconds", 10)))
            elif kind == "state":
                ttl = float(action.get("ttl_s", 30))
                if not 0 < ttl <= 86400:
                    raise ValueError("纠正有效期必须在 0 到 86400 秒之间")
                now = self.env.clock.wall_now()
                self.env.observations.apply_correction(UserCorrection(
                    correction_id=_id("correction"), state=action["state"],
                    reason=action.get("reason", "用户确认当前状态"),
                    origin="automation" if getattr(self.args, "auto_smoke", False) else "user", created_at=now,
                    valid_until=now + timedelta(seconds=ttl)))
                result = {"status": "correction_applied"}
            elif kind == "clear_override":
                self.env.observations.clear_override()
                result = {"status": "correction_cleared"}
            elif kind == "raw_sample":
                raw = RawSignals.model_validate(action["sample"])
                self.env.observations.submit_sample(raw, sampling_name=action.get("sampling_name"))
            elif kind == "sampling_error":
                self.env.observations.submit_sampling_error(action["error_code"], sampling_name=action.get("sampling_name"))
                self.pump()
                self.error.emit(f"真实采样失败：{action['error_code']}")
                return
            elif kind == "set_signals":
                if self.args.profile != "scenario":
                    raise ValueError("真实采样模式不能注入模拟原始信号")
                self.env.observations.set_signals(**action["signals"])
                result = {"status": "sample_submitted"}
            elif kind == "refresh_observation":
                result = {"status": "sample_refreshed"}
            elif kind == "set_estimate":
                setter = getattr(self.env.estimator, "set_estimate", None)
                if not callable(setter):
                    raise ValueError("当前估计器为只读；请替换为可控估计器后调试")
                setter(action["state"], evidence=action.get("evidence"), ttl_s=action.get("ttl_s", 30))
                result = {"status": "estimate_submitted"}
            elif kind == "session":
                result = self.env.sessions.apply(SessionCommand(
                    command_id=action.get("event_id") or _id("session-command"), kind=action["command"],
                    session_id=action["session_id"], expected_version=action.get("version", 0),
                    payload=action.get("payload", {})))
            elif kind == "save-decision":
                task = self.env.tasks.get(action["task_id"])
                if task is None:
                    raise ValueError("请选择任务")
                now = self.env.clock.wall_now()
                self._saved_envelope = DecisionEnvelope(
                    request_id=_id("late-request"), task_id=task.task_id,
                    task_version=task.version,
                    context_revision=self._observation.context_revision,
                    created_at=now, expires_at=now + timedelta(seconds=300))
                result = {"status": "saved", "task_version": task.version}
            elif kind in ("late-decision", "timeout"):
                task = self.env.tasks.get(action["task_id"])
                if task is None:
                    raise ValueError("请选择任务")
                now = self.env.clock.wall_now()
                envelope = self._saved_envelope if kind == "late-decision" else None
                if envelope is None:
                    envelope = DecisionEnvelope(
                        request_id=_id("request"), task_id=task.task_id,
                        task_version=task.version,
                        context_revision=self._observation.context_revision,
                        created_at=now, expires_at=now + timedelta(seconds=30))
                if kind == "late-decision":
                    error = self.env.tasks.accept_decision(ProposedPlan(envelope=envelope))
                    result = {"status": "rejected" if error else "accepted",
                              "error_code": error.value if error else None}
                else:
                    self.env.runtime.faults["show_reminder"] = "timeout"
                    result = self.env.runtime.execute(ToolCall(
                        call_id=_id("call"), tool_name="show_reminder", envelope=envelope,
                        idempotency_key=_id("timeout")))
                    self.env.runtime.faults.pop("show_reminder", None)
            elif kind == "receipt":
                receipt = DisplayReceipt.model_validate(action["receipt"])
                error = self.env.tasks.record_receipt(receipt)
                result = {"status": "receipt_rejected" if error else "receipt_recorded",
                          "error_code": error.value if error else None,
                          "delivery_id": receipt.delivery_id}
            else:
                raise ValueError(f"未知动作 {kind}")
            if result is not None:
                value = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
                self.result_ready.emit(value)
            self.pump()
        except Exception as exc:
            self.error.emit(f"操作失败：{type(exc).__name__}: {exc}")

    @pyqtSlot()
    def stop(self):
        if self.timer:
            self.timer.stop()
        self.stopped.emit()
        QThread.currentThread().quit()


class ReminderCard(QFrame):
    painted = pyqtSignal(object)

    def __init__(self, command, buttons, parent=None):
        super().__init__(parent)
        self.command = command
        self._reported = False
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "ReminderCard { background: #fff5d8; border: 2px solid #ce8c22; border-radius: 8px; }"
            "ReminderCard QLabel { color: #3b2c13; background: transparent; }"
            "ReminderCard QPushButton { color: #30240f; background: #fffdf7; border: 1px solid #bd9654; padding: 6px; border-radius: 4px; }"
        )
        layout = QVBoxLayout(self)
        title = QLabel(f"提醒 · {command.content}")
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(QLabel(f"{command.task_id} · v{command.task_version} · {command.reason}"))
        row = QHBoxLayout()
        for text, callback in buttons:
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)

    def paintEvent(self, event):
        super().paintEvent(event)
        window = self.window()
        evidence_paint = getattr(window, "_capturing", False) or getattr(window, "_finishing", False)
        if not evidence_paint and not self._reported and self.isVisible():
            self._reported = True
            # Parent ownership cancels the callback when a card is deleted.
            self._receipt_timer = QTimer(self)
            self._receipt_timer.setSingleShot(True)
            self._receipt_timer.timeout.connect(self._emit_painted)
            self._receipt_timer.start(0)

    def _emit_painted(self):
        if self.isVisible():
            self.painted.emit(self.command)


class Workbench(QMainWindow):
    action = pyqtSignal(object)
    stop_requested = pyqtSignal()

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.setWindowTitle("Mochi M0 · 协作联调台")
        self.resize(1180, 900)
        self.views = {}
        self.session_views = {}
        self.latest = {}
        self.cards = {}
        self.receipts = {}
        self.pending_cancels = set()
        self._cancel_commands = {}
        self.results = []
        self.errors = []
        self.ready_for_actions = False
        self._next_task = 1
        self._select_task = None
        self._last_task_rows = None
        self._last_session_rows = None
        self._closing = False
        self.report = None
        self._finishing = False
        self._capturing = False
        self._activation_requested = False
        self._activation_error = None
        self._native_active_at_start = None
        self._native_exposed_at_start = None
        self._smoke_start_wait = 0
        self._smoke_steps = []
        self._smoke_index = 0
        self._smoke_wait = 0
        self._smoke_checks = {}
        self._capabilities = None
        self._observation_checkpoints = {}
        self.ui_surface = None
        self.surface_context = None
        self._surface_configured = False
        self._surface_spec = None
        self._surface_failed = False
        self._surface_commands = {}
        self._surface_setup_error = None
        self._build_ui()
        self._mount_surface()
        self.delivery = QtDeliveryBridge(self)
        self.thread = QThread(self)
        self.worker = ServiceWorker(args, self.delivery)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.action.connect(self.worker.act, Qt.ConnectionType.QueuedConnection)
        self.stop_requested.connect(self.worker.stop, Qt.ConnectionType.QueuedConnection)
        self.worker.snapshot_ready.connect(self._snapshot)
        self.worker.result_ready.connect(self._result)
        self.worker.error.connect(self._error)
        self.worker.ready.connect(self._ready)
        self.delivery.requested.connect(self._display, Qt.ConnectionType.QueuedConnection)
        self.delivery.dismissed.connect(self._dismiss, Qt.ConnectionType.QueuedConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()
        if self._surface_setup_error:
            QTimer.singleShot(0, lambda: self._error(self._surface_setup_error))
        if getattr(args, "auto_smoke", False):
            QTimer.singleShot(15000, self._startup_timeout)

    def _button(self, text, callback, name=None):
        button = QPushButton(text)
        if name:
            button.setObjectName(name)
        button.clicked.connect(callback)
        return button

    def _mount_surface(self):
        """Only GUI commands import C's widget; the standard diagnostic tabs stay."""
        from .composition import load_adapters, resolve_ui_surface
        from .ui_surface import SurfaceContext
        try:
            configured = load_adapters(getattr(self.args, "adapters", None))
            self._surface_configured = "ui_surface" in configured
            self._surface_spec = configured.get("ui_surface")
            factory = resolve_ui_surface(configured)
            if factory is None:
                return
            self.surface_context = SurfaceContext(
                submit=self._submit_surface_action, report=self._surface_receipt,
                can_report=self._surface_can_report, parent=self)
            widget = factory(context=self.surface_context, parent=self.tabs)
            if not isinstance(widget, QWidget) or widget.thread() != self.thread():
                raise TypeError("ui_surface factory must return a QWidget on the GUI thread")
            for method in ("render_snapshot", "show_reminder", "dismiss_reminder"):
                if not callable(getattr(widget, method, None)):
                    raise TypeError(f"ui_surface is missing {method}")
            self.ui_surface = widget
            widget.destroyed.connect(self._surface_destroyed)
            self.tabs.addTab(widget, "C 界面 · 实际展示")
            self.tabs.setCurrentWidget(widget)
        except Exception as exc:
            self._surface_configured = self._surface_failed = True
            self._surface_setup_error = f"C 界面装配失败：{type(exc).__name__}: {exc}"
            self.tabs.addTab(QLabel(self._surface_setup_error), "C 界面 · 失败")

    def _submit_surface_action(self, action):
        if not self.ready_for_actions or self._closing or self._finishing or self._surface_failed:
            raise RuntimeError("UI surface is not accepting actions")
        self.action.emit({"kind": "ui", "action": action.model_dump(mode="json")})

    def _surface_destroyed(self):
        self.ui_surface = None
        if self.surface_context is not None:
            self.surface_context._close()
        if not (self._closing or self._finishing or self._surface_failed):
            self._fail_surface(RuntimeError("C 界面组件已销毁"))

    def _surface_can_report(self):
        return bool(self.ui_surface is not None and self.ui_surface.isVisible()
                    and not (self._capturing or self._finishing or self._closing or self._surface_failed))

    def _surface_receipt(self, delivery_id, outcome, error):
        command = self._surface_commands.get(delivery_id)
        if command is None or self._capturing or self._finishing or self._closing:
            return False
        if outcome == "displayed" and not self._surface_can_report():
            return False
        cached = self.receipts.get(delivery_id)
        if cached and cached.outcome == "displayed":
            return False
        view = self.views.get(command.task_id)
        if view and (view["version"] != command.task_version or view["status"] != "registered"):
            return False
        if self.latest and command.expires_at <= datetime.fromisoformat(self.latest["now"]):
            self._receipt(command, "rejected", ErrorCode.EXPIRED_REQUEST)
            self._surface_commands.pop(delivery_id, None)
            return False
        self._receipt(command, outcome, error)
        if outcome != "displayed":
            self._surface_commands.pop(delivery_id, None)
        return True

    def _fail_surface(self, exc):
        self._surface_failed = True
        if self.ui_surface is not None:
            self.ui_surface.setEnabled(False)
        if self.surface_context is not None:
            self.surface_context._close()
        for command in list(self._surface_commands.values()):
            cached = self.receipts.get(command.delivery_id)
            if cached is None or cached.outcome != "displayed":
                self._receipt(command, "failed", ErrorCode.FAILED)
        self._surface_commands.clear()
        self._error(f"C 界面运行失败：{type(exc).__name__}: {exc}")

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        self.status = QLabel("正在装配服务线程…")
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        split = QSplitter()
        self.tabs.addTab(split, "任务与工作段")
        left, right = QWidget(), QWidget()
        split.addWidget(left)
        split.addWidget(right)
        left_l, right_l = QVBoxLayout(left), QVBoxLayout(right)
        form_group = QGroupBox("结构化提醒（UTC；先创建草稿，再确认）")
        form = QFormLayout(form_group)
        self.content = QLineEdit("买牛奶")
        self.content.setObjectName("content")
        self.mode = QComboBox()
        self.mode.addItems(["soft", "hard"])
        self.delay = QSpinBox()
        self.delay.setRange(0, 86400)
        self.delay.setValue(10)
        self.window_s = QSpinBox()
        self.window_s.setRange(0, 3600)
        self.window_s.setValue(20)
        form.addRow("事项", self.content)
        form.addRow("模式", self.mode)
        form.addRow("最早提醒：当前时间＋秒", self.delay)
        form.addRow("软提醒窗口宽度（秒）", self.window_s)
        self.create_button = self._button("创建草稿", self._create, "create")
        form.addRow(self.create_button)
        left_l.addWidget(form_group)
        self.task_list = QListWidget()
        self.task_list.setObjectName("tasks")
        left_l.addWidget(self.task_list, 1)
        task_row = QHBoxLayout()
        self.confirm_button = self._button("确认", lambda: self._task("confirm"), "confirm")
        self.edit_button = self._button("按上方字段修改", lambda: self._task("edit", self._draft_payload()), "edit")
        self.cancel_button = self._button("取消", lambda: self._task("cancel"), "cancel")
        for b in [self.confirm_button, self.edit_button, self.cancel_button]:
            task_row.addWidget(b)
        left_l.addLayout(task_row)
        controls = QGroupBox("观察、时间与故障")
        controls_l = QFormLayout(controls)
        controls_l.addRow(QLabel("完整采样、状态证据和用户纠正见 Observation 页"))
        self.advance_button = self._button("快进 10 秒", lambda: self.action.emit({"kind": "advance", "seconds": 10}), "advance")
        controls_l.addRow(self.advance_button)
        self.delivery_fault = QComboBox()
        self.delivery_fault.addItems(["正常展示", "渲染失败", "缺失回执"])
        controls_l.addRow("展示出口", self.delivery_fault)
        row = QHBoxLayout()
        row.addWidget(self._button("保存当前决策版本", lambda: self._inject("save-decision")))
        row.addWidget(self._button("注入旧决策", lambda: self._inject("late-decision")))
        row.addWidget(self._button("注入模型超时", lambda: self._inject("timeout")))
        controls_l.addRow(row)
        left_l.addWidget(controls)
        right_l.addWidget(QLabel("提醒卡：实际绘制后发回执；展示不等于完成"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.card_area = QWidget()
        self.card_layout = QVBoxLayout(self.card_area)
        self.card_layout.addStretch()
        scroll.setWidget(self.card_area)
        right_l.addWidget(scroll, 1)
        session = QGroupBox("工作段 mock（共用任务时钟）")
        session_l = QVBoxLayout(session)
        self.goal = QLineEdit("完成 M0 接口联调")
        session_l.addWidget(self.goal)
        self.session_list = QListWidget()
        self.session_list.setMaximumHeight(110)
        session_l.addWidget(self.session_list)
        row = QHBoxLayout()
        self.session_buttons = {}
        for kind, label in [("start", "开始60秒"), ("pause", "暂停"), ("resume", "继续"), ("end", "结束/记录成果")]:
            b = self._button(label, lambda checked=False, k=kind: self._session(k), f"session-{kind}")
            self.session_buttons[kind] = b
            row.addWidget(b)
        session_l.addLayout(row)
        self.review = QLabel("回顾：尚无用户确认成果")
        self.review.setWordWrap(True)
        session_l.addWidget(self.review)
        right_l.addWidget(session)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(145)
        layout.addWidget(self.log)
        self._build_observation_ui()

    def _build_observation_ui(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        self.observation_scroll = QScrollArea()
        self.observation_scroll.setWidgetResizable(True)
        self.observation_scroll.setWidget(page)
        self.tabs.addTab(self.observation_scroll, "Observation · 采样 / 估计 / 纠正")
        self.observation_status = QLabel("等待采样与估计器…")
        self.observation_status.setWordWrap(True)
        page_layout.addWidget(self.observation_status)
        self.observation_advance_button = self._button(
            "虚拟时间＋10秒", lambda: self.action.emit({"kind": "advance", "seconds": 10}), "observation-advance")
        self.observation_advance_button.setEnabled(self.args.profile == "scenario")
        page_layout.addWidget(self.observation_advance_button)
        controls = QHBoxLayout()
        page_layout.addLayout(controls)
        self.raw_controls = QGroupBox("原始信号输入（scenario 可控源）")
        raw_form = QFormLayout(self.raw_controls)
        self.raw_application = QLineEdit("Editor")
        self.raw_application.setObjectName("raw-application")
        self.raw_idle = QDoubleSpinBox()
        self.raw_idle.setRange(0, 86400)
        self.raw_idle.setValue(15)
        self.raw_input = QComboBox()
        self.raw_input.addItems(["unknown", "true", "false"])
        self.raw_foreground_cap = QCheckBox("前台应用信号可用")
        self.raw_foreground_cap.setChecked(True)
        self.raw_idle_cap = QCheckBox("空闲时间信号可用")
        self.raw_idle_cap.setChecked(True)
        self.raw_ttl = QSpinBox()
        self.raw_ttl.setRange(1, 3600)
        self.raw_ttl.setValue(30)
        raw_form.addRow("应用进程", self.raw_application)
        raw_form.addRow("idle 秒", self.raw_idle)
        raw_form.addRow("输入活跃（聚合值）", self.raw_input)
        raw_form.addRow(self.raw_foreground_cap)
        raw_form.addRow(self.raw_idle_cap)
        raw_form.addRow("样本有效期（秒）", self.raw_ttl)
        self.raw_submit = self._button("提交模拟原始采样", self._submit_raw, "raw-submit")
        raw_form.addRow(self.raw_submit)
        self.raw_refresh = self._button("重新读取采样 / 估计", lambda: self.action.emit({"kind": "refresh_observation"}), "raw-refresh")
        raw_form.addRow(self.raw_refresh)
        controls.addWidget(self.raw_controls)
        correction_group = QGroupBox("用户纠正（独立于原始事实）")
        correction_form = QFormLayout(correction_group)
        self.state = QComboBox()
        self.state.addItems(["unknown", "working", "available", "away"])
        self.state.setObjectName("correction-state")
        self.correction_reason = QLineEdit("用户确认当前状态")
        self.correction_reason.setObjectName("correction-reason")
        self.correction_ttl = QSpinBox()
        self.correction_ttl.setRange(1, 86400)
        self.correction_ttl.setValue(30)
        self.correction_apply = self._button("应用用户纠正", self._apply_correction, "correction-apply")
        self.correction_clear = self._button("清除纠正，恢复估计", lambda: self._ui("state.clear_override"), "correction-clear")
        correction_form.addRow("纠正状态", self.state)
        correction_form.addRow("原因", self.correction_reason)
        correction_form.addRow("有效期（秒）", self.correction_ttl)
        correction_form.addRow(self.correction_apply)
        correction_form.addRow(self.correction_clear)
        self.correction_status = QLabel("无生效纠正")
        self.correction_status.setWordWrap(True)
        correction_form.addRow(self.correction_status)
        controls.addWidget(correction_group)
        self.estimator_controls = QGroupBox("估计器调试（仅可控替身）")
        estimator_form = QFormLayout(self.estimator_controls)
        self.estimator_state = QComboBox()
        self.estimator_state.addItems(["unknown", "working", "available", "away"])
        self.estimator_evidence = QLineEdit("scripted_estimator")
        self.estimator_ttl = QSpinBox()
        self.estimator_ttl.setRange(1, 3600)
        self.estimator_ttl.setValue(30)
        self.estimator_apply = self._button("提交模拟估计结果", self._submit_estimate, "estimate-submit")
        estimator_form.addRow("估计状态", self.estimator_state)
        estimator_form.addRow("证据引用（逗号分隔）", self.estimator_evidence)
        estimator_form.addRow("估计有效期（秒）", self.estimator_ttl)
        estimator_form.addRow(self.estimator_apply)
        explanation = QLabel("默认规则估计器只读。配置独立的可控估计器后启用，提交不会修改原始信号。")
        explanation.setWordWrap(True)
        estimator_form.addRow(explanation)
        controls.addWidget(self.estimator_controls)
        panels = QSplitter()
        page_layout.addWidget(panels, 1)
        for name, title in [("raw_view", "原始采样：时间 / 能力 / 字段"),
                            ("estimate_view", "估计器：状态 / 证据 / 有效期"),
                            ("correction_view", "有效 Observation 与用户纠正")]:
            box = QGroupBox(title)
            box_layout = QVBoxLayout(box)
            summary = QLabel("等待数据…")
            summary.setWordWrap(True)
            setattr(self, name + "_summary", summary)
            box_layout.addWidget(summary)
            view = QTextEdit()
            view.setReadOnly(True)
            view.setObjectName(name)
            view.setMinimumHeight(200)
            setattr(self, name, view)
            box_layout.addWidget(view)
            panels.addWidget(box)

    def _submit_raw(self):
        input_text = self.raw_input.currentText()
        self.action.emit({"kind": "set_signals", "signals": {
            "application": self.raw_application.text().strip() or None,
            "idle_s": self.raw_idle.value(),
            "input_active": None if input_text == "unknown" else input_text == "true",
            "capabilities": {"foreground": self.raw_foreground_cap.isChecked(),
                             "idle": self.raw_idle_cap.isChecked(), "input_activity": input_text != "unknown"},
            "ttl_s": self.raw_ttl.value(),
        }})

    def _submit_estimate(self):
        self.action.emit({"kind": "set_estimate", "state": self.estimator_state.currentText(),
                          "evidence": [x.strip() for x in self.estimator_evidence.text().split(",") if x.strip()],
                          "ttl_s": self.estimator_ttl.value()})

    def _apply_correction(self):
        self._ui("state.override", payload={"state": self.state.currentText(),
                 "reason": self.correction_reason.text(), "ttl_s": self.correction_ttl.value()})

    @staticmethod
    def _render_json(widget, value):
        text = json.dumps(value, ensure_ascii=False, indent=2)
        if widget.toPlainText() != text:
            vertical = widget.verticalScrollBar().value()
            horizontal = widget.horizontalScrollBar().value()
            widget.setPlainText(text)
            widget.verticalScrollBar().setValue(vertical)
            widget.horizontalScrollBar().setValue(horizontal)

    def _render_observation(self, value):
        frame = value.get("observation_frame")
        if not frame:
            return
        observation = frame["observation"]
        raw = frame.get("raw")
        now = datetime.fromisoformat(value["now"])
        age = None if not raw else round(max(0.0, (now - datetime.fromisoformat(raw["sampled_at"])).total_seconds()), 2)
        sampler = frame["sampling_name"]
        if self.args.profile == "live-smoke" and hasattr(self, "_live_source"):
            sampler = type(self._live_source).__module__ + "." + type(self._live_source).__qualname__
        self.observation_status.setText(
            f"采样：{sampler} · 估计器：{frame['estimator_name']}\n"
            f"样本状态：{frame['raw_status']} · 样本年龄：{age if age is not None else 'missing'}s · "
            f"context_revision：{observation['context_revision']} · 有效状态：{observation['estimate']['state']}")
        editable = bool(value.get("sampling_editable"))
        for control in (self.raw_application, self.raw_idle, self.raw_input,
                        self.raw_foreground_cap, self.raw_idle_cap, self.raw_ttl, self.raw_submit):
            control.setEnabled(editable)
        self.raw_refresh.setEnabled(True)
        if not editable and raw:
            self.raw_application.setText(raw.get("application") or "")
            self.raw_idle.setValue(raw.get("idle_s") or 0)
            self.raw_foreground_cap.setChecked(raw.get("capabilities", {}).get("foreground", False))
            self.raw_idle_cap.setChecked(raw.get("capabilities", {}).get("idle", False))
        self.estimator_controls.setEnabled(bool(value.get("estimator_editable")))
        self.correction_status.setText(
            f"纠正状态：{frame['correction_status']} · "
            f"最终来源：{observation['estimate']['source']}")
        estimate = frame["estimated"]
        correction = frame.get("correction")
        self.raw_view_summary.setText(
            f"{frame['raw_status']} · 来源 {raw['source'] if raw else 'missing'} · 样本 {raw['sample_id'] if raw else '-'}\n"
            f"采样 {raw['sampled_at'] if raw else '-'}\n有效至 {raw['valid_until'] if raw else '-'}")
        self.estimate_view_summary.setText(
            f"估计 {estimate['state']} · 来源 {estimate['source']}\n"
            f"证据 {', '.join(estimate['evidence']) or '-'}\n有效至 {estimate.get('valid_until') or '无有效证据'}")
        self.correction_view_summary.setText(
            f"生效 {observation['estimate']['state']} · context {observation['context_revision']}\n"
            f"纠正 {frame['correction_status']} · 来源 {correction.get('origin', 'user') if correction else '-'}\n"
            f"有效至 {correction['valid_until'] if correction else '-'}")
        self._render_json(self.raw_view, {"raw_status": frame["raw_status"], "age_s": age,
                                       "sampling_name": sampler, "raw": raw, "error": frame.get("error")})
        self._render_json(self.estimate_view, {"estimator_name": frame["estimator_name"],
                                            "estimated": frame["estimated"]})
        self._render_json(self.correction_view, {"correction_status": frame["correction_status"],
                                               "correction": frame.get("correction"),
                                               "context_revision": observation["context_revision"],
                                               "effective_estimate": observation["estimate"]})

    def _ui(self, action_id, *, task_id=None, version=None, payload=None):
        ui = UiAction(event_id=_id("ui"), action_id=action_id, task_id=task_id,
                      expected_task_version=version, surface_revision=max(1, version or 1),
                      payload=payload or {})
        self.action.emit({"kind": "ui", "action": ui.model_dump(mode="json")})
        return ui.event_id

    def _draft_payload(self):
        return {"content": self.content.text(), "mode": self.mode.currentText(),
                "delay_s": self.delay.value(), "window_s": self.window_s.value()}

    def _selected_task(self):
        current = self.task_list.currentItem()
        task_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        return self.views.get(task_id)

    def _create(self):
        task_id = f"gui-{self._next_task}"
        self._next_task += 1
        self._select_task = task_id
        self._ui("reminder.create", task_id=task_id, version=0, payload=self._draft_payload())

    def _task(self, kind, payload=None, task_id=None, version=None):
        view = self.views.get(task_id) if task_id else self._selected_task()
        if view is None:
            self._error("请选择任务")
            return
        if version is not None and view["version"] != version:
            self._error("旧卡片动作已拒绝：STALE_SURFACE")
            return
        if kind == "cancel":
            self.pending_cancels.add((view["task_id"], view["version"]))
            self._dismiss(view["task_id"])
        event_id = self._ui("reminder.done" if kind == "complete" else f"reminder.{kind}",
                            task_id=view["task_id"], version=view["version"], payload=payload or {})
        if kind == "cancel":
            self._cancel_commands[event_id] = (view["task_id"], view["version"])

    def _inject(self, kind):
        task = self._selected_task()
        if task:
            self.action.emit({"kind": kind, "task_id": task["task_id"]})
        else:
            self._error("请选择任务")

    def _session(self, kind):
        item = self.session_list.currentItem()
        view = self.session_views.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if kind != "start" and view is None:
            self._error("请选择工作段")
            return
        payload = {"goal": self.goal.text(), "duration_s": 60} if kind == "start" else {}
        if kind == "end":
            payload = {"confirmed_outcomes": ["已确认：完成一次接口联调"], "next_steps": ["接入正式模块"]}
        payload.update({"session_id": _id("session") if kind == "start" else view["session_id"],
                        "expected_session_version": 0 if kind == "start" else view["version"]})
        self._ui(f"session.{kind}", payload=payload)

    @pyqtSlot(object)
    def _snapshot(self, value):
        if self._closing or self._finishing:
            return
        self.latest = value
        self._render_observation(value)
        self.views = {v["task_id"]: v for v in value["tasks"]}
        self.session_views = {v["session_id"]: v for v in value["sessions"]}
        rows = [(v["task_id"], v["version"], v["status"], v["displayed_at"], v["draft"]["content"]) for v in value["tasks"]]
        if rows != self._last_task_rows:
            selected = self._selected_task()
            selected_id = self._select_task or (selected["task_id"] if selected else None)
            if self._select_task in self.views:
                self._select_task = None
            self.task_list.clear()
            for v in value["tasks"]:
                text = f'{v["task_id"]} · v{v["version"]} · {v["status"]} · {v["draft"]["content"]}'
                if v["displayed_at"]:
                    text += " · 已展示"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, v["task_id"])
                self.task_list.addItem(item)
                if selected_id == v["task_id"]:
                    self.task_list.setCurrentItem(item)
            if self.task_list.currentRow() < 0 and self.task_list.count():
                self.task_list.setCurrentRow(self.task_list.count()-1)
            self._last_task_rows = rows
        for delivery_id, card in list(self.cards.items()):
            view = self.views.get(card.command.task_id)
            if view and (view["version"] != card.command.task_version or view["status"] != "registered"):
                self._dismiss(card.command.task_id)
        for command in list(self._surface_commands.values()):
            view = self.views.get(command.task_id)
            if view and (view["version"] != command.task_version or view["status"] != "registered"):
                self._dismiss(command.task_id)
        session_rows = [(v["session_id"], v["version"], v["state"], round(v["remaining_s"], 1)) for v in value["sessions"]]
        if session_rows != self._last_session_rows:
            selected = self.session_list.currentItem()
            selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
            self.session_list.clear()
            for v in value["sessions"]:
                item = QListWidgetItem(f'{v["goal"]} · v{v["version"]} · {v["state"]} · {v["remaining_s"]:.1f}s')
                item.setData(Qt.ItemDataRole.UserRole, v["session_id"])
                self.session_list.addItem(item)
                if selected_id == v["session_id"]:
                    self.session_list.setCurrentItem(item)
                if v["confirmed_outcomes"]:
                    self.review.setText("已确认成果：" + "；".join(v["confirmed_outcomes"]) + "\n下一步：" + "；".join(v["next_steps"]))
            if self.session_list.currentRow() < 0 and self.session_list.count():
                self.session_list.setCurrentRow(self.session_list.count()-1)
            self._last_session_rows = session_rows
        self.status.setText(f'{value["now"]} · {value["clock"]} · 状态 {value["observation"]["estimate"]["state"]} · {value["event_count"]} 个事件')
        if self.surface_context is not None:
            self.surface_context._update_snapshot(value)
        if self.ui_surface is not None and not self._surface_failed:
            try:
                self.ui_surface.render_snapshot(self.surface_context.snapshot)
            except Exception as exc:
                self._fail_surface(exc)

    @pyqtSlot(object)
    def _result(self, result):
        cancelled = self._cancel_commands.pop(result.get("command_id"), None)
        if cancelled and result.get("status") == "rejected":
            self.pending_cancels.discard(cancelled)
        self.results.append(result)
        self.log.append(json.dumps(result, ensure_ascii=False))
        if self.surface_context is not None and not (self._closing or self._finishing):
            self.surface_context._publish_result(result)

    @pyqtSlot(str)
    def _error(self, message):
        self.errors.append(message)
        self.log.append(message)
        if getattr(self.args, "auto_smoke", False):
            QTimer.singleShot(0, lambda: self._finish_smoke(False))

    def _startup_timeout(self):
        if not self.ready_for_actions and not self._closing:
            self._smoke_checks["service_initialized"] = False
            self._error("服务初始化超时")

    def _receipt(self, command, outcome="displayed", error=None):
        if self._finishing:
            return
        source = "offscreen" if QGuiApplication.platformName() in ("offscreen", "minimal") else "native"
        if not self.latest:
            return
        receipt = DisplayReceipt(delivery_id=command.delivery_id, task_id=command.task_id,
                                 task_version=command.task_version,
                                 rendered_at=(datetime.now(timezone.utc) if self.latest["clock"] == "wall" else self.latest["now"]), outcome=outcome,
                                 source=source, error_code=error)
        self.receipts[command.delivery_id] = receipt
        if outcome == "displayed":
            output = Path(self.args.output)
            output.mkdir(parents=True, exist_ok=True)
            self._grab_evidence(output / f"delivery-{len(self.receipts)}.png")
        self.action.emit({"kind": "receipt", "receipt": receipt.model_dump(mode="json")})

    @pyqtSlot(object)
    def _display(self, command):
        if self._finishing:
            return
        view = self.views.get(command.task_id)
        if ((command.task_id, command.task_version) in self.pending_cancels
                or (view and (view["version"] > command.task_version or view["status"] in ("cancelled", "completed")))):
            self._receipt(command, "rejected", ErrorCode.STALE_TASK)
            return
        if self.latest and command.expires_at <= datetime.fromisoformat(self.latest["now"]):
            self._receipt(command, "rejected", ErrorCode.EXPIRED_REQUEST)
            return
        cached = self.receipts.get(command.delivery_id)
        if cached and cached.outcome == "displayed":
            self.action.emit({"kind": "receipt", "receipt": cached.model_dump(mode="json")})
            return
        fault = self.delivery_fault.currentText()
        if fault == "渲染失败":
            self._receipt(command, "failed", ErrorCode.FAILED)
            return
        if fault == "缺失回执":
            return
        if self._surface_configured:
            if self._surface_failed:
                self._receipt(command, "failed", ErrorCode.FAILED)
                return
            if command.delivery_id not in self._surface_commands:
                self._surface_commands[command.delivery_id] = command
                self.tabs.setCurrentWidget(self.ui_surface)
                try:
                    self.ui_surface.show_reminder(command.model_copy(deep=True))
                except Exception as exc:
                    self._fail_surface(exc)
            return
        if command.delivery_id in self.cards:
            return
        buttons = []
        for label, kind, payload in [("已完成", "complete", {}), ("10秒后", "snooze", {"seconds": 10}), ("修改", "edit", None), ("取消", "cancel", {})]:
            buttons.append((label, lambda checked=False, k=kind, p=payload, c=command: self._task(
                k, self._draft_payload() if p is None else p, task_id=c.task_id, version=c.task_version)))
        card = ReminderCard(command, buttons)
        card.painted.connect(lambda cmd: self._receipt(cmd) if cmd.delivery_id in self.cards else None)
        self.cards[command.delivery_id] = card
        self.card_layout.insertWidget(self.card_layout.count()-1, card)
        card.show()

    @pyqtSlot(str)
    def _dismiss(self, task_id):
        routed = any(c.task_id == task_id for c in self._surface_commands.values())
        self._surface_commands = {key: c for key, c in self._surface_commands.items() if c.task_id != task_id}
        if routed and self.ui_surface is not None and not self._surface_failed:
            try:
                self.ui_surface.dismiss_reminder(task_id)
            except Exception as exc:
                self._fail_surface(exc)
        for key, card in list(self.cards.items()):
            if card.command.task_id == task_id:
                self.card_layout.removeWidget(card)
                card.hide()
                card.deleteLater()
                del self.cards[key]

    @pyqtSlot()
    def _ready(self):
        self.ready_for_actions = True
        if self.args.profile == "live-smoke":
            self.advance_button.setEnabled(False)
            self._start_live_sampling()
        if getattr(self.args, "auto_smoke", False):
            self._await_smoke_window()

    def activate_for_launch(self):
        """One foreground activation for an explicit launch; refreshes never raise."""
        if self._activation_requested:
            return
        self._activation_requested = True
        if QGuiApplication.platformName() in ("offscreen", "minimal"):
            return
        self.raise_()
        self.activateWindow()
        if sys.platform == "darwin":
            try:
                import AppKit
                AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception as exc:
                self._activation_error = type(exc).__name__

    def _await_smoke_window(self):
        if self._finishing:
            return
        if QGuiApplication.platformName() in ("offscreen", "minimal"):
            self._start_auto_smoke()
            return
        handle = self.windowHandle()
        exposed = bool(handle and handle.isExposed())
        active = self.isActiveWindow()
        if self._activation_requested and active and exposed:
            self._native_active_at_start = active
            self._native_exposed_at_start = exposed
            self._start_auto_smoke()
            return
        self._smoke_start_wait += 1
        if self._smoke_start_wait >= 50:
            self._native_active_at_start = active
            self._native_exposed_at_start = exposed
            self._smoke_checks["native_window_ready"] = False
            self._error("原生 GUI smoke 需要可见且激活的桌面窗口；请保持联调窗未遮挡")
            return
        QTimer.singleShot(100, self._await_smoke_window)

    def _grab_evidence(self, path):
        # QWidget.grab can send synthetic paint events. They are evidence capture,
        # never proof that another reminder was presented on the real desktop.
        self._capturing = True
        try:
            return self.grab().save(str(path))
        finally:
            self._capturing = False

    def _start_live_sampling(self):
        try:
            from .composition import resolve_sampling_source
            from deskpet.mocks.clock import RealClock
            self._live_source = resolve_sampling_source(
                profile="live-smoke", clock=RealClock(), adapters=getattr(self.args, "adapters", None))
            self._last_live_error_code = None
            self._live_timer = QTimer(self)
            self._live_timer.timeout.connect(self._sample_live)
            self._live_timer.start(1000)
            self._sample_live()
        except Exception as exc:
            self._error(f"真实采样不可用：{type(exc).__name__}: {exc}")

    def _sample_live(self):
        try:
            value = self._live_source.sample()
            data = value.model_dump(mode="python") if isinstance(value, RawSignals) else value
            raw = RawSignals.model_validate(data)
            self._capabilities = raw.capabilities
            self._last_live_error_code = None
            sampling_name = type(self._live_source).__module__ + "." + type(self._live_source).__qualname__
            self.action.emit({"kind": "raw_sample", "sample": raw.model_dump(mode="json"), "sampling_name": sampling_name})
        except Exception as exc:
            code = type(exc).__name__
            if code != getattr(self, "_last_live_error_code", None):
                self._last_live_error_code = code
                sampling_name = type(self._live_source).__module__ + "." + type(self._live_source).__qualname__
                self.action.emit({"kind": "sampling_error", "error_code": code, "sampling_name": sampling_name})

    def _start_auto_smoke(self):
        """Exercise the same widget handlers; no direct domain adapter access."""
        self.delay.setValue(0 if self.args.profile == "live-smoke" else 10)
        self.mode.setCurrentText("hard")
        self.state.setCurrentText("working")
        self.correction_ttl.setValue(300)
        self.correction_apply.click()
        self._smoke_steps = [
            (lambda: self.create_button.click(), lambda: "gui-1" in self.views, "draft_created"),
            (lambda: self.confirm_button.click(), lambda: self.views.get("gui-1", {}).get("status") == "registered", "confirmed"),
            (lambda: self.advance_button.click() if self.args.profile == "scenario" else None,
             lambda: self.views.get("gui-1", {}).get("displayed_at") is not None and bool(self.cards or self._surface_commands), "paint_receipt"),
            (lambda: self._task("snooze", {"seconds": 10}), lambda: self.views.get("gui-1", {}).get("occurrence", 0) == 2, "snoozed"),
            (lambda: self.advance_button.click() if self.args.profile == "scenario" else None,
             lambda: self.views.get("gui-1", {}).get("displayed_at") is not None, "snooze_display"),
            (lambda: self._task("complete"), lambda: self.views.get("gui-1", {}).get("status") == "completed" and not (self.cards or self._surface_commands), "user_completed"),
            (lambda: self.create_button.click(), lambda: "gui-2" in self.views, "second_draft"),
            (lambda: self._task("edit", {**self._draft_payload(), "content": "修改后的提醒"}), lambda: self.views.get("gui-2", {}).get("draft", {}).get("content") == "修改后的提醒", "edited"),
            (lambda: self.confirm_button.click(), lambda: self.views.get("gui-2", {}).get("status") == "registered", "second_confirmed"),
            (lambda: self._inject("save-decision"), lambda: any(r.get("status") == "saved" for r in self.results), "decision_saved"),
            (lambda: self.cancel_button.click(), lambda: self.views.get("gui-2", {}).get("status") == "cancelled", "cancelled"),
            (lambda: self._inject("late-decision"), lambda: any(r.get("error_code") in ("STALE_TASK", "CANCELLED") for r in self.results), "stale_rejected"),
            (lambda: self.session_buttons["start"].click(), lambda: bool(self.session_views), "session_started"),
            (lambda: self.session_buttons["pause"].click(), lambda: any(v["state"] == "paused" for v in self.session_views.values()), "session_paused"),
            (lambda: self.session_buttons["resume"].click(), lambda: any(v["state"] == "focusing" for v in self.session_views.values()), "session_resumed"),
            (lambda: self.session_buttons["end"].click(), lambda: any(v["state"] == "ended" and v["confirmed_outcomes"] for v in self.session_views.values()), "session_review"),
        ]
        if self.args.profile == "scenario":
            self._smoke_steps.extend(self._observation_smoke_steps())
        else:
            self._smoke_steps.append((lambda: self.tabs.setCurrentIndex(1),
                lambda: bool(self.latest.get("observation_frame", {}).get("raw")) and
                    self.latest["observation_frame"]["raw"]["source"] == "live",
                "live_raw_signal_visible"))
        self._smoke_timer = QTimer(self)
        self._smoke_timer.timeout.connect(self._auto_step)
        self._smoke_timer.start(100)
        self._smoke_action_sent = False

    def _observation_smoke_steps(self):
        def frame():
            return self.latest.get("observation_frame", {})
        def raw_available():
            self.tabs.setCurrentIndex(1)
            self.raw_application.setText("Editor")
            self.raw_idle.setValue(15)
            self.raw_ttl.setValue(30)
            self.raw_foreground_cap.setChecked(True)
            self.raw_idle_cap.setChecked(True)
            self.raw_submit.click()
        def apply_working():
            self.state.setCurrentText("working")
            self.correction_reason.setText("联调：用户纠正优先")
            self.correction_ttl.setValue(30)
            self.correction_apply.click()
        def change_raw():
            self.raw_application.setText("Viewer")
            self.raw_idle.setValue(20)
            self.raw_submit.click()
        def new_task():
            self.tabs.setCurrentIndex(0)
            self.delay.setValue(1000)
            self.create_button.click()
        def short_raw():
            self.tabs.setCurrentIndex(1)
            self.raw_ttl.setValue(1)
            self.raw_submit.click()
        def short_correction():
            self.state.setCurrentText("working")
            self.correction_ttl.setValue(1)
            self.correction_apply.click()
        return [
            (lambda: self.correction_clear.click(), lambda: frame().get("correction_status") == "cleared", "correction_cleared"),
            (raw_available, lambda: bool(frame().get("raw")) and
             frame().get("estimated", {}).get("based_on_sample_id") == frame()["raw"]["sample_id"] and
             frame()["raw"].get("application") == "Editor", "raw_to_estimate"),
            (apply_working, lambda: frame().get("correction_status") == "active" and
             frame().get("observation", {}).get("estimate", {}).get("state") == "working", "user_correction_applied"),
            (change_raw, lambda: frame().get("raw", {}).get("application") == "Viewer" and
             frame().get("observation", {}).get("estimate", {}).get("state") == "working", "raw_does_not_override_user"),
            (lambda: self.correction_clear.click(), lambda: frame().get("correction_status") == "cleared" and
             frame().get("observation", {}).get("estimate", {}).get("state") == frame().get("estimated", {}).get("state"), "clear_restores_estimate"),
            (new_task, lambda: "gui-3" in self.views, "observation_task_created"),
            (lambda: self.confirm_button.click(), lambda: self.views.get("gui-3", {}).get("status") == "registered", "observation_task_confirmed"),
            (lambda: self._inject("save-decision"), lambda: sum(r.get("status") == "saved" for r in self.results) >= 2, "observation_decision_saved"),
            (short_raw, lambda: frame().get("raw", {}).get("valid_until") is not None and
             frame().get("raw_status") == "fresh", "short_sample_submitted"),
            (lambda: self.observation_advance_button.click(),
             lambda: frame().get("raw_status") == "expired" and
             frame().get("observation", {}).get("estimate", {}).get("state") == "unknown", "expired_raw_unknown"),
            (lambda: self._inject("late-decision"), lambda: any(r.get("error_code") == "STALE_CONTEXT" for r in self.results), "expired_context_rejected"),
            (short_correction, lambda: frame().get("correction_status") == "active" and
             frame().get("observation", {}).get("estimate", {}).get("state") == "working", "correction_without_fresh_raw"),
            (lambda: self.observation_advance_button.click(),
             lambda: frame().get("correction_status") == "expired" and
             frame().get("observation", {}).get("estimate", {}).get("state") == "unknown", "correction_expired"),
        ]

    def _auto_step(self):
        if self._smoke_index >= len(self._smoke_steps):
            self._finish_smoke(True)
            return
        action, condition, name = self._smoke_steps[self._smoke_index]
        if not self._smoke_action_sent:
            self._smoke_action_sent = True
            action()
        if condition():
            self._smoke_checks[name] = True
            if name in {"raw_to_estimate", "user_correction_applied", "raw_does_not_override_user",
                        "clear_restores_estimate", "expired_raw_unknown", "correction_expired",
                        "live_raw_signal_visible"}:
                self._observation_checkpoints[name] = self.latest.get("observation_frame")
                output = Path(self.args.output)
                output.mkdir(parents=True, exist_ok=True)
                self._grab_evidence(output / f"observation-{name}.png")
            self._smoke_index += 1
            self._smoke_wait = 0
            self._smoke_action_sent = False
        else:
            self._smoke_wait += 1
            if self._smoke_wait >= 180:
                self._smoke_checks[name] = False
                self._finish_smoke(False)

    def _finish_smoke(self, success):
        if self._finishing:
            return
        self._finishing = True
        if hasattr(self, "_smoke_timer"):
            self._smoke_timer.stop()
        if hasattr(self, "_live_timer"):
            self._live_timer.stop()
        if not self._stop_service_thread():
            self.errors.append("服务线程收尾超时")
            success = False
        output = Path(self.args.output)
        output.mkdir(parents=True, exist_ok=True)
        source = "offscreen" if QGuiApplication.platformName() in ("offscreen", "minimal") else "native"
        from .composition import provenance
        if self.args.profile == "live-smoke":
            frame = self.latest.get("observation_frame", {})
            raw = frame.get("raw") or {}
            live_ok = bool(self._capabilities and self._capabilities.get("native_desktop")
                           and raw.get("source") == "live" and frame.get("raw_status") == "fresh")
            self._smoke_checks["live_observation_sampled"] = live_ok
            success = success and live_ok
        success = success and not self.errors
        self.report = {**provenance(), "status": "passed" if success else "failed", "profile": self.args.profile,
                       "receipt_source": source, "native_gui_verified": success and source == "native",
                       "manual_review": "pending", "checks": self._smoke_checks,
                       "errors": self.errors, "capabilities": self._capabilities,
                       "receipt_count": len(self.receipts), "qt_platform": QGuiApplication.platformName(),
                       "observation_frame": self.latest.get("observation_frame"),
                       "ui_surface": {"configured": self._surface_configured, "failed": self._surface_failed,
                                      "adapter": self._surface_spec,
                                      "delivery_owner": "ui_surface" if self._surface_configured else "workbench"},
                       "observation_checkpoints": self._observation_checkpoints,
                       "activation_requested": self._activation_requested,
                       "native_active_at_start": self._native_active_at_start,
                       "native_exposed_at_start": self._native_exposed_at_start,
                       "activation_error": self._activation_error,
                       "native_smoke_precondition": "interactive desktop, foreground at start, keep workbench exposed"}
        (output / "native-gui-report.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        self._grab_evidence(output / "native-gui.png")
        if self.ui_surface is not None:
            self.tabs.setCurrentWidget(self.ui_surface)
            self._grab_evidence(output / "c-ui-surface.png")
        self.tabs.setCurrentIndex(1)
        self._grab_evidence(output / "observation.png")
        QTimer.singleShot(100, self.close)

    def _stop_service_thread(self):
        if not self.thread.isRunning():
            return True
        self.stop_requested.emit()
        return self.thread.wait(3000)

    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return
        self._closing = True
        if hasattr(self, "_smoke_timer"):
            self._smoke_timer.stop()
        if hasattr(self, "_live_timer"):
            self._live_timer.stop()
        if not self._stop_service_thread():
            self._closing = False
            self._error("服务线程尚未停止，请稍后重试关闭")
            event.ignore()
            return
        if self.surface_context is not None:
            self.surface_context._close()
        event.accept()


def run_gui(args) -> int:
    """CLI entry. QApplication is intentionally created only here."""
    from deskpet.perception.dpi import set_dpi_awareness
    set_dpi_awareness()
    app = QApplication.instance() or QApplication(["mochi-m0"])
    if args.profile == "live-smoke" and QGuiApplication.platformName() in ("offscreen", "minimal"):
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        from .composition import provenance
        report = {**provenance(), "status": "unsupported", "profile": args.profile,
                  "receipt_source": "offscreen", "native_gui_verified": False,
                  "reason": "live-smoke requires a native QApplication; offscreen is not OS evidence"}
        (output / "native-gui-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 2
    window = Workbench(args)
    window.show()
    QTimer.singleShot(0, window.activate_for_launch)
    app.exec()
    if getattr(args, "auto_smoke", False):
        return 0 if window.report and window.report["status"] == "passed" else 1
    return 0
