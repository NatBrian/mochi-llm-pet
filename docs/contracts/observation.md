# Observation v1 · 采样、估计与纠正

| 边界 | 产生的值 | 接收方 |
| --- | --- | --- |
| `SamplingSource.sample() -> RawSignals` | 样本 ID、采样器 ID、脚本或桌面来源、采集时间、失效时间、能力和可用信号 | A 的观察管线 |
| `StateEstimator.estimate(signals) -> UserStateEstimate` | working / available / away / unknown，估计器 ID、样本 ID、证据与失效时间 | 同一管线 |
| `ObservationSource.read() -> Observation` | 应用有效纠正后的 estimate、上下文版本及兼容的顶层字段 | B 的 TaskService 与 A 的策略 |
| `ObservationFrame` | 原始样本及状态、基础估计、纠正及状态、当前 Observation、适配器名称和错误 | C 的诊断界面与 D 的核验 |

**采样。** 管线的 read() 执行采样与估计，环境 pump 刷新时效并驱动下游。已有观察后，snapshot() 只返回最近已产生的 ObservationFrame；GUI 展示与 TaskService 本轮消费的是同一份结果。单独推进时钟后应先 read 或 pump，再读快照。每个 RawSignals 的有效区间为 `[sampled_at, valid_until)`。同一份样本的时间随样本保存，读取或刷新视图不会延长这个区间。application 表示前台应用，idle_s 是空闲秒数，input_active 是活动指示，cursor_position 是光标坐标，window_count 是窗口计数。无法取得的值可为 null。对照 capabilities 和 errors 区分采集失败、缺权限、部分可用与真实的零值。live 适配器只提供桌面摘要，排除自身窗口，采样不需要截图、按键文本或剪贴板内容。

**估计。** 此端口是同步的快速本地调用。远程请求或耗时模型需由独立 worker 执行，再由协调器关联并校验完成结果。当前规则基线可供后续 Jev / System 2 接入时对照。

在原始样本的有效期之内，估计器依据可用信号给出状态。StateEvidence 记录 ref、信号名、值、来源、sampled_at 与 valid_until；用 based_on_sample_id 回到该次原始观测。evidence 保留 v1 的简要文本。默认规则先查 idle 能力和 idle_s。空闲至少 120 秒时估计为 away。低于 120 秒时须有外部前台应用，空闲少于 10 秒为 working，10 至不足 120 秒为 available。规则未使用光标坐标或窗口数判定状态。到达有效期边界、收到未来样本或缺少判断依据时，基础状态为 unknown。证据支持的是规则在该时刻作出的推断。窗口数量和光标位置本身不能证明用户是否专注。

管线用 frame.raw_status 区分 fresh、expired、future、missing、error。新鲜样本也可能只产生 unknown，例如 idle 能力缺失。估计器抛错或返回不匹配的样本 ID、超出样本有效期的结果、非法证据引用时，检查 frame.error 和基础估计，不应把这类失败计入传感器缺失。estimate.source=user 只用于用户纠正。

**纠正。** UserCorrection 含 correction_id、state、reason、created_at、valid_until、origin。origin 默认为 user，表示手工输入；回放固定为 scripted，GUI 自动操作标为 automation。有效纠正优先于基础估计，observation.estimate 的 source 为 user。基础估计继续随新样本更新。清除或到期后回到当时仍有效的基础估计，基础证据已过期时回到 unknown。检查 frame.correction_status 区分 none / active / cleared / expired。估计的生存期与纠正的生存期分别核对。

**任务与兼容性。** TaskService 读取 observation.estimate、context_revision 与 Clock。策略提出的动作携带该 revision 并经任务服务校验。语义改变会使旧决定失效，同一状态下采样 ID、时间戳、光标等变化不足以使决定失效。v1 输入保留 observed_at、estimate、capabilities、application、idle_s、source 等字段和原来的简要 evidence。旧输入允许省略 raw、correction、based_on_sample_id 和 evidence_details，D 记录证据缺口。

[公共类型](../../deskpet/contracts/models.py) · [公共端口](../../deskpet/contracts/ports.py) · [装配与验收](../handoff/observation.md)

原始样本已失效或缺失时，降级 `unknown` 的 valid_until 为 null，界面显示“无有效证据”。这表示当前缺少可采纳的依据，不给原始数据续期。旧决定仍因语义版本变化被拒绝；在新 unknown 上下文中提出的计划受自己的 DecisionEnvelope 期限和任务约束校验。
