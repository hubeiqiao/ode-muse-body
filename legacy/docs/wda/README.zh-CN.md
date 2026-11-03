# 可选 WebDriverAgent 实验

主演示使用 ESP32 蓝牙和 AirPlay，不依赖 WDA。此工具只在另行授权的原生实验中使用。提供干净、固定到完整 commit 的本地 Appium WebDriverAgent checkout 后，可用 `legacy/tools/wda/vendor.sh --source <path> --commit <40-char-sha>` 准备忽略目录。BSD-3-Clause LICENSE 会随该外部源码保留。

构建必须设置自己的 `APPLE_DEVELOPMENT_TEAM`，可选 `APPLE_CODE_SIGN_IDENTITY`（默认 `Apple Development`）。运行必须明确设置 `WDA_DEVICE_UDID`；型号、配对、连接和开发者模式检查均需通过。当前只支持 `iPad14,1`。可通过 `WDA_DENIED_UDIDS` 设置本地逗号分隔的拒绝清单，不内置任何所有者设备标识。

`legacy/tools/wda/build.sh` 和 `run.sh` 使用 `WDA_PYTHON`，未设置时从 PATH 找 `python3`。不要提交外部构建、设备清单、绑定、密钥或日志。完整配置见 [SETUP.md](../SETUP.md)。
