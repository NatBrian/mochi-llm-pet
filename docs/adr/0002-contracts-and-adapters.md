# ADR-0002：公共类型与端口

- 状态：Accepted
- 日期：2026-11-10
- 背景：独立 mock 若字段和真实实现不同，会把联调错误推到集成末期。
- 决策：Pydantic v2 模型作为字段/枚举的单一机器真源，Protocol 表达进程内服务接口，JSON Schema 从模型导出。公共导入无 Qt、平台和网络依赖。通过开发者配置的 module:factory 装配真实实现或 mock。
- 结果：样例、Schema、契约测试与 handoff 一起维护。模型返回结构化任务建议，旧 coerce_intent 不处理任务 JSON。所有者在自己目录实现接口，GUI/Runtime 不窥探模拟对象私有字段。
- 替代：本阶段不引入 HTTP 服务、通用消息中间件或跨进程 DTO；如未来需要，以新的 ADR 定义部署和版本兼容。

观察端口的拆分及其 v1 兼容约定见 [ADR-0006](0006-observation-pipeline.md)。
