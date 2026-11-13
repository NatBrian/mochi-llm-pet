# GUI 与工作段

M0 窗口是保留诊断页的 Qt 联调壳：提供真实输入、确认、任务列表、观察状态和快进控制。C 可通过 ui_surface 把自己的组件嵌入同一窗口，共享任务和工作段后端。默认固定组件及嵌入样例不解释完整动态 UiSpec；正式动态 renderer 仍由 C 实现。

`UiSpec` 包含 schema_version、surface_id/revision、task_id/version、components 和 data。组件子集为 text/card/text_input/select/button/list。组件只绑定注册动作和数据，不执行任意代码。未知组件在公共模型校验时拒绝。

`UiAction` 包含 event_id、action_id、任务预期版本、surface_revision 和 payload。注册动作：reminder.create/confirm/done/snooze/edit/cancel、state.override、state.clear_override、session.start/pause/resume/end。GUI 对旧表面动作进行拒绝；TaskService 再进行任务版本校验。完成操作映射到 TaskCommand.complete。

主线程创建 QWidget 和渲染卡片；任务服务线程维护状态、版本与调度。跨线程传结构化消息，经 queued signal 返回视图。卡片只产生一次有效展示回执；同一 delivery_id 重送复用卡片。旧任务、已取消任务或失效卡片不能被迟到响应重新打开。

## C 的嵌入组件与宿主

配置 `ui_surface: "module:factory"` 后，联调台在 GUI 主线程调用 `factory(*, context, parent) -> QWidget`，挂载「C 界面 · 实际展示」页。任务与工作段、Observation 和日志保留。组件必须实现 `render_snapshot(snapshot: dict)`、`show_reminder(command: DisplayCommand)`、`dismiss_reminder(task_id: str)`。[Qt 接入实现](../../deskpet/m0/ui_surface.py) · [独立组件样例](../../deskpet/m0/example_surface.py)

宿主负责 QApplication、服务线程、queued gateway、统一 Clock 和状态快照；C 的组件只读快照、提交动作、绘制及报告结果。工厂构造期间服务可能尚未就绪，不应提交动作。快照是 JSON 字典，包含 now、clock、tasks、sessions、observation、observation_frame 和 event_count；`context.snapshot` 及传给组件的快照均为副本，修改它们不会写入服务。所有 SurfaceContext 调用在 GUI 主线程完成。

| 上下文接口 | 语义 |
| --- | --- |
| `snapshot` | 最近共享快照的深拷贝；尚未收到快照时为空。 |
| `submit_action(UiAction 或 dict) -> str` | 校验结构后排队到服务线程，返回 event_id；不直接写任务或工作段状态。 |
| `result_received` | Qt 信号，发布共享的操作结果字典。任务和工作段命令结果用 command_id 关联提交时的 event_id，旧表面拒绝结果携带 event_id。 |
| `can_report_display` | 是否处于可报告展示的组件生命周期及可见状态。 |
| `report_display(delivery_id, *, outcome="displayed", error_code=None) -> bool` | 仅确认已经路由到该组件的请求。host 生成 DisplayReceipt 的任务、版本、时间和来源；非成功结果必须提供 ErrorCode。返回 true 表示 host 已将回执送入服务队列。服务核验结果由 receipt_recorded / receipt_rejected 及交付事件给出。 |

配置 ui_surface 后，正式投递只发给 C 组件，默认任务页不再生成正式提醒卡。新投递切到 C 页，常规快照刷新不切页、不抢窗口焦点。组件应按 delivery_id 去重，在真实可见 paint 后通过排队回调确认；创建控件、调用 show_reminder 或截图绘制均不足以报告 displayed。host 拒绝未知或过期请求，以及隐藏、截图期间或重复提交的成功回执。组件异常显式报告失败，不回退默认卡片冒充该组件验收成功。

`gui: "module:run_gui"` 保留整体宿主替换能力；GUI 命令禁止同时配置 gui 与 ui_surface。headless replay 不导入两类界面模块。ui_surface 是 Qt 接入协议，公共 deskpet.contracts 仍不依赖 Qt。组件销毁或宿主关闭后不能再提交动作。[ADR-0007](../adr/0007-embedded-ui-surface.md) · [C 的接手步骤](../handoff/c-gui-work-sessions.md)

## 工作段参考协议

SessionCommand.start payload 包含 goal、duration_s，可选 task_id；pause/resume 使用当前 session version；end 可提交 confirmed_outcomes 和 next_steps。暂停保留 remaining_s，继续重新登记到期时刻。结束工作段不自动完成关联任务。

M0 的状态子集是 focusing/paused/break_due/ended；正式 candidate/自动模式/休息阶段等由 C 另行扩展契约。mock 用共享 Clock 证明暂停、继续和结束语义；没有另建 Qt 倒计时或持久化。基础回顾仅显示明确确认的成果和下一步。

## 观察诊断

界面读取 ObservationFrame 中的 raw、estimated、observation.estimate 与 correction_status。以有效截止时间说明各层 TTL，保留原始能力缺口和失败原因。state.override 提交 state、reason、ttl_s，state.clear_override 提交空 payload。GUI 手工操作生成 origin=user，自动 smoke 生成 origin=automation。显示最近一次 read/pump 产生的帧，snapshot() 不会再次采样。用户纠正的原因与有效区间随输入保存，清除和到期都会重新暴露当前基础估计。回放时的原始信号注入只改变采样输入。[观察契约](observation.md) · [C 的联调步骤](../handoff/c-gui-work-sessions.md)
