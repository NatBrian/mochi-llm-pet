# M0 公共契约 v1

本目录是四个工作包的协作入口。设计依据为[飞书任务划分及内部接口](https://d5abb0zl2u.feishu.cn/wiki/HtHXw15Ayiq6X3kPPlncc3pTnqd)（2026-11-10 读取，revision 220）。M0 是进程内 Python 接口，不是 HTTP 服务。

- [类型与所有权](interfaces.md)：每个模块接收什么、产生什么、谁负责替换。
- [观察、证据与纠正](observation.md)覆盖采样、估计、TTL 和有效状态。
- [任务、版本、时钟与回执](task-semantics.md)：可执行的行为约定。
- [受限工具与反馈](tools.md)：注册参数、pending 与有界重规划。
- [GUI 与工作段](gui-work.md)：动作与展示事实的边界。
- [事件与回放](events-replay.md)：合成数据、指标和实机证据。
- [JSON 样例](examples.json)与[导出 Schema](schema.json)：由公共模型校验。
- [ADR](../adr/README.md)、[A/B/C/D 交接](../handoff/README.md)。

字段与枚举的机器真源是 [`deskpet/contracts/models.py`](../../deskpet/contracts/models.py)，方法签名在 [`ports.py`](../../deskpet/contracts/ports.py)。两者导入不加载 Qt、OS 或模型 SDK。禁止各包复制一套私有 DTO 后靠字典猜字段。

```bash
uv run python -m deskpet.m0 examples --check
uv run python -m deskpet.m0 schemas --output docs/contracts/schema.json
uv run python -m deskpet.m0 replay --suite m0 --output output/m0/replay
```

修改契约时，由主负责人统一审查，同时更新模型、样例、Schema、共享测试与相关 handoff。v1 的新增必填字段、枚举或语义变化均须显式讨论；不能以 mock 的额外私有属性作为正式模块依赖。

## 完成状态

M0 包含有状态内存服务、固定联调窗、工作段模拟、事件与回放。任务数据关闭进程后不保存；B 接入 SQLite 后才能验收跨进程恢复。模拟场景通过不代表真实模型、动态 GUI 或正式实验已经完成。
