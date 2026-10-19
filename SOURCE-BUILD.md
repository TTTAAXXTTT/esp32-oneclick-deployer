# ESP32 OneClick v1.1.0 完整源码包

本项目是 WS08 独立维护的 Windows 一键部署工具 v1.1.0 的完整构建工作目录。GUI、刷写后端、更新模块、测试、打包脚本与已发布便携版中的源码一致。

原作者与上游项目：[onehumanbeing/esp32-iphone-agent](https://github.com/onehumanbeing/esp32-iphone-agent)。本工具是由 TTTAAXXTTT（原 WS08）维护的 Windows 部署辅助工具，保留上游的原始著作权与许可证声明；上游作者并未为本工具背书。

## 从源码构建 Windows 软件

1. 在 Windows x64 安装带 Tkinter 的 Python 3.12（支持 Python 3.10+）。
2. 克隆本仓库，或解压完整源码包，在根目录打开 PowerShell。仓库下载版本先运行 `python prepare-build-inputs.py`；完整源码附件无需此步。
3. 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

输出为 `dist/ESP32-OneClick/ESP32-OneClick.exe` 和同目录的完整便携 ZIP。构建不会连接串口或刷写开发板。Python 依赖首次安装需要联网；完成依赖安装后，随包构建输入已齐全。

必须保留本目录的 `firmware/`、`tools/`、`licenses/` 和 `source/`。完整源码 Release 附件已包含 16 MB 的默认固件和未修改的官方 `tools/arduino-cli.exe`，无需从便携版另行复制。

测试运行方法：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用模拟串口与子进程，不会刷写真实开发板。

## 固件来源与精确目标

- 上游：<https://github.com/onehumanbeing/esp32-iphone-agent>
- 随包固件对应源码：<https://github.com/onehumanbeing/esp32-iphone-agent/tree/bae786f7b3d04d9a2feb4fe2ef398b3f169bdd02>
- 源提交：`bae786f7b3d04d9a2feb4fe2ef398b3f169bdd02`
- 目标：ESP32-S3 N16R8，16 MB Flash / 8 MB OPI PSRAM。
- FQBN：`esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=cdc,FlashSize=16M,PSRAM=opi,PartitionScheme=default_8MB`
- 镜像 SHA-256：`ca5605b4a96997121be8b993f2088aa5d43af3356f72ef25fe384ab1fae0c001`
- 镜像长度：16,777,216 字节，合并镜像从地址 `0x0` 写入。

完整首选固件源码、原始声明和精确编译参数位于 `source/firmware/`。默认固件原构建记录为 Arduino CLI 1.3.1、Arduino ESP32 core 3.3.12、NimBLE-Arduino 2.5.1。v1.1.0 的在线更新构建模块使用随包 Arduino CLI 1.5.1，并固定相同版本 core 与 NimBLE；两者不要混淆。官方未来源码变化可能要求新版部署工具。

`source/firmware/BUILD.md` 保留原便携包的路径描述；在本项目中，许可证实际位于根目录 `licenses/`，固件 manifest 位于根目录 `firmware/manifest.json`。原 `source/README.md` 是便携包内的重建说明；本源码包直接按照本文构建即可，不需要复制目录。

## 对应源码、许可证与工具

- 本工具源码：GNU GPL version 3，见 `LICENSE`。
- 固件：保留上游 MIT 声明；AirPlay 部分及相应完整固件按 GPL-3.0 分发，见 `licenses/FIRMWARE-GPL-NOTICE.md` 和 `source/firmware/` 原始声明。
- esptool 5.1.0 原始源码：`source/third-party/esptool-5.1.0.tar.gz`，GPL-2.0-or-later。
- Arduino CLI 1.5.1 原始源码：`source/third-party/arduino-cli-1.5.1.tar.gz`，GPL-3.0-only。构建方法见同目录 README；EXE 来源、长度与哈希见 `tools/manifest.json`。
- Python 与依赖的原始许可、元数据和已用版本位于 `licenses/third-party/`；Arduino CLI 的原始依赖通知位于 `licenses/arduino-cli/` 与其源码归档。
- `SOURCE-INVENTORY.json` 和 `SHA256SUMS.source.txt` 记录交付文件的 SHA-256；清单本身不递归包含自己的哈希。

本包不包含用户 API Key、Apple ID、Wi-Fi 配置、设备备份或运行日志。`secrets.example.h` 仅是上游的占位模板。
