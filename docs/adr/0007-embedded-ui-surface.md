# ADR-0007 · C 界面嵌入联调台并共享后端

Accepted · 2026-11-11 · 负责人 A，联调方 C、B、D

已有 work_sessions 工厂可以替换工作段逻辑，但 gui 配置只能替换整个 Qt 入口。C 若交付新布局，需要自行保留或复制诊断界面，难以在同一个联调台直接核对状态、观察和事件。

1. 新增独立的 ui_surface 工厂，签名为 `factory(*, context, parent) -> QWidget`。A 的 Workbench 保留 QApplication、服务线程、统一 Clock、信号连接与诊断页；C 的组件实现 render_snapshot、show_reminder、dismiss_reminder，在 GUI 主线程装入单独选项页。work_sessions 与 ui_surface 可分别替换。
2. 组件只通过 SurfaceContext 读取快照副本、排队提交 UiAction、接收操作结果和报告已分配请求的展示结果。任务及工作段写入仍由各自服务验证，组件不能取得服务实例或拥有第二份业务状态。Qt 适配类型留在 m0 层，领域公共契约仍可无 Qt 导入。
3. 一个宿主只选择一个正式提醒展示端。配置 ui_surface 时，正式投递由 C 组件处理，默认诊断页不生成第二张提醒卡。host 补齐回执的任务、版本、时间和来源，拒绝未知或过期请求，以及隐藏、截图过程或重复提交的成功回执；组件负责确保 displayed 来自真实可见绘制。新投递切换到 C 页，常规刷新不抢焦点。
4. 装配或运行异常显式展示失败并拒绝后续组件动作，不回退默认卡片掩盖实现问题。整体 gui 入口替换仍保留，与 ui_surface 在 GUI 命令中互斥。离线 replay 不导入或启动界面模块。
5. 用仓库内固定组件样例验证工作段双向同步、提醒展示和用户动作。它不等于正式 UiSpec renderer，也不启动旧桌宠 Brain、Sensors、真实模型或额外倒计时。以后复用桌宠窗口时仍使用相同服务和展示路由。

此方案把 C 的验收收敛为只改组件、工作段适配器及配置，即可在联调台操作并核对同一业务状态。新增独立进程 UI、多展示端回执聚合或正式动态渲染语义需另行定义，不由本插槽隐式承担。

[组件契约](../contracts/gui-work.md) · [C 的交接](../handoff/c-gui-work-sessions.md) · [ADR-0003](0003-task-consistency.md) · [ADR-0004](0004-clock-and-delivery.md)
