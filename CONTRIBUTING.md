# 开发与提交约定

## 日常改动

小功能、修复、样式调整直接用Git提交记录，不递增版本、不新建版本目录、不自动打tag。当前完整界面改版经用户指定为v0.3.0，后续小修保持该版本。

默认构建固定输出 `dist/slot-capture.exe` 和 `dist/slot-capture-gui.exe`。需要更新时由用户关闭旧程序后重新构建；不擅自结束用户运行中的程序。不自动打开GUI、操作鼠标、读取剪贴板、连接模拟器或调用模型进行现场验收，现场测试由用户完成。

只有用户明确要求正式发布时，才统一更新版本号并创建对应tag或GitHub Release。

## 源码与数据

提交源码、文档、构建配置和测试。`data/`、运行数据、截图、原包、诊断、虚拟环境、打包产物、认证文件不入库。构建产物需要分发时单独作为发布附件处理。

## 检查

开发环境安装：

```powershell
python -m venv .venv
& .venv/Scripts/python.exe -m pip install -e .
& .venv/Scripts/python.exe -m pip install -r requirements-build.txt
& .venv/Scripts/python.exe -m unittest discover -s tests -v
```

默认测试不创建GUI窗口、不连接真实设备、不读取系统剪贴板、不发模型请求；历史数据回归在私有样本缺失时跳过。GUI验收测试需要人工明确设置 `SLOT_CAPTURE_GUI_TESTS=1`，且依赖本地历史样本与已打包程序。

可选 `python design/check_desktop.py` 使用独立、无界面的Edge上下文和假的桌面API测试正式页面，不能替代真实桌面壳与设备验收。`requirements-design.txt` 只供离屏验证，不随正式程序启动。

测试通过后检查 `git diff --check`、暂存文件清单和忽略规则，再提交。提交说明写清具体行为变化。
