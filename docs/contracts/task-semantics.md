# 任务、版本、时钟与展示语义

## 命令

| kind | payload | 约束 |
| --- | --- | --- |
| create | `{"draft": TaskDraft}` | expected_version=0；只产生草稿，不调度 |
| confirm | `{}` | 确认用户时间窗后注册 |
| edit | `{"draft": TaskDraft}` | 修改版本，撤销旧投递/复查；注册任务保持注册 |
| snooze | `{"seconds": 正数}` | 从当前时刻延后；保留软窗口宽度；新 occurrence |
| cancel | `{}` | 任务取消，撤销待执行展示与卡片 |
| complete | `{}` | 用户明确完成；展示回执不能代替此命令 |

create 之后的命令带 expected_version。每次已接受的任务变更增加版本；展示事实不增加任务版本。相同 command_id 和相同输入返回 duplicate，不能二次修改；同 ID 携带不同输入返回 INVALID_ARGUMENT。

状态：`draft → registered → completed/cancelled`；草稿也可取消。投递状态独立于任务状态；已展示但尚未完成的 registered 任务仍可延后、编辑或取消。

## 调度与接受规则

hard 的 earliest_at 与 latest_first_delivery_at 必须相等，到期不等待模型。soft 在窗口内 available 时可以显示；working/away/unknown 不作为提前打断的依据，最迟首次展示时使用固定兜底。模型与工具只能建议，TaskService 在接受和执行前校验任务、版本、取消状态、有效期和时间窗。

错误枚举包含 STALE_TASK、STALE_CONTEXT、EXPIRED_REQUEST、CANCELLED、OUTSIDE_WINDOW、DUPLICATE、INVALID_TRANSITION、INVALID_ARGUMENT、NOT_FOUND、TIMEOUT、FAILED、BUDGET_EXCEEDED 和 UNSUPPORTED。状态拒绝是可观察结果，不应静默吞掉。

同一时刻的命令按进入服务的顺序裁决。回放用 `(at_s, seq)` 固定这个顺序；若要取消先于到期处理，应把取消输入排在对应 pump 前。每个输入处理后再 pump，不抢先执行尚未裁决的同刻取消。

## 展示事实

1. 服务生成稳定 delivery_id，绑定任务版本与 occurrence，记录展示请求。
2. `DeliveryPort.show` 返回回执，或返回 None 等待异步 GUI 回执。
3. 原生 GUI 仅在有效卡片已完成可见绘制后回传 native/displayed；这表示客户端渲染事实，不证明用户已经注意到。
4. 记录 displayed 后才停止本次兜底；失败/缺回执具有明确记录和重试。重复 delivery_id 复用同一张卡。
5. 用户后续点击完成/延后/取消是独立命令。取消前真实发生的投递保留历史。

simulated、native、offscreen 是不同回执来源。offscreen 不能计为原生桌面验收。内存服务不承诺跨进程 exactly-once；B 的恢复协议采用持久投递意图、重试同 ID、幂等 GUI 和回执落盘。

## 时钟

所有任务、复查、请求有效期、脚本超时和工作段消费同一注入 Clock。FakeClock.advance 不 sleep；Qt 定时器只触发 pump。scenario 使用固定 UTC 基准与虚拟时间，live-smoke 使用 RealClock。虚拟时间不用于 P50/P95 实机延迟结论。

## B 的恢复验收夹具

[recovery-fixture.json](recovery-fixture.json)提供与 SQL 表结构无关的恢复边界：已确认且待回执的任务、已取消但残留旧 job 的任务、延后后仍残留旧版本 job 的任务。它按公共 RecoveryFixture/TaskView/DisplayCommand 校验，`examples --check` 校验同一内容的示例副本。

B 需把这些状态用自己的仓库格式落盘，关闭进程后重启服务：待回执投递重用 persisted-delivery-1，取消任务不展示，延后任务忽略旧 job 且不提前展示。请在 B 的正式实现中增加真实多进程用例。夹具的 verification 明确为 pending_real_SQLite_process_restart，M0 内存参考实现不提供恢复，不能把夹具校验通过当持久化已通过。
