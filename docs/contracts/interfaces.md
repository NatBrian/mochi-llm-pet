# 类型、端口与替换点

| 边界 | 生产 → 消费 | 公共类型/方法 | 所有者 |
| --- | --- | --- | --- |
| 采样 | OS 适配/脚本 → 观察管线 | SamplingSource.sample → RawSignals | A |
| 状态估计 | 原始样本 → 基础估计 | StateEstimator.estimate → UserStateEstimate | A 实现基线，D 独立标注 |
| 有效观察 | 管线 → Runtime/TaskService；诊断视图 → GUI | ObservationSource.read → Observation；ObservationFrame | A 装配，B/C 消费 |
| 决策 | 快/慢策略 → 校验/工具 | DecisionEnvelope、ProposedPlan；FastPolicy.decide、SlowPlanner.plan | 主负责人 |
| 任务 | GUI/Runtime → TaskService | TaskCommand、CommandResult、TaskView；apply/get/list_tasks | B |
| 计划与复查 | Runtime → TaskService | validate、accept_decision、schedule_recheck、pump | B 校验；主负责人提议 |
| 工具 | Runtime → 注册处理器 → Runtime | ToolCall、ToolResult；execute | 主负责人 |
| 投递 | TaskService → GUI → TaskService | DisplayCommand、DisplayReceipt；DeliveryPort.show/dismiss、record_receipt | C 渲染；B 保存投递事实 |
| GUI | 任务/固定程序 → Qt → TaskService | UiSpec、UiComponent、UiAction | C |
| 联调台组件 | 联调壳 → C 的 QWidget → queued gateway | ui_surface 工厂、SurfaceContext、render_snapshot/show_reminder/dismiss_reminder | A 持有宿主与路由，C 实现组件 |
| 工作段 | GUI/工具 → 工作段服务 | WorkSession、SessionCommand、SessionResult；apply/get/list_sessions/pump | C；存储/计时基础由 B 提供 |
| 实验 | 各 owner → Sink/回放 | TraceEvent、Scenario；MetricsSink.record | D；各 owner 接入 |
| 时钟 | 装配 → 所有领域模块 | Clock.wall_now/monotonic_now | 主负责人固定公共边界 |

## 最小工厂契约

装配入口 `build_environment` 返回 clock、trace、tasks、sessions、observations、runtime、delivery。构造发生在服务所属线程。GUI 经 queued signal 访问，不直接调用属于任务线程的服务。

用 `--adapters path/to/adapters.json` 指向 Python 工厂；这些是开发者安装的仓库代码，不接受模型生成的模块名。

```json
{
  "sampling_source": "my_observation:make_sampler",
  "state_estimator": "my_rules:make_estimator",
  "task_service": "my_backend.adapter:make_task_service",
  "work_sessions": "my_work.adapter:make_work_sessions",
  "metrics": "my_evaluation.adapter:make_sink",
  "ui_surface": "my_gui.surface:create_surface"
}
```

`ui_surface` 工厂签名为 `create_surface(*, context, parent) -> QWidget`，只在 GUI 主线程加载。返回组件实现 render_snapshot、show_reminder、dismiss_reminder；SurfaceContext 提供快照副本、queued UiAction、操作结果信号和受控展示回执。联调台继续维护服务线程与诊断页，C 只修改自己的组件和配置。[组件与宿主契约](gui-work.md)

需要整体替换宿主时，使用独立配置 `{"gui": "my_gui.app:run_gui"}`。GUI 入口签名为 `run_gui(args) -> int`，负责自己的 Qt 窗口与同一输出报告。GUI 命令禁止同时指定 gui 和 ui_surface。replay 不导入或启动两类界面模块；界面配置不改变领域装配。

观察的两个工厂各自只接收关键字参数 `clock`，分别返回 SamplingSource 和 StateEstimator。可单独配置一项进行替换。[观察契约](observation.md)

其余工厂签名分别是 `make_task_service(*, clock, delivery, emit)`、`make_work_sessions(*, clock, emit)`、`make_sink(*, path)`，返回对应 Protocol。`emit(kind, **fields)` 的业务附加信息放在 `payload`。B/C/D 只修改自己的实现与适配配置，无需编辑 Application 或 composition。

替换后的 TaskService 必须运行 `tests/m0/test_task_service.py` 的共享行为集；测试使用 `task_service_factory` fixture，可在自己的 conftest 覆盖此 fixture。真实数据库测试应为每项分配独立数据库，并遵守同一 Clock 与 DeliveryPort。

## 字段规则

全部外部模型拒绝未知顶层字段；时间必须有 UTC offset，进入模型后统一转 UTC。任务的 `timezone` 保留输入时区标识；M0 GUI 使用明确的相对秒数和 UTC 时间，不提供自然语言日期解析。

任务与工作段各自使用单调递增的 version，互不替代。`context_revision` 只表示相关语义变化，不能为每次鼠标采样递增。`schema_version=1` 表示序列化版本。

旧 WorldSnapshot / Intent 仍属于桌宠；新任务、工具和 GUI 领域对象通过适配器接入，不塞进宠物动作词汇。
