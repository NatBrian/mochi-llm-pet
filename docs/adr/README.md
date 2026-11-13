# 架构决策记录

状态为 Accepted 表示 M0 已选择的实现方向，不代表后续正式功能全部完成。变更由主负责人维护，关联契约和 owner 同时更新。

| 编号 | 决策 |
| --- | --- |
| [0001](0001-m0-boundaries.md) | M0 独立入口与四包所有权 |
| [0002](0002-contracts-and-adapters.md) | 公共模型、进程内端口与可替换模拟 |
| [0003](0003-task-consistency.md) | 任务单写、版本、幂等与 GUI 线程边界 |
| [0004](0004-clock-and-delivery.md) | 统一 Clock、投递回执与恢复边界 |
| [0005](0005-replay-and-evidence.md) | 回放、离线模式及两平台证据 |
| [0006](0006-observation-pipeline.md) | 采样与估计分离、证据时效及用户纠正 |
| [0007](0007-embedded-ui-surface.md) | C 界面嵌入联调台、共享后端与单一展示路由 |
