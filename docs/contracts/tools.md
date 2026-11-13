# M0 受限工具与结果反馈

工具由 Python 应用注册，模型或脚本只提交 ToolCall，不提交可执行代码。每个调用包含 call_id、tool_name、args、DecisionEnvelope 与 idempotency_key。TaskService 在执行前验证任务版本、状态、context 和期限。

| 工具 | args | 结果与所有者 |
| --- | --- | --- |
| show_reminder | `{}` | TaskService 经 DeliveryPort 投递；GUI 回执前 pending；A 编排/B 校验/C 渲染 |
| schedule_recheck | `{"run_at":"带时区的ISO时间"}` | 登记任务版本绑定的复查；不能越过用户截止；B 执行 |
| start_pomodoro | `{"session_id":"s","goal":"目标","duration_s":1500}` | 经 WorkSessionService 同一入口创建；C 实现 |
| save_important_observation | `{"state":"working/available/away/unknown","evidence":["证据引用"]}` | M0 存在 Runtime 内存样例中，标记 scripted_tool；不是已验证用户事实或长期记忆 |
| execute_plan | `{"steps":[{"tool_name":"...","args":{}}]}` | 最多4个已注册叶子动作，禁止递归计划，逐步重新校验 |

未知工具和多余参数拒绝。ToolResult 分 succeeded/pending/rejected/failed/timeout/duplicate，包含 error_code、receipt_id、state_version 和 evidence。幂等键重用但参数不同会拒绝；同一 pending 调用可在 GUI 回执后以同一键查询完成，不能无限缓存 pending。

`ToolRuntime.execute_plan` 在第一步前验证整个结构，遇到 pending、失败或拒绝停止后续步骤；部分已经生效的动作保留真实记录。`ToolRuntime.run` 接收观察、任务、planner 与信封，把工具结果送回 planner，最多一次重规划，总动作预算4步。新规划使用派生 request_id，保持原任务/context/有效期约束。

`scripted-feedback` 场景演示确定性快策略、慢规划、工具超时及结果驱动的复查方案。它不发真实模型请求；后续主负责人替换 planner 后继续运行相同工具与任务契约。
