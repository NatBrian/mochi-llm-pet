# ADR-0005：离线回放与两平台实测证据

- 状态：Accepted
- 日期：2026-11-10
- 决策：scenario 装配只包含脚本观察、模型替身和 FakeClock。live-smoke 显式使用原生 Qt 与 OS 采样，模型仍模拟。缺权限/平台不支持必须报告，不能悄悄返回 fake 并标通过。
- 记录：Scenario 存输入，Trace 存运行结果。JSONL 含来源、关联 ID、时间类型和真实/模拟标记。D 的指标出口不代替 B 的恢复、去重和回执存储。
- 验收：回放两次结果一致，原生 GUI 与真实采样分别核验；快进、offscreen 和模拟 HTTP 均不作为真实 P95 或模型效果证据。
- Windows：用户将执行脚本交给 Windows agent，回传与 macOS 同源指纹的报告。回传前明确待验收。既有 macOS 沙箱 Qt CPU 特征检测限制通过受控执行环境解决，不为此升级依赖。
