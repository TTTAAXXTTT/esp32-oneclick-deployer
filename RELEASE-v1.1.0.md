# ESP32 一键部署 v1.1.0 · Windows x64 / N16R8

为 **ESP32-S3 N16R8（16 MB Flash / 8 MB OPI PSRAM）** 提供 Windows 图形部署工具：完整解压即可运行，内置固件可离线刷写。v1.1.0 新增官方固件检查和下载构建功能，并保留 v1.0.1 的下拉菜单崩溃修复。

![ESP32 一键部署 v1.1.0 软件界面](https://raw.githubusercontent.com/TTTAAXXTTT/esp32-oneclick-deployer/v1.1.0/docs/windows-deployer-v1.1.0.jpg)

## 下载附件

- **`ESP32-OneClick-N16R8-Windows-x64-v1.1.0.zip`**：Windows 10/11 x64 便携软件，包含运行环境、内置固件、许可证和对应源码。
- **`ESP32-OneClick-Source-v1.1.0.zip`**：完整源码包，用于修改和重新构建。
- **`SHA256SUMS.txt`**：发布附件的 SHA-256 校验值。

请完整解压便携包，保留 `_internal` 等附带目录，再运行 `ESP32-OneClick.exe`。完整工具源码、测试、构建脚本和对应固件源码均直接位于本独立仓库。完整源码附件包含两个预编译构建输入，可按 `SOURCE-BUILD.md` 重新构建。

## 怎么使用

![从电脑刷写到接回 iPhone 的部署流程](https://raw.githubusercontent.com/TTTAAXXTTT/esp32-oneclick-deployer/v1.1.0/docs/windows-deployer-flow.svg?revision=maintainer-refresh)

1. 用数据线将开发板 **USB/OTG** 口接电脑，刷新并选择 COM 端口。
2. 确认可以覆盖板上的旧程序和配置，点击「一键部署」。
3. 等待成功提示和 `Hash of data verified.` 校验结果，按一下 **RESET/EN**。
4. 将 **USB/OTG** 接回已解锁的 iPhone，打开已安装的 ESP32 Agent，继续配对和设置。

手机 App 需要单独安装和更新；本工具负责开发板固件。详细操作、下载模式和常见问题见[中文图文指南](https://github.com/TTTAAXXTTT/esp32-oneclick-deployer/blob/v1.1.0/docs/GUIDE.zh-CN.md)。

## 新增固件更新

默认启动时检查 [原作者仓库](https://github.com/onehumanbeing/esp32-iphone-agent) 的 `main` 分支，并在保持打开时每 6 小时再检查。工具固定官方提交并比较固件目录，避免仅文档或手机 App 更新导致误报。

发现更新后，点击「下载并准备更新」，会下载对应源码并按 N16R8 配置在本机编译、校验；完成后再点击「一键部署」。**首次约需下载 2 GB 工具链并占用数 GB 磁盘空间**，后续复用依赖。检查和准备更新本身不会刷写开发板，失败时仍可使用已有固件。

固定依赖为 Arduino CLI 1.5.1、ESP32 core 3.3.12、NimBLE-Arduino 2.5.1，沿用 `nimble_cfg.h`。官方未来若调整协议、依赖或分区格式，可能需要同步升级本工具或手机 App。

## 已验证与当前限制

通过 **68 项自动化测试、独立 EXE 自检和界面检查**；实际官方更新检查正常，发布时内置固件源码与官方一致。内置固件已有 N16R8 实板写入、哈希校验成功记录。

**完整的新版下载、工具链安装、编译和刷写更新流程尚未做完整实测。** 手机 Agent 的蓝牙、镜像和模型操作闭环需在用户设备上继续完成。

## 致谢与开源

原始项目由 **Henry（[@iamhenryyuan](https://x.com/iamhenryyuan) / [onehumanbeing](https://github.com/onehumanbeing)）** 开发：[onehumanbeing/esp32-iphone-agent](https://github.com/onehumanbeing/esp32-iphone-agent)。Windows 图形工具、便携打包和更新功能由 **TTTAAXXTTT 的独立项目 esp32-oneclick-deployer** 维护。本发布不代表原作者官方发布或背书。

原项目保留 MIT 声明；AirPlay 部分及链接后的完整固件按 GPL-3.0 分发。本工具为 GPL-3.0，esptool 为 GPL-2.0-or-later，Arduino CLI 为 GPL-3.0。附件包含许可证与对应源码，请在重新分发时一并保留。
