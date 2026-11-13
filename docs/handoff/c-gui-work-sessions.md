# C · 动态界面与工作段（A5、A7）

首个交付行为是在 C 的嵌入界面开始、暂停、继续和结束工作段，联调台的任务与工作段页同步显示该工作段的目标、版本和剩余时间，两页通过 session_id 关联同一记录。随后在 C 页承接到期提醒，并通过「已完成」或「10秒后」改变同一任务。M0 提供固定诊断页、有状态的工作段 mock 和可运行的 C 界面样例。C 可独立替换工作段服务、嵌入界面或同时替换两者。动态 UiSpec 渲染、番茄钟产品交互与正式回顾界面仍由 C 实现。[界面与工作段语义](../contracts/gui-work.md) · [接口所有权](../contracts/interfaces.md) · [端口定义](../../deskpet/contracts/ports.py)

## 界面和领域服务的交界

| 输入 | C 的动作或输出 | 对端 |
| --- | --- | --- |
| UiSpec，含 surface_revision 与可选的 task_version | 仅呈现受支持的组件和动作，操作时带回 UiAction、event_id、surface_revision 和 expected_task_version | A 定义公共协议，B 核对任务版本 |
| TaskView、CommandResult | 从最新快照刷新控件，提交 UiAction，由宿主在服务线程转换为 TaskCommand | B 持有任务状态，C 负责过期界面反馈 |
| DisplayCommand | 在 Qt 线程展示或报告失败；嵌入组件通过 context.report_display 报告，host 构造 DisplayReceipt | B 根据带 delivery_id、版本、时间和来源的回执确认首次展示 |
| SessionCommand | WorkSessionService 返回 SessionResult 和带版本的 WorkSession；渲染剩余时长、阶段、confirmed_outcomes、next_steps | C 实现此端口，D 消费工作段事件 |
| 联调台快照与 SurfaceContext | render_snapshot 更新组件；submit_action 提交 UiAction；result_received 接收操作结果 | A 的 Qt 壳负责装配与 queued gateway，C 负责组件 |

任务和工作段各自检查版本。暂停保留剩余时间，继续以剩余时间计算到期；结束时仅记录用户确认的结果与下一步。固定窗将提醒操作封成 UiAction，排队分发时核对 surface_revision。旧表面返回 STALE_SURFACE，任务命令通过此检查后再由 B 校验 expected_version。用契约中的 task_id 关联工作段与提醒，跨服务操作通过命令提交。Qt 主线程负责渲染，领域写入经排队入口派发。[装配入口](../../deskpet/m0/composition.py)

### 接入自己的工作段服务和界面

通过 `ui_surface` 把 C 的组件装入联调台的「C 界面 · 实际展示」页，任务与工作段、Observation 和日志继续保留。通过 `work_sessions` 替换工作段服务。两项可独立启用；省略一项就使用该层默认实现。在本地 `adapters.json` 中保留要替换的项，将模块名换成自己的可导入模块。给下方联调命令追加 `--adapters adapters.json`。

```json
{
  "work_sessions": "c_sessions:factory",
  "ui_surface": "c_ui:create_surface"
}
```

- `work_sessions` 的工厂签名为 `factory(*, clock, emit)`，返回实现 WorkSessionService 的实例。[参考实现](../../deskpet/mocks/work_session.py)
- `ui_surface` 的工厂签名为 `create_surface(*, context, parent) -> QWidget`。工厂和组件在 Qt 主线程运行；底座创建 QApplication、服务线程、Clock 和信号连接。C 不另建任务后端或业务倒计时。[Qt 接入边界](../../deskpet/m0/ui_surface.py) · [可替换样例](../../deskpet/m0/example_surface.py)
- `gui` 仍可指向整体窗口入口 `run_gui(args) -> int`，适合自行提供完整宿主。GUI 命令禁止同时配置 `gui` 与 `ui_surface`；嵌入联调台时使用 `ui_surface`。离线 replay 使用同一配置但不导入或启动两种界面模块。[CLI 路由](../../deskpet/m0/__main__.py)

### 组件必须实现的接口

工厂返回 QWidget，提供以下三个方法。C 只需修改自己的组件模块与适配配置，无需编辑 Workbench、Application 或公共装配代码。

| 方法或上下文成员 | 用法 |
| --- | --- |
| `render_snapshot(snapshot: dict)` | 收到最新共享快照后刷新布局。字段包括 now、clock、tasks、sessions、observation、observation_frame、event_count；不在重绘时再次采样或写领域状态。 |
| `show_reminder(command: DisplayCommand)` | 按 delivery_id 幂等创建提醒表面。真实可见绘制后报告回执，任务取消或版本改变前保留相应关联信息。 |
| `dismiss_reminder(task_id: str)` | 移除该任务的旧表面和待执行回调。 |
| `context.snapshot` | 返回最近快照的深拷贝。组件不能借此修改后端；服务就绪前为空，构造阶段不提交动作。 |
| `context.submit_action(UiAction 或 dict)` | 校验后经 queued gateway 提交，返回 event_id。任务动作带 expected_task_version 与 surface_revision；工作段动作在 payload 中带 session_id 与 expected_session_version。 |
| `context.result_received` | 任务和工作段命令结果的 command_id 对应提交时的 event_id，旧表面拒绝结果直接携带 event_id。其他共享结果可能只有状态或 delivery_id。 |
| `context.can_report_display` | 在 paintEvent 和随后排队的回调中检查当前能否报告实际展示。 |
| `context.report_display(delivery_id, *, outcome="displayed", error_code=None)` | 返回 true 表示 host 已提交回执，最终是否入账以服务结果和交付事件为准。failed/rejected 必须带 ErrorCode，host 补齐任务、版本、时间及 native/offscreen 来源。 |

