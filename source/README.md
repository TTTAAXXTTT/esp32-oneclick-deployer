# 随包源码

- `deployer/`：图形工具、刷写后端、测试和 Windows 打包脚本。
- `firmware/`：完整固件 sketch、构建脚本和精确依赖/编译参数。
- `third-party/esptool-5.1.0.tar.gz`：使用的 esptool 原始源码包。
- 许可证和第三方声明位于便携包根目录的 `_internal/licenses/`。

重新构建 GUI 时，将 `deployer/` 复制到新的工作目录；从便携包的
`_internal/` 复制 `firmware/` 和 `licenses/` 到该工作目录，再将本目录完整
复制成该工作目录中的 `source/`。创建带 Tkinter 的 Python 3.12 虚拟环境，
安装 `requirements.txt`，然后运行 `build.ps1 -Python <虚拟环境的python.exe>`。

从固件源码重新编译的方法见 `firmware/BUILD.md`。将新编译的完整合并镜像
放到 GUI 工作目录的 `firmware/agent-n16r8.bin`，并同步更新 manifest 的
SHA256、长度与来源信息，再重新打包。

本版本只接受 ESP32-S3 N16R8（16 MB Flash、8 MB PSRAM）的完整 16 MB 镜像。
