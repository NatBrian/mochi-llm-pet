# B · 提醒任务服务与交付调度（A3、A6）

首个可交付行为是让同一个提醒从草稿、确认、到期交付走到完成。随后用 SQLite 替换内存实现，验证取消、重复命令和进程重启时的行为。M0 在独立入口装配有状态内存服务、虚拟时钟和可控的交付端，供 B 开发期间与 A、C 并行联调。[任务语义](../contracts/task-semantics.md) · [接口所有权](../contracts/interfaces.md) · [端口定义](../../deskpet/contracts/ports.py)

## 从命令到交付

| B 接收 | B 产出 | 对端 |
| --- | --- | --- |
| 带 command_id、task_id 和 expected_version 的 TaskCommand | CommandResult，含状态、actual_version、错误码和可用的 TaskView | C 用返回值刷新界面，A 经工具端口提交命令 |
| Observation、DecisionEnvelope、ProposedPlan | 校验任务版本、上下文版本和有效期，决定能否接纳计划或安排复查 | A 提供观测、策略与受限工具 |
| 时钟到期、有效交付请求 | 含 delivery_id、版本、occurrence 和有效期的 DisplayCommand | C 展示后返回 DisplayReceipt |
| DisplayReceipt | 对应任务的首次展示事实、交付事件 | C 提供渲染结果，D 消费事件 |

创建时用 expected_version=0，后续写入使用最近一次返回的版本。硬提醒的两个窗口端点相同，软提醒在确认的交付窗口内调度。首次实际展示必须能追溯到同一交付请求的成功回执。把命令去重、版本检查、状态迁移及待交付记录放在同一条写入路径，避免重复点击、晚到的模型决定或通道重试触发第二次交付。[字段与枚举](../../deskpet/contracts/models.py)

## 替换内存服务

在 B 的适配器模块实现 TaskService，沿用装配提供的 Clock、DeliveryPort 和事件出口。让 SQLite 事务保存任务版本、已处理命令及可恢复的调度状态，按 task_id、发生次数与交付标识核对恢复结果。[装配入口](../../deskpet/m0/composition.py)

M0 的 `examples --check` 校验 RecoveryFixture 的数据结构。夹具将跨进程恢复标为 `pending_real_SQLite_process_restart`，`m0_persistence` 为 false。B 需按自己的 SQL 表结构将夹具中的任务、待回执交付和调度作业写入 SQLite，关闭写入进程，再由新进程打开同一数据库，逐项核对结果。[恢复验收夹具](../contracts/recovery-fixture.json)

| 重启前状态 | 新进程应产生的结果 |
| --- | --- |
| recover-pending 的交付已发出，回执待到 | 重试沿用 persisted-delivery-1 和原 occurrence，成功回执到达后才记展示 |
| recover-cancelled 已取消，仍残留 v2 作业 | 丢弃旧作业，交付请求数为 0 |
| recover-snoozed 已延后至 17:01 UTC，仍残留 v2 作业 | 丢弃 v2 作业，17:01 前无交付，到期处理 v3、occurrence 2 |

保存重启前后的任务快照、交付 ID 和两次进程运行的事件日志，作为 B 的恢复验收证据。

### 只替换自己的适配器

在可导入的模块中提供工厂 `factory(*, clock, delivery, emit)`，返回与 [MemoryTaskService](../../deskpet/mocks/task_service.py) 相同端口的实例。将下列 JSON 保存为本地 `adapters.json`；示例中的 `b_tasks` 换成自己的 Python 模块名。给下方 replay 或 gui 命令追加 `--adapters adapters.json`，其余角色会使用默认实现。

```json
{"task_service": "b_tasks:factory"}
```

在 POSIX shell 中用 `M0_ADAPTERS=adapters.json uv run python -m pytest -q tests/m0/test_task_service.py` 运行同一套契约断言。PowerShell 中先运行 `$env:M0_ADAPTERS = "adapters.json"`，再运行相同的 pytest 命令。重点核对异步交付：窗口内提交返回 pending，窗口关闭后仍可查询同一请求，收到有效回执后查询应返回成功，查询不会产生第二次展示。[任务服务契约测试](../../tests/m0/test_task_service.py)

## 联调与取证

在仓库根目录运行离线回放，再打开固定 Qt 联调窗。scenario 使用虚拟时间和脚本策略，无需模型密钥。

```sh
uv run python -m deskpet.m0 replay --suite m0 --output output/m0/b-replay
uv run python -m deskpet.m0 gui --profile scenario --scenario reminder-flow --output output/m0/b-gui
```

1. 在窗口选择 hard，最早提醒设为 10 秒，点击「创建草稿」「确认」「快进 10 秒」。收到卡片后点击「已完成」，依次核对 task.confirmed、delivery.requested、delivery.displayed、task.completed。
2. 再建一条提醒，确认后点「保存当前决策版本」「取消」「注入旧决策」。预期 decision.rejected 的 payload.error_code 为 CANCELLED。用 task_id 检查取消后成功交付数为 0。
3. 保存 `output/m0/b-gui/trace.jsonl`。回放总览在 `output/m0/b-replay/report.json`，预期 status 为 passed，各场景目录另有 trace.jsonl 和 report.json。比较任务版本、错误码和 delivery_id，保留失败用例原始报告。

单独复现取消竞争时，将 replay 命令里的 `--suite m0` 换成 `--scenario cancel-late`。预期 milk 停在 cancelled、v3，晚到的工具结果为 rejected / CANCELLED，displays 为 0。[取消场景输入与断言](../../deskpet/m0/fixtures/cancel-late.json)

公共字段、错误码或决策有效期需要调整时联系 A。交付请求与展示回执的关联问题联系 C。交付计数与故障统计联系 D。[C 的界面交接](c-gui-work-sessions.md) · [D 的评估交接](d-evaluation.md)

## 接收观察与拒绝旧决定

B 保留 `observe(Observation)` 边界。软提醒窗口内提前展示时检查当前有效 estimate 及其 valid_until，到期的 available 不能作为提前展示的依据。显式且有效的用户纠正可改变当前状态。hard 到期与 soft 最迟时刻仍遵循已确认的窗口。保存决策时记录 context_revision，执行时核对当前语义版本与请求 TTL。仅光标移动或更换同类采样 ID 应允许旧决定继续通过上下文检查。用 A 的 raw_sample 回放覆盖估计链，历史 observation 场景继续覆盖直接注入后的任务行为。[观察契约](../contracts/observation.md) · [A 的独立验证](observation.md)
