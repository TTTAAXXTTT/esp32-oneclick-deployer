# v1.1.2 — 修复官方更新的 HTTPS 证书与代理下载

部分 Windows 机器点击「检查更新」或「下载并准备更新」时，旧版提示 `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`。更新下载已中止，当前固件未被替换。

## 修复

- 随便携软件提供 `certifi 2026.10.23` 的 Mozilla 公共 CA 证书，并与 Windows 系统信任库合并使用；已由系统信任的公司代理证书仍可使用。
- 保持证书链和主机名验证。过期、域名不符和未知签发者证书仍然拒绝，不提供「忽略证书」方式。
- 证书资源延迟加载：资源损坏时报告更新失败，GUI 与当前固件仍可用；给出系统日期、根证书及代理的检查建议。
- 修复 Arduino CLI 1.5.1 未沿用 HTTPS 代理而导致工具链直连超时的问题；只在编译工具子进程环境中传递配置，不把代理凭据写入缓存配置，日志与失败提示会隐藏代理认证信息。
- 下载编译依赖前检查 Windows 缓存目录；Xtensa 链接器不支持中文等非 ASCII 路径，不能通过短路径别名解决。现在会提前提示，并支持通过 `ESP32_ONECLICK_UPDATE_CACHE` 选择英文缓存目录，避免等待到编译末尾才失败。
- 源码构建输入下载也使用相同的验证 HTTPS 传输。
- 增加冻结 EXE 的 CA 自检、`--check-update` 真实联网检查及 `--prepare-update` 独立缓存完整构建诊断入口，以及 Windows 自动测试和打包工作流。

## 验证方法

98 项自动化测试覆盖原有固件校验、串口/GUI 状态、取消、失败保留当前固件，以及真实本地 HTTPS 握手、缺失系统根证书、过期证书、错误域名、未知签发者、HTTPS 降级重定向、源码输入完整性、代理传递、独立编译缓存和 Windows 编译路径检查。

```powershell
python -m unittest discover -s tests -v
.\ESP32-OneClick.exe --self-test self-test.json
.\ESP32-OneClick.exe --check-update update-check.json
```

测试和更新准备均不自动刷板。新增修复不更换随包的默认固件，固件结构与 SHA-256 校验继续启用。详细的本次本机验证结果随发布附件提供；真实开发板刷写及手机控制流程应另外验收。

## 升级

完整解压新版 `ESP32-OneClick-N16R8-Windows-x64-v1.1.2.zip` 到一个新目录，运行其中的 EXE，不要仅复制 EXE 覆盖旧目录。无需重新刷写才能修复 Windows 工具自身的下载问题。

代理若签发了系统不信任的证书，新版仍会安全地终止下载。此时应修正系统信任与网络配置，而不是关闭验证。

原作者与许可保持不变，见 README 和 licenses/。源码版的首次输入准备复用既有 v1.1.1 中按清单固定的未修改固件与 Arduino CLI；本发布不声称它们是重新编译的默认固件。
