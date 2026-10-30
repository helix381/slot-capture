# Slot Capture Local

Windows本地采集器，提供图形界面与命令行。把已验证的采集操作和条件判断放在本地执行，模型只需要读取状态摘要和新事件。

当前版本 **v0.3.0**。**日常修复和小功能用Git提交记录，不增加版本号或版本目录。** 默认构建固定输出 `dist/slot-capture.exe` 与 `dist/slot-capture-gui.exe`。新版界面见 [v0.3.0说明](docs/desktop-v0.3.md)，采集流程见 [机台录入与首转流程](docs/onboarding-v0.2.md)，开发约定见 [贡献说明](CONTRIBUTING.md)。

v0.3.0将正式GUI改为黑白灰基底、橙红强调、轻弥散与分区卡片。四个步骤位于顶部，左侧固定为机台档案，规则助手只在确认规则时展示。主页面单屏展示，长列表分页，右上角内嵌窗口控制。HTML/CSS通过pywebview与Edge WebView2承载，继续调用原有JSON CLI；[原设计稿](design/prototype/index.html)单独保留，仅演示交互。[设计说明](design/DESIGN.md)记录了选型与视觉要求。

当前GUI识图使用可配置的Responses API，不再依赖找到Codex CLI。固定使用`gpt-6-luna / medium`，无需先读取模型列表。密钥用Windows加密本地保存，也可从当前用户环境读取。截图入口明确为打开导入面板，粘贴/拖放均在面板内进行。固定提示词和JSON schema会提取Base/Free、Symbol、赔付表及首转核验清单，完整结果带入档案并可查看JSON。下述v0.2.x识图说明为历史记录。

v0.2.3：“选择规则截图”打开本地图片导入面板，支持文件拖入、Ctrl+V粘贴截图/复制的图片文件、连续追加、预览、移除和去重。最多12张、单张20MiB；图片先缓存本地，只有用户点击识图后才发送给Codex。识图默认显式指定`gpt-6-luna`与`model_reasoning_effort="low"`，不再使用不确定的CLI默认模型，不自动升级到其他模型。Luna在官方指南中定位为经济型提取模型，本地目录支持图片且最低深度为low；未取得所有模型的账户扣额对照，不宣称已证明绝对最低费用。

v0.2.2修复UID获取过于依赖单一字段的问题：兼容`userId`/`appId`及Android用户空间，并依次回退到`cmd package list packages -U`与`pm list packages -U`。前台活动与焦点窗口交叉检查；失败保留设备与包名、退出“检测中”。检测命令、返回码和输出摘要写入本地`data/diagnostics`；可在连接详情点击“打开检测诊断”。尚未取得用户现场失败的原始返回，具体原因以新诊断为准。

首页默认提供“自动检测并连接”：查找ADB、枚举在线模拟器、读取前台游戏包名、按应用UID筛选实时IPv4连接，再被动探测游戏心跳。检测结果常驻展示，四个技术参数移入可展开的“连接详情 / 手动修正”。单一结果自动采用；多个设备或多个心跳匹配结果由用户选择。找不到时给出明确原因，可手动修正。程序不使用上次IP充当本次检测结果。

候选连接可能在检测期间断开：启动前已过期的候选会跳过，不会误报“抓包进程未收尾”或丢弃此前的心跳匹配。未检测到心跳、连接变化、探测未安全停止各有独立提示；只有多个可用连接需要选择时才显示选择入口。

图形版入口按“打开机台→连接与心跳→规则确认→首转→正式采集”组织。规则可手填或通过Codex CLI识图生成草稿；机台档案可复用。首转单独归档，已验证档案可跳过。正式采集只填轮数，Bet从本批次首个有效付费结果读取后锁定。

机台列表支持“删除选中档案”及“撤销删除”。确认名称后仅将档案移入本地`data/trash/machines`，采集历史和规则截图保留；采集任务尚在运行时禁止删除。CLI对应`machine-delete --id ID --store DIR`和`machine-restore --id ID --deleted TOKEN --store DIR`。撤销按钮恢复本次会话最近一次删除，更早的删除可通过CLI恢复。

## 现在能做什么

- 被动读取已有ADB连接和设备PCAP，解码Bole消息；不接管鼠标，不发送旋转、领奖或改下注请求。
- 米亚适配器区分付费/免费和末局免费计数为0的情况，检查Bet和付费上限。
- 到上限发出“停止新增付费”事件，继续等完整奖励链；它不能替玩家停止自动旋转。
- 转盘、Pick、免费整链、历史地图终局分别归属；收据按时间、金额、类型唯一使用，余额不计单次奖励。
- 检查已回报线奖金额，不声称独立穷举所有线路、恢复服务器概率或支持其他机台数学规则。
- 状态有更新时间及进程身份；旧文件不显示为在线。仅能停止本任务PID、启动时间和完整命令匹配的进程。
- JSON接口、事件游标、最长60秒的等待接口；原始数据和详细审计保存在本地，不重复送进模型上下文。

