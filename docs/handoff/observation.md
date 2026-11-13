# A · Observation（A4）、Runtime 与公共装配

A 交付从原始样本到有效状态的可回放链路。B 决定接受和何时执行任务，C 提供观察面板与用户纠正，D 维护独立标签、指标与评估。交给 B 的值为 Observation，交给 C/D 的诊断数据为 ObservationFrame。[端口签名](../../deskpet/contracts/ports.py) · [字段与时效](../contracts/observation.md)

## 独立运行与替换

从仓库根目录运行下面的命令。scenario 使用虚拟时钟，独立观察入口可在未启动 Qt、任务服务和模型的情况下检查管线。

```sh
uv run python -m deskpet.m0 observe --profile scenario --output output/m0/a-observe
```

默认脚本采样器从无已知活动信号开始，因此第一次读取可得到 fresh / unknown。要检查一条已知输入，将下面内容保存为 `raw.json`。

```json
{
  "sample_id": "editor-1",
  "sampler_id": "manual-fixture",
  "source": "scripted",
  "sampled_at": "2026-01-01T17:00:00Z",
  "valid_until": "2026-01-01T17:00:30Z",
  "capabilities": {"foreground": true, "idle": true},
  "application": "Editor",
  "idle_s": 12
}
```

```sh
uv run python -m deskpet.m0 observe --input raw.json --output output/m0/a-fresh
uv run python -m deskpet.m0 observe --input raw.json --advance 30 --output output/m0/a-expired
```

`--input` 直接把 RawSignals 交给管线，跳过采样器工厂，并将虚拟时钟设为 sampled_at。分别读取两个输出目录的 `observation-report.json`。首次应见 raw_status=fresh、estimated.state=available、based_on_sample_id=editor-1。第二次故意提交过期证据，当前诊断 CLI 返回非零，报告为 failed，帧中应见 raw_status=expired、estimated.state=unknown。要将这个预期降级作为通过断言来验收，可运行下面的 observation-raw-expiry 回放。检查有效区间边界时以帧内字段为准。要测试安装的采样器，省略 `--input`。原生桌面采样通过 `smoke --profile live-smoke` 运行。

自定义适配器文件只需保留希望替换的键，模块必须是本机可导入的开发者代码。将 `--adapters adapters.json` 追加到 observe 命令。只替换 sampling_source 时可检验新采样器与默认规则的组合。只替换 state_estimator 时可在同一组可重现的输入上对照估计结果。

```json
{
  "sampling_source": "my_sampling:make_sampler",
  "state_estimator": "my_estimation:make_estimator"
}
```

- `make_sampler(*, clock)` 返回实现 `sample()` 的对象。为新采集的信号分配 sample_id，声明 sampler_id、source、capabilities 和有效区间。缺信号时保留缺口。原生桌面采样在 Qt 主线程执行，再将 RawSignals 交给服务线程。
- `make_estimator(*, clock)` 返回实现 `estimate(signals)` 的对象。选择有证据的状态，填上 estimator_id、based_on_sample_id、valid_until 与 evidence_details。一次 estimate 是快速、本地的同步调用。远程 Jev 或耗时模型需在独立 worker 执行，由协调器关联样本和异步结果，并在采用结果前再次检查时效。当前脚本策略与规则基线尚未接通真实 Jev / System 2。
- 保留原装配，分别固定一种输入、更换一个工厂并重复同一测试。运行使用的 sampling_name、estimator_name、sample_id 和证据引用应能在诊断帧中对应。

仓库内即可试换估计器。将 `{"state_estimator":"deskpet.mocks.state_estimator:ScriptedStateEstimator"}` 保存到 adapters.json，再运行以下命令。

```sh
uv run python -m deskpet.m0 observe --input raw.json --adapters adapters.json --output output/m0/a-scripted-estimator
```

核对同一 editor-1 样本的基础估计变为 unknown、source 为 scripted。该脚本估计器初始状态为 unknown。在 scenario GUI 中加载同一份适配器配置，可在「估计器调试（仅可控替身）」设定状态、证据引用与有效秒数，点击「提交模拟估计结果」。查看基础估计变化并核对 sample_id 与原始时间保持一致。

## 核验顺序

1. 给出外部前台应用、可用的 idle 和采样截止时间。核对 raw、estimated、observation.estimate 的来源及每条证据的采样 ID 或引用。替换规则时按该规则列出的输入条件检查预期状态。
2. 让时钟走到样本的 valid_until，核对原始时间不变、样本 expired、基础估计 unknown。再输入一个尚未生效的样本，核对 future 与 unknown。用缺权限或空值样本验证 capability 与错误仍可追溯。
3. 在 scenario GUI 中提交带原因和有限 TTL 的用户纠正。有效期内换一条原始样本，核对基础估计变化而有效状态仍服从纠正。记录 correction.origin（手工 user，回放 scripted，自动 GUI 为 automation）与 trace 的 clock、simulated。清除纠正应立即恢复基础估计。再以短 TTL 重试，等待或快进到纠正截止时间检查自动回退。
4. 将相同状态下的重复采样与真实状态切换分别送入任务场景。前者保持上下文版本，后者使旧决定失效。请 B 验证在任务确认窗口内的交付与拒绝记录。独立观察报告只验收观察链。

下面的三个场景在相同的管线上执行，分别覆盖纠正覆盖/清除与能力缺失、纠正到期回退、原始证据过期与新样本恢复。在各自报告的 outcomes.observation_states 核对每个输入后的有效状态。

```sh
uv run python -m deskpet.m0 replay --scenario observation-raw-correction --output output/m0/a-raw-correction
uv run python -m deskpet.m0 replay --scenario observation-correction-expiry --output output/m0/a-correction-expiry
uv run python -m deskpet.m0 replay --scenario observation-raw-expiry --output output/m0/a-raw-expiry
```

可单独运行 `uv run python -m pytest -q tests/m0/test_observation.py` 检查管线的契约断言。[观察测试](../../tests/m0/test_observation.py)

[回放输入](../contracts/events-replay.md) · [B 的调度](b-task-service.md) · [C 的操作路径](c-gui-work-sessions.md) · [D 的评估](d-evaluation.md)
