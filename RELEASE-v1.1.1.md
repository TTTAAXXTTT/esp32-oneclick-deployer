# ESP32 OneClick Deployer v1.1.1 · 作者信息版

**作者QQ：230154231**

Windows 部署工具维护：**TTTAAXXTTT**。这个 QQ 属于本工具维护者；原始 ESP32 iPhone Agent 项目由 Henry / onehumanbeing 开发。

## 本版更新

- 启动软件时显示作者信息弹窗，包含作者 QQ、工具维护者及原始项目来源。
- 主界面底部常驻显示「作者QQ：230154231」，点击「关于作者」可再次查看。
- README、图文指南、构建说明、流程图及随包说明同步补充作者联系方式。
- 保留串口识别、固件校验、刷写进度和官方固件源码更新功能。

![软件主界面](https://raw.githubusercontent.com/TTTAAXXTTT/esp32-oneclick-deployer/v1.1.1/docs/windows-deployer-v1.1.1.png)

## 下载与使用

| 附件 | 用途 |
| --- | --- |
| `ESP32-OneClick-N16R8-Windows-x64-v1.1.1.zip` | Windows 10/11 x64 完整便携软件 |
| `ESP32-OneClick-Source-v1.1.1.zip` | 完整源码、许可证和构建输入 |
| `SHA256SUMS.txt` | 两个 ZIP 的 SHA-256 校验值 |

完整解压便携 ZIP，保留 `_internal` 等所有配套目录，再运行 `ESP32-OneClick.exe`。不能只复制 EXE。开发板使用 **USB/OTG** 接电脑；点击「一键部署」会覆盖板上程序和配置。刷写并校验完成后按 RESET/EN，再接回已解锁的 iPhone。

![部署流程](https://raw.githubusercontent.com/TTTAAXXTTT/esp32-oneclick-deployer/v1.1.1/docs/windows-deployer-flow.svg)

[中文图文指南](https://github.com/TTTAAXXTTT/esp32-oneclick-deployer/blob/v1.1.1/docs/GUIDE.zh-CN.md) · [源码构建说明](https://github.com/TTTAAXXTTT/esp32-oneclick-deployer/blob/v1.1.1/SOURCE-BUILD.md)

## 验证与限制

本版沿用已验证的 N16R8 内置固件与固定构建依赖。通过 68 项自动化测试，并检查 Windows 窗口及冻结 EXE 自检。作者信息更新不会自动刷写开发板。

官方固件更新的完整下载、工具链安装、编译和实板写入流程仍未完成完整实测。首次准备更新约需下载 2 GB 工具链；手机 App 的蓝牙、镜像和模型操作闭环需在实际设备上验证。

## 原作者与开源

感谢 **Henry（[onehumanbeing](https://github.com/onehumanbeing) / [@iamhenryyuan](https://x.com/iamhenryyuan)）** 提供原始项目：[onehumanbeing/esp32-iphone-agent](https://github.com/onehumanbeing/esp32-iphone-agent)。本独立 Windows 工具由 TTTAAXXTTT 维护，不代表原作者官方发布或背书。

保留原始 MIT、GPL 及第三方许可证声明。工具按 GPL-3.0 提供，AirPlay 部分及相应完整固件按 GPL-3.0 分发；esptool 为 GPL-2.0-or-later，Arduino CLI 为 GPL-3.0。重新分发时请一并保留许可证和对应源码。
