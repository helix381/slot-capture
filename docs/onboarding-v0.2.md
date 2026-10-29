# 机台采集主流程 v0.2

本版本落实用户确认的操作流程；后续异常交互和视觉设计另议。不自动操作游戏。

1. 首页先提示在模拟器里打开目标机台。选已有档案，或建立新机台。
2. 点击“自动检测并连接”。程序查找ADB与在线设备、读取前台应用，再按UID筛选当前连接；最多8个候选会逐个被动探测，每个最多12秒。唯一心跳匹配自动回填；多个设备或多个匹配连接需要选择。随后启动最多60秒的正式连接核验，无需旋转。检测结果展示在首页，技术参数可展开查看、手动修正。连接成功不证明当前具体机台身份。
3. 检测期间可手动录入规则，或点击选择规则截图，打开支持拖拽、Ctrl+V粘贴、文件选择、预览与移除的图片导入面板。图片只缓存本地；点击识图时才调用Codex CLI，使用CLI登录与额度。默认显式指定gpt-6-luna与最低思考low，可手动改模型但失败不会自动换贵模型。GUI不读取或复制凭据，不需要桌面窗口保持运行。
4. 列出Symbol的名称、类型、占格、说明、可空协议ID；列出可能的特殊玩法。用户逐项编辑、确认并保存。识图只生成草稿，不推断协议ID、后台概率或未见规则。
5. 档案保存规则、截图副本、设备配置与验证记录。新机台没有适配器时可建档，但不能套用米亚数学规则采集。
6. 首转：需规则确认和最近10分钟的连接证据。先点击开始监听，再由用户在游戏里Spin一次。程序自动读取Bet、等完整奖励链及静默后，停本任务抓包、严格审计。成功后自动把证据写入机台档案。
7. 首转与正式采集分目录，首转不消耗正式目标轮数。通过首转的档案，在相同规则与游戏包名下可以跳过首转；规则、适配器或包名改变会清除验证资格。仅连接地址、ADB路径或设备变化不会清除规则验证，但必须重新检测本次环境。更改机台名称也会保守地使验证失效。
8. 正式采集只填付费轮数。该批次的首个有效付费结果读取Bet并锁定本批次；免费局另计。上轮Bet不会当作当前Bet。之后切档仍会产生偏离异常，不会混池。
9. 达到付费目标提醒手动停止新增付费；继续监听奖励尾链，最后审计与归档。关闭窗口不会停止后台采集。

## 程序接口

GUI与CLI共享本地文件和规则。日常构建固定输出 `dist/slot-capture.exe` 和 `dist/slot-capture-gui.exe`，新库默认在 `dist/data/`。若此前已有版本目录中的机台档案，则原地沿用该库，不自动复制原包。CLI可用 `--store` 指向其他档案库。小改动以Git提交记录，正式发布才更新版本号。

- `discover --store DIR`：自动发现并探测心跳；可选 `--adb EXE` 和 `--device SERIAL` 限定设备。`--package PACKAGE` 是明确的手动覆盖，GUI自动入口不会传旧包名。`--no-probe` 仅列出连接，不宣称心跳验证通过。

- `machine-list --store DIR`
- `machine-save --input FILE --store DIR`
- `machine-show --id ID --store DIR`
- `session-prepare --id ID --purpose connection --run NEW_DIR --store DIR`
- `session-prepare --id ID --purpose first_spin --connection CONNECTION_RUN --run NEW_DIR --store DIR`
- `session-prepare --id ID --purpose sampling --paid 100 --connection CONNECTION_RUN --run NEW_DIR --store DIR`
- `start --run DIR`：上述prepare只创建计划，GUI在用户点击后再调用start。
- `vision-status [--codex EXE]`
- `vision-read --image FILE [--image FILE] [--model NAME] [--codex EXE] --store DIR`

原 `status/events/wait/stop/audit/export/replay` 接口保持可用。CLI JSON契约版本仍为1。

## 测试边界

回归仅使用历史原包、合成通信和假ADB，不操作真实模拟器。Codex识图适配器使用模拟返回验证调用参数与草稿流程，没有发起真实模型调用；具体账号、模型可用性和GUI现场操作由用户测试。