SurfaceContext 仅在 GUI 主线程使用，宿主关闭后不再接受动作。其 Qt 类型留在 m0 层，公共领域契约依然可无 Qt 导入。

配置 `ui_surface` 后，该组件是唯一正式提醒展示端。投递会切换到 C 页；普通快照更新不切页或激活窗口。原诊断页继续显示任务，但不会再生成第二张正式提醒卡。调用 show_reminder、创建控件或截图均不等于实际展示。C 负责在可见绘制后提交回执；host 会拒绝未知或过期请求，以及隐藏、截图过程中或重复提交的成功回执。组件装配或运行异常会显示失败，不用默认提醒卡掩盖问题。

如果后续复用旧桌宠，C 可复用动画、窗口与交互，A 负责共享宿主装配和展示路由。当前样例未接旧桌宠；不要从组件启动旧 Application.main 中的第二套 Brain、Sensors 或记忆循环。

## 联调与取证

在仓库根目录分别运行离线用例和真实 Qt 窗口。scenario 使用脚本观测、虚拟时钟和脚本策略。

```sh
uv run python -m deskpet.m0 replay --suite m0 --output output/m0/c-replay
uv run python -m deskpet.m0 gui --profile scenario --scenario reminder-flow --output output/m0/c-gui
```

先运行随仓库提供的嵌入样例，再把配置里的 ui_surface 改为自己的工厂：

```sh
uv run --offline python -m deskpet.m0 gui --profile scenario --adapters examples/m0/c-ui.adapters.json --output output/m0/c-embedded
```

1. 在「C 界面 · 实际展示」页开始工作段，切到「任务与工作段」对照目标、版本与剩余时间。从操作结果与 trace.jsonl 的 session.applied 事件核对 session_id。
2. 在 C 页点暂停，在诊断页快进 10 秒，确认剩余时间不变；回 C 页继续，再快进，确认两个界面显示相同结果。填写成果和下一步后结束，核对两处回顾与 trace 中的 session 事件。
3. 在诊断页创建 hard 提醒，设为 10 秒后，确认并快进。应自动切到 C 页，出现提醒卡；真实绘制后产生同一 delivery_id 的成功回执。默认诊断页不另生成提醒卡。点「已完成」，核对任务完成和 C 卡片消失。
4. 使用自己的 work_sessions 与 ui_surface 工厂一起重复上述步骤，只更改 adapters.json，保存 trace.jsonl 与截图。模拟回执和 offscreen 绘制不能作为原生展示验收。

不配置 ui_surface 时，可继续用默认固定面板核对参考行为：

1. 在窗口选择 hard，最早提醒设为 10 秒，点击「创建草稿」「确认」「快进 10 秒」。看到卡片后点「已完成」。在 `output/m0/c-gui/trace.jsonl` 对照同一 delivery_id 的 delivery.requested 和 delivery.displayed。
2. 输入工作目标，依次点「开始60秒」「暂停」「继续」「结束/记录成果」。核对 session_id、ended 和回顾。固定窗的结束按钮提交预填示例成果；正式界面需让用户填写或确认实际成果。
3. 再建一条 hard 提醒，这次在到期前将「展示出口」设为「渲染失败」。快进到期后，预期出现 delivery.failed，错误码 FAILED，这次请求不会产生成功展示回执。交付给 B 的证据包括 delivery_id、task_version 和 trace 行号。

回放总览为 `output/m0/c-replay/report.json`，每个场景另有 `report.json` 和 `trace.jsonl`。自动操作原生窗口时给 gui 命令追加 `--auto-smoke`，检查 `native-gui-report.json` 的 checks、receipt_source、native_gui_verified，并人工查看 `native-gui.png`。验收原生渲染时 receipt_source 应为 native。

只复现暂停计时时，将 replay 的 `--suite m0` 换成 `--scenario session-pause`。预期 work-1 的最终状态为 ended、v4，剩余 10 秒，确认的成果为 “Read introduction”。[工作段场景](../../deskpet/m0/fixtures/session-pause.json)

替换 WorkSessionService 时由 C 实现 apply、get、list_sessions、pump；动态 renderer 在 C 的 UI 层消费 UiSpec。与 B 对齐 TaskView 刷新和回执处理，与 A 对齐动作与组件白名单、装配和公共模型，与 D 对齐展示及回顾事件。[B 的任务交接](b-task-service.md) · [D 的评估交接](d-evaluation.md)

## 观察面板与用户纠正

C 接收服务线程发布的 ObservationFrame。worker 在 read/pump 刷新后发布 snapshot() 的缓存结果，界面重绘直接使用收到的帧。显示 raw 来源、采样能力、失败项、采集时间和截止时间，按 estimated.based_on_sample_id 展开基础估计的 evidence_details。将 observation.estimate 作为任务当前使用的状态，并单列纠正来源（手工 user、自动 smoke 为 automation）、原因、创建时间、截止时间及 correction_status。scenario 中的原始信号注入与用户纠正各有入口。live 的输入来自主线程桌面采样，向 worker 传结构化样本。用户只提交合法状态、原因与有效期，清除动作交由管线处理。工作段开始或任务卡出现都不能自动生成用户标签。

在「Observation · 采样 / 估计 / 纠正」页保留 Editor、idle 15 秒和 30 秒样本有效期，点击「提交模拟原始采样」，基础估计应为 available。选 working、写明原因、将纠正有效期设为 5 秒，点击「应用用户纠正」，检查 effective 已变、raw 和基础估计仍可追溯。快进越过纠正有效期，分别检查原始证据仍新鲜与已经失效两种结果。[A 的交接](observation.md) · [观察契约](../contracts/observation.md)
