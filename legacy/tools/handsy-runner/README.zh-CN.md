# HandsyRunner（可选原生实验）

此目录保留独立的 XCTest 协议实验，并非 ESP32 蓝牙演示的必要组件。默认不要启动设备。离线协议测试使用 Swift doubles，不枚举或访问真实设备。

原生构建前设置自己的 `APPLE_DEVELOPMENT_TEAM` 和明确选择的 `WDA_DEVICE_UDID`，并完成 Xcode 配对及开发者模式设置。当前型号检查只接受 `iPad14,1`；脚本不会按名称或第一行选择设备。项目 bundle IDs 为 `com.example.handsy.*`，可按本地签名需要调整。`build.sh` 会请求 provisioning updates/device registration，只有明确授权的原生实验才可运行。构建目录为当前仓库的忽略目录 `work/handsy-runner-build`。

`legacy/tools/handsy-runner/build.sh` 构建，`run.sh` 使用已有构建。停止使用 Ctrl-C。签名、设备标识、日志和构建产物不得提交。原生实验的完整设置未随此快照提供；本次投稿的范围见 [归档说明](../../README.md)。
