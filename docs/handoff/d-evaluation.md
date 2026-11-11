# D · 事件、回放与评估（A8）

首个交付物是一份能复现的场景判定：从固定输入启动离线回放，按事件关联出命令、决定、交付与回执，再对照场景的 expected_outcomes。D 维护 MetricsSink、标注、对照与报告；A、B、C 在各自路径上发出事实事件。[事件与回放语义](../contracts/events-replay.md) · [接口所有权](../contracts/interfaces.md) · [事件与场景模型](../../deskpet/contracts/models.py)

## 输入、关联与替换

| 提供方 | 输入 D 的事实 | 核对方法 |
| --- | --- | --- |
| A | 观测、策略决定、工具调用和结果 | 通过 request_id、call_id、task_version 关联迟到决定和拒绝原因 |
| B | 命令写入、版本、调度及交付请求 | 按 task_id 与版本排序；重复命令只计一次业务状态迁移 |
| C | 展示回执、用户操作与工作段迁移 | 用 delivery_id 关联请求与回执，用 session_id 追踪工作段 |
| D | 场景输入、标注、判定口径与对照报告 | 保留场景版本、期望值、实际值、分母和无法判定的样本 |

在 D 的模块实现 MetricsSink.record(event)。JSONL 每行是一条 TraceEvent，按 run_id、scenario_id 分组，用 seq 确定同轮顺序。保留 schema_version、source、simulated、clock 和原始关联字段。扩展统计时保留原始 JSONL 供复核；接收端写入失败要单列为证据缺口。[M0 JSONL 接收端](../../deskpet/m0/trace.py) · [装配入口](../../deskpet/m0/composition.py)

### 只替换自己的适配器

在可导入的模块中提供工厂 `factory(*, path)`，返回与 [JsonlMetricsSink](../../deskpet/m0/trace.py) 相同端口的实例。将下列 JSON 保存为本地 `adapters.json`；示例中的 `d_metrics` 换成自己的 Python 模块名。给下方 replay 或 gui 命令追加 `--adapters adapters.json`，其余角色会使用默认实现。

```json
{"metrics": "d_metrics:factory"}
```

## 运行与证据

```sh
uv run python -m deskpet.m0 replay --suite m0 --output output/m0/d-replay
uv run python -m deskpet.m0 gui --profile scenario --scenario reminder-flow --output output/m0/d-gui
```

从仓库根目录运行。总览在 `output/m0/d-replay/report.json`，检查 status、cases、provenance 与 platform_acceptance。每个场景的 report.json 给出 outcomes 和 failures，trace.jsonl 给出事件顺序。outcomes.delivery_requests 统计交付端收到的不同 delivery_id 个数。outcomes.displays 统计产生 delivery.displayed 成功回执的不同 delivery_id 个数。对已发出的同一 ID，delivery.requested 事件条数减一即为重试次数。例如 display-missing 的 delivery_requests 为 1、displays 为 0。[缺失回执场景](../../deskpet/m0/fixtures/display-missing.json)

1. 在固定 Qt 窗口选择 hard，最早提醒设为 10 秒，点击「创建草稿」「确认」「快进 10 秒」。从 `output/m0/d-gui/trace.jsonl` 找到同一 delivery_id 的 delivery.requested 与 delivery.displayed，登记 source、clock、task_version 及两条 seq。
2. 新建第二条提醒，确认后保存决策版本，取消并注入旧决策。预期 decision.rejected 带 CANCELLED。按 task_id 核对其后成功展示数为 0。报告同时给出请求数、成功回执数及拒绝原因。
3. 做策略对照时固定 fixture、时钟、观测和扰动，只改变被测策略。每组记录实现版本、样本量、分子、分母与失败用例；窗口内首次成功展示率的分母应写明如何处理提前取消的提醒。工作段成果的标签使用用户确认值。

单独复现重试时，将 replay 的 `--suite m0` 换成 `--scenario display-failure-recovery`。预期 milk 的 displayed 为 true，displays 为 1，轨迹里同一 delivery_id 先 failed 后 displayed。[交付重试场景](../../deskpet/m0/fixtures/display-failure-recovery.json)

评估工具反馈链时运行 `--scenario scripted-feedback`，核对工具结果依次为 timeout、succeeded，runtime.feedback 出现两次且 recheck.scheduled 出现一次。[脚本策略反馈场景](../../deskpet/m0/fixtures/scripted-feedback.json)

回放中的时钟差值是模拟场景内的时间；真实端到端时延需在同一真实运行上记录起止点和单调时钟。原生窗口使用虚拟时钟时仍归入虚拟时间。模型停用时标记“未调用”，API 费用和模型时延标记“未测”。恢复夹具通过结构校验时登记为“样例已校验”。SQLite 跨进程恢复待 B 提供重启前后的快照与事件日志后再判定。Windows 实机结果待 Windows agent 回传后再计入平台覆盖。

新增指标或事件先跟事实所属方确定触发点：策略与工具找 A，调度与状态找 B，渲染、回执与回顾找 C。公共 schema 变更联系 A。[B 的任务交接](b-task-service.md) · [C 的界面交接](c-gui-work-sessions.md)

## 核对估计器误判与证据时效

对同一条 raw_sample，保留 sample_id、来源、能力和错误、采样与截止时间。用 estimated.based_on_sample_id 关联基础状态、estimator_id 和逐项证据。用户纠正另存 correction_id、origin、原因、状态与有效区间。回放固定 origin=scripted，自动操作为 automation，手工为 user。纠正事件遇到 scripted / automation 或虚拟时钟均标为 simulated。只有手工录入才能列入人工纠正样本，需另核验实验来源及独立标注流程。estimate.source=user 表示当前状态采用纠正，不足以确认纠正是人工输入。只在样本、估计和独立标签有效区间重叠时比较基础估计与标签。若把应用纠正后的有效状态与该纠正比较，会把相同输入重复用作预测和真值。

报告按 working / available / away / unknown 列出有标签样本量、冲突数和例子。分别计数无独立标签、部分能力、过期、未来样本及采样或估计失败，注明误判率分母。同一样本的反复读取不能增加样本量。工作段开启、用户未纠正和合成脚本状态均不足以充当真实用户标签。用 raw_sample 回放验证推断与 TTL 的因果顺序，用历史 observation 场景验证直接给定状态后的下游行为。真实系统效果需取得独立标注并报告覆盖盲区。[观察契约](../contracts/observation.md) · [A 的验证路径](observation.md)