当前只支持`mia-bole-v1`；其他机台不能仅换名称就套用。设备ADB、已有抓包权限、tcpdump、目标包名及当前服务器IPv4仍是外部前置依赖。不会修改root、代理或证书。

## 文件与运行

构建后双击 `dist/slot-capture-gui.exe` 打开图形界面。请将它与同一次构建的 `slot-capture.exe` 放在同一目录。两者均不依赖外部Python；图形版需要Microsoft Edge WebView2 Runtime和.NET Framework（本机已检查到WebView2 Runtime）。连接设备仍需要ADB及设备上的tcpdump。可选截图识别需要已登录的Codex CLI。

界面提供建档、规则录入、截图导入与识图、自动连接、首转、正式采集、打开历史批次与导出。看板显示付费/目标、免费次数、Bet、暂计/最终返奖和问题数；事件悬停可查看完整字段。首转不进入正式批次数字；返奖在完整尾链审计前仍可能变化。没有数据时显示空状态。

启动GUI不会自动检测设备、抓包或调用模型。点击“自动检测并连接”“开始首转监听”“开始采集”才启动相应监听。关闭GUI不停止后台任务，重新打开会恢复最近批次；停止监听只控制抓包，不停止游戏。`slot-capture-gui.exe --run DIR` 可直接查看指定批次。已有CLI调用方式保持一致。

```powershell
$capture = 'E:\AI\tools\slot-capture\dist\slot-capture.exe'
& $capture schema
& $capture init --run E:\AI\captures\mia-next --bet 13500000 --paid 100 `
  --adb D:\leidian\LDPlayer14\adb.exe --device emulator-5554 `
  --game-ip CONFIRMED_CURRENT_IPV4 --package VERIFIED_GAME_PACKAGE
& $capture doctor --plan E:\AI\captures\mia-next\plan.json
& $capture start --run E:\AI\captures\mia-next
& $capture status --run E:\AI\captures\mia-next
& $capture events --run E:\AI\captures\mia-next --after 0 --limit 20
& $capture wait --run E:\AI\captures\mia-next --after 3 --timeout 30
& $capture stop --run E:\AI\captures\mia-next
& $capture audit --run E:\AI\captures\mia-next
& $capture export --run E:\AI\captures\mia-next --out E:\AI\capture-exports\mia-next
```

示例中的IP和包名是占位符，需要根据当前设备确认。不要照搬旧采样的代理地址、UID或PID。

`init`只生成计划。`start`才启动被动抓包；程序不会替用户设置Bet或启动游戏。`stop`请求等待尾链后停止采集，`stop --force`立即收尾并标为不完整。停止采集不等于停止游戏。

## CLI输出契约

正常命令stdout是一行UTF-8 JSON：`{"ok":true,"api_version":1,"data":{...}}`。错误为`ok:false`和稳定的`error.code`；`--help`是面向人的文字。内部worker详细日志只写到运行目录。

| 命令 | 用途 |
|---|---|
| `schema` | 输出命令、状态、退出码契约 |
| `validate --plan FILE` | 检查Bet、局数、预算、时限及适配器 |
| `doctor --plan FILE` | 检查设备、包UID、目标IPv4连接及已有tcpdump |
| `init --run DIR --bet N --paid N ...` | 建立全新运行目录和计划 |
| `start --run DIR` | 后台启动，仅允许全新PREPARED运行 |
| `status --run DIR` | 简短状态、计数、健康度；问题只列前5项 |
| `events --run DIR --after N --limit N` | 读取游标后的新事件并返回`next_cursor` |
| `wait --run DIR --after N --timeout 30` | 等新事件、结束或健康异常，最长60秒 |
| `stop --run DIR [--force]` | 控制采集进程，不操作游戏 |
| `replay --plan FILE --pcap FILE --game-ip IPV4 --out NEW_DIR` | 完整离线回放原包 |
| `replay --plan FILE --input-run OLD_DIR --out NEW_DIR` | 回放已有消息与逐转记录 |
| `audit --run DIR` | 重建账本与审计，离线运行同步更新状态 |
| `export --run DIR --out NEW_DIR` | 导出研究数据及哈希，不复制原始网络包 |

退出码：0=命令执行成功，2=参数/计划无效，3=回放或审计未完整通过，4=运行失败。`--help`与`--version`为文字；其他命令使用JSON。`status`命令本身成功不代表采集在线，调用方要看`live`与`health`。查询命令不把旧文件当成在线证据。

