# Windows agent 执行交接

## 任务

在 Windows 的真实、已解锁桌面上，对同一份 M0 代码运行离线回放、原生 Qt 提醒闭环和真实 OS 感知。由用户把此文档及代码交给 Windows agent。当前 Windows 状态为**待验收**。

## 前置条件与代码一致性

取得完整 M0 工作区（含新增的 contracts/mocks/m0、fixtures、docs/contracts、tests/m0 和 scripts，不能只 checkout 旧 HEAD）。安装 uv 和 Python 3.12；不需要模型 API key 或 Ollama。仓库的本地 config.toml、数据库、密钥及 macOS 虚拟环境无需复制。

在 PowerShell 从仓库根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/smoke_windows.ps1 -Install
```

已安装相同锁定依赖时可省略 `-Install`。默认会运行完整测试、回放、真实 GUI 自动 smoke 与真实采样；不要设置 QT_QPA_PLATFORM=offscreen/minimal。原生进程只在当前桌面打开测试窗口；不启动原桌宠或真实模型。启动时仅激活一次窗口，请在自动验收期间保持窗口可见、未被遮挡；报告会记录 active/exposed 前提。

输出默认在 `output/m0/windows/`。若只想诊断无界面部分，可用 `-SkipNativeGui -SkipLiveObservation`，但这会标记 not_run，不能算 Windows 验收通过。

## 自动结果回传

回传整个输出目录，至少包含：

- windows-summary.json、examples.log、observation.log、pytest.log、replay.log；
- observation/observation-report.json：固定原始输入与独立估计器输出；
- replay/report.json 及场景 trace；
- native-gui/native-gui-report.json、native-gui.png、delivery-*.png 和 trace.jsonl；
- live-observation/native-gui-report.json、截图和 trace.jsonl。

报告包含 commit、source_sha256、契约版本、fixture 指纹、Python/Qt/OS、回执来源和能力。即使 commit 相同，也必须比较 source_sha256，因为 M0 可以存在未提交代码。指纹不同先排查源码、锁文件或 fixture 差异，再比较结果；不能直接宣称跨平台一致。

## 人工 GUI 核验

自动 smoke 通过后，再运行：

```powershell
uv run --offline python -m deskpet.m0 gui --profile scenario --output output/m0/windows/manual
```

按窗口实际按钮录入事项、确认并快进到期。分别验证完成、延后、编辑和取消，选择 working/available/unknown 观察差异；操作工作段开始、暂停、继续和结束。确认文字与卡片可读、点击可用、普通状态刷新不主动抢焦点，取消后旧结果不重开卡。

切换到 Observation 页，核对原始样本、证据、有效期与用户纠正四部分。提交模拟原始信号，应用有原因/TTL 的纠正后再次改变原始信号，检查基础估计更新但最终状态仍服从有效纠正；清除或到期后恢复估计。原生 live 模式应显示真实采样来源，原始信号控件禁用。

记录是否存在 DPI、中文字体、多屏、缺权限或焦点差异。真实采样在本进程窗口前台时 foreground 可能为 false、状态 unknown，这是显式能力边界；可切到其他应用再观察。不得把缺信号写成已验证用户工作状态。

## 给当前 chat 的回传格式

```text
Windows / Python / Qt：
source_sha256 与 macOS 是否一致：
契约版本和 fixture 是否一致：
自动检查：通过 / 失败 / 未执行（附报告）
原生回执来源：
真实采样能力与缺失项：
人工输入/卡片/焦点/取消检查：
错误堆栈或截图：
```

不需要提交 PR；失败时保留原报告，修复后使用新的输出目录重跑。offscreen、跳过项目和未回传的结果均不算真实 Windows 通过。
