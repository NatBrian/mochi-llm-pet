# 事件、回放与证据

`TraceEvent` 的公共字段为 schema_version、seq、ts、monotonic_s、run_id、scenario_id、kind、source、simulated、clock，另有适用的 task/request/call/delivery/session ID 与 task_version。业务详情放 payload。

事件链分开记录 task.created/confirmed/edited/snoozed/cancelled/completed、command.rejected/duplicate、decision.accepted/rejected、recheck.scheduled/fired、delivery.requested/displayed/failed/timeout/rejected 及工具和工作段事件。以实际导出的轨迹和对应 owner 的发出点为准；D 新增统计事件时不得替代 B 的业务回执记录。

JSONL sink 是实验出口；sink 失败不能回滚已经提交的业务状态。TraceRecorder 保留内存事件与 sink_errors 供诊断。每次测试/回放使用独立输出目录，保留完整 report 与 trace。

## Scenario 与 Trace 不同

Scenario 存储初始时间和按 `(at_s, seq)` 排列的输入事件，以及 expected_outcomes。重放输入后由同一 TaskService、WorkSession 与 ToolRuntime 重新产生 Trace；不能把 display 等输出事件再次作为命令执行。

场景来源是合成 fixture，不是实际用户会话。快进回放验证因果、状态、拒绝和重复；不证明模型效果或真实延迟。无真实模型调用时成本标记为 simulated，不输出“真实 API 费用为零”的研究结论。

## 实机证据

报告包括源码与 fixtures 指纹、契约版本、Python/Qt/OS、模式、真实采样能力、回执来源与逐项结果。native GUI 的自动 smoke 和人工体验核验分别记录；没有执行、unsupported 与失败均不能合并成通过。缺少 GUI 回执时，只有请求记录，不能统计展示成功。

真实感知只在 live-smoke 显式启用：观察日志保留应用进程名、能力、空闲秒数、光标位置、窗口数等粗粒度信号以及单独的状态估计，不采集截图、剪贴板或按键内容。记录缺失能力，证据不足时输出 unknown，不悄悄替换成脚本数据。foreground 是本进程时不把联调窗当用户工作应用。

## Runner 输入语法

`command`、`session`、`tool`、`receipt` 分别携带对应公共模型的 JSON；`observation` 携带 `state` 及可选 `source/evidence/valid_until`；`advance` 仅由 `at_s` 推进时钟，payload 必须为空。`fault` 可设置 `delivery=displayed|failed|missing` 或 `tools={工具名: timeout|failed}`。同一时刻先处理输入，再 pump 到期项，因此 `(at_s, seq)` 是取消与到期竞争的确定顺序。

普通 `decision` 携带 `ProposedPlan`。额外支持 runner 专用的输入缩写 `{"scripted": true, "envelope": {...DecisionEnvelope...}}`，用于运行内置 ScriptedFastPolicy、ScriptedSlowPlanner 与有限结果反馈。这不是领域 `ProposedPlan` 的 wire format；正式 Planner 仍返回公共 `ProposedPlan`。`scripted-feedback` 场景展示一次超时和一次复规划，事件中有 `policy.fast`、`runtime.feedback`。

回放 `report.json` 的 `outcomes.displays` 只统计收到 `delivery.displayed` 回执的唯一 delivery；`delivery_requests` 单列投递请求。缺回执时前者为 0，后者可以非 0。二者均不能证明原生桌面展示，需另查 receipt_source 和 native GUI 报告。


## 观察回放的证据范围

`raw_sample` 可提交完整 RawSignals。ScriptedSamplingSource.submit 保留原始采样 ID、采集时间和截止时间，接收旧样本不会刷新其 TTL。内置可控脚本源还支持简写，例如 `{"application":"Editor","idle_s":15,"ttl_s":30}`。简写会在事件发生时生成样本 ID 与截止时间，保留未指定的测量字段。传入显式 capabilities 会替换整个能力表。管线按场景时钟判断是否新鲜，再运行已装配的估计器。`correction` 携带 UserCorrection，回放强制写入 origin=scripted。`clear_correction` 携带空对象。用 `advance` 的 at_s 触及时效边界。outcomes.observation_states 提供每个输入处理后的有效状态，outcomes.observation_frame 提供最终诊断帧。轨迹记录 observation.sampled、observation.estimated 和 observation.correction.applied / cleared / expired。纠正事件在 origin 为 scripted / automation 或使用虚拟时钟时标记 simulated。人工在 scenario 窗口操作时，origin=user 且 simulated=true。sampled 对应样本变化；同一样本因时间推进失效时，可沿 estimated 的 error 与帧的 raw_status 复核。保留两种来源字段，raw.source 说明 scripted / live，estimate.source 说明 rule / scripted / model / user。比对 raw.sample_id、estimated.based_on_sample_id、证据与时间，随后单独核对纠正是否影响最终 estimate。场景直接输入样本时，能验证估计与下游行为；桌面采样 API 仍需 live-smoke 验证。

旧 `observation` 输入直接指定 state，属于 legacy-state-injection。这条路径可用于已知状态驱动的任务测试，无法证明 raw 到状态的估计规则或真实 OS 采样。[观察契约](observation.md)