事件包含`cursor/event_id/time_utc/kind/severity/detail`，重启读取日志后仍去重。调用方保存最后的`next_cursor`，没有新事件就不再读取旧明细。首个真实付费匹配结果才发`first_spin_verified`；仅进程启动不能证明已读到旋转。

## 固定判断与停止规则

| 条件 | 本地处理 |
|---|---|
| 计划金额、局数或预算不合法 | 拒绝启动 |
| UID与当前配置IPv4不相符 | 拒绝启动，要求重新核验连接 |
| 输出目录已有数据 | 拒绝覆盖；不静默恢复或混池 |
| 活跃文件和进程不匹配/状态过期 | `stale_or_process_missing`，不报在线 |
| 只有心跳、没有匹配旋转 | `WAITING_FOR_SPIN` |
| 付费匹配达到目标 | `DRAINING`，通知玩家不要再开付费 |
| 最后一局Free请求计数为0 | 结合响应免费状态归为Free，不占付费计数 |
| 奖励未收齐或请求未匹配 | 不完成，继续等尾链 |
| TCP帧仅临时不完整 | 等后续字节；不把单次轮询半帧当永久缺包 |
| 目标消息解码失败、Bet偏离、局数超限、未知奖励结构 | 记录关键异常，保留数据，提醒暂停新增付费并收尾 |
| 奖励链完整且游戏相关消息静默达到quiet_seconds | 再停本任务tcpdump，拉取末包，严格审计，成功才COMPLETE |
| 心跳持续但奖励链已完整 | 心跳不阻止收尾，静默时钟只看目标游戏消息 |
| 采集超时、尾链超时、原包大小超限 | 不擅自延长或增加付费，归档为不完整 |
| 远端PID命令与清单不一致 | 拒绝发送信号，不killall或结束无关ADB |
| 旧文件、当前模拟器状态或新机台规则未知 | 返回明确状态/不支持项，交回调用方判断 |

状态：`PREPARED → STARTING → WAITING_FOR_SPIN/CAPTURING → DRAINING → COMPLETE`。非正常结束使用`STOPPED_EARLY/STOPPED_INCOMPLETE/FAILED`，不能当完成样本。

## 运行目录

计划`plan.json`，所有权`manifest.json`，短状态`status.json`，增量事件`events.jsonl`，原始包`raw.pcap/raw-final.pcap`，原消息`messages.jsonl`，逐转`messages.jsonl.spins.jsonl`，完整审计`audit.json`，周期账本`paid-cycles.json`，特殊事件`feature-events.json`，诊断日志`worker.log/tcpdump.log`。

每轮默认3600秒、尾链等待120秒、目标游戏消息静默5秒、PCAP上限256MiB。计划中均可调整。当前使用不轮转单文件，避免环形覆盖；轮询需重复读取设备文件，超大长时间采集后续再增加分段传输适配。

独立净亏损停止线可用`init --max-net-loss N`或计划中的`max_net_loss`配置，默认null表示不另设。按已完整结算周期计算，触发后要求暂停新增付费并进入收尾；它不能从游戏客户端阻止后续扣款。

PCAP支持IPv4、Ethernet/SLL/SLL2、Bole长度帧；PCAPNG、IPv6、IP分片、TCP序号回绕及未见Free重触发/多结果批次未承诺支持。未知状态明确阻断，不能硬算概率。

## 测试与构建

```powershell
& .venv\Scripts\python.exe -m unittest discover -s tests -v
& .venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --name slot-capture `
  --paths src --collect-submodules dpkt --distpath dist --workpath build --specpath packaging launcher.py
```

测试使用已有500付费、30免费数据的原包和已解码消息，检查金额、计数、完整尾链、领奖去重；另外用假ADB模拟后台生命周期、持续网络活动时收尾和PID不匹配拒绝终止。它们不连接模拟器、不发游戏请求。

源代码回归通过不等于当前设备现场验证。本版本只在历史回放和假ADB上验收实时生命周期；下一次用户正式采样时仍须核实当前目标与首转。

## 扩展接口

协议层在`vendor/slot_capture.py`与`bole_value_codec.py`；机台规则在`audit.py`；状态/事件在`common.py`；进程与ADB在`runtime.py`；外部接口在`cli.py`。

新增机台先实现独立adapter及回放用例，再将它加入计划白名单。不要用JSON随意指定Python模块或执行shell回调。后续GUI、任务调度器或其他CLI直接启动EXE、读取JSON和事件游标即可，不依赖模型解释日志。当前未开放网络服务，也没有自动下注接口。
