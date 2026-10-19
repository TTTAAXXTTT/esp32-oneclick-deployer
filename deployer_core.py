"""Bounded, hardware-checked ESP32-S3 N16R8 deployment backend.

Public operations are synchronous: call them from a GUI worker thread. All
esptool work runs in a separate invocation of the application, including frozen
windowed executables. The worker writes UTF-8 output to a temporary file because
Windows GUI executables do not reliably have stdout/stderr handles.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Sequence


FLASH_BYTES = 16 * 1024 * 1024
ESP32S3_CHIP_ID = 9
CANDIDATE_VIDS = frozenset({0x303A, 0x1A86, 0x10C4, 0x0403})
RESET_MODES = frozenset({"auto", "usb-reset", "default-reset", "no-reset"})
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_MAC = re.compile(r"\b([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b", re.I)


class DeployerError(RuntimeError):
    """An actionable deployment failure, suitable for a GUI error dialog."""


class CancelledError(DeployerError):
    pass


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str = ""
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    location: str | None = None

    @property
    def identity(self) -> tuple:
        return (self.device.casefold(), self.vid, self.pid,
                self.serial_number or "", self.location or "")

    @property
    def label(self) -> str:
        usb = (f" [{self.vid:04X}:{self.pid:04X}]"
               if self.vid is not None and self.pid is not None else "")
        return f"{self.device} — {self.description or '串口设备'}{usb}"


@dataclass(frozen=True)
class FirmwareSpec:
    path: str | Path
    sha256: str
    size: int = FLASH_BYTES
    flash_size_mb: int = 16
    chip_id: int = ESP32S3_CHIP_ID


@dataclass(frozen=True)
class DeviceInfo:
    chip: str
    flash_size_mb: int
    mac: str
    features: str
    psram_mb: int | None
    port_identity: tuple


def list_candidate_ports(include_device: str | None = None) -> list[PortInfo]:
    """List likely wired ESP ports, retaining an explicitly selected live port."""
    from serial.tools import list_ports
    selected = include_device.casefold() if include_device else None
    result = []
    for item in list_ports.comports():
        if item.vid in CANDIDATE_VIDS or item.device.casefold() == selected:
            result.append(PortInfo(item.device, item.description or "", item.vid,
                                   item.pid, item.serial_number, item.location))
    return sorted(result, key=lambda p: (int(m.group()) if (m := re.search(r"\d+", p.device))
                                        else 999999, p.device))


def choose_port(candidates: Sequence[PortInfo], selected_device: str | None = None) -> PortInfo:
    if selected_device:
        match = [p for p in candidates if p.device.casefold() == selected_device.casefold()]
        if len(match) == 1:
            return match[0]
        raise DeployerError("所选串口已断开，请刷新设备列表并重新选择。")
    if not candidates:
        raise DeployerError("没有发现开发板串口，请使用 USB 数据线连接 UART/COM 或 USB/OTG 口。")
    if len(candidates) != 1:
        raise DeployerError("发现多个串口，请明确选择要部署的开发板。")
    return candidates[0]


def reset_mode_for(port: PortInfo, requested: str = "auto") -> str:
    if requested not in RESET_MODES:
        raise DeployerError("不支持的下载模式。")
    if requested != "auto":
        return requested
    return "usb-reset" if (port.vid, port.pid) == (0x303A, 0x1001) else "default-reset"


def default_command_prefix() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(sys.argv[0]).resolve())]


def _check_device_values(chip: str, flash_mb: int, mac: str,
                         features: str) -> tuple[str, int]:
    if not re.fullmatch(r"ESP32-S3(?:\s.*)?", chip, re.I):
        raise DeployerError("芯片不匹配：本工具只支持 ESP32-S3 N16R8。")
    if flash_mb != 16:
        raise DeployerError(f"Flash 容量不匹配：检测到 {flash_mb} MB，需要 16 MB。")
    psram = re.search(r"\bPSRAM\s+(\d+)\s*MB\b", features, re.I)
    if not psram or int(psram.group(1)) != 8:
        raise DeployerError("无法确认 8 MB PSRAM，已停止；请确认开发板型号为 N16R8。")
    found_mac = _MAC.fullmatch(mac.strip())
    if not found_mac or mac.lower() in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}:
        raise DeployerError("未能读取有效芯片身份，已停止部署。")
    return mac.lower(), 8


def parse_device_output(output: str, port: PortInfo) -> DeviceInfo:
    plain = _ANSI.sub("", output).replace("\r", "\n")
    chip = re.search(r"^(?:Chip type|Chip is):?\s*(.+?)\s*$", plain, re.M)
    features = re.search(r"^Features:\s*(.+?)\s*$", plain, re.M)
    size = re.search(r"Detected flash size:\s*(\d+)\s*MB\b", plain, re.I)
    mac_line = re.search(r"^MAC:\s*(.+?)\s*$", plain, re.M | re.I)
    if not (chip and features and size and mac_line):
        raise DeployerError("无法完整读取芯片、Flash、PSRAM 或 MAC；没有写入任何固件。")
    chip_text, feature_text = chip.group(1), features.group(1)
    mac, psram_mb = _check_device_values(chip_text, int(size.group(1)),
                                        mac_line.group(1), feature_text)
    return DeviceInfo(chip_text, int(size.group(1)), mac, feature_text,
                      psram_mb, port.identity)


def validate_firmware(spec: FirmwareSpec) -> None:
    """Check the exact approved merged image, including both image headers."""
    if spec.size != FLASH_BYTES or spec.flash_size_mb != 16 or spec.chip_id != ESP32S3_CHIP_ID:
        raise DeployerError("固件清单不匹配：只接受 ESP32-S3 的 16 MB 合并镜像。")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", spec.sha256):
        raise DeployerError("固件 SHA-256 清单无效。")
    path = Path(spec.path)
    try:
        with path.open("rb") as source:
            if os.fstat(source.fileno()).st_size != spec.size:
                raise DeployerError("固件长度不匹配：必须是完整的 16 MB 合并镜像。")
            hasher = hashlib.sha256()
            while block := source.read(1024 * 1024):
                hasher.update(block)
            if hasher.hexdigest() != spec.sha256.lower():
                raise DeployerError("固件 SHA-256 校验失败，请重新下载完整部署包。")
            for offset in (0, 0x10000):
                source.seek(offset)
                header = source.read(24)
                if (len(header) != 24 or header[0] != 0xE9 or
                        not 1 <= header[1] <= 16 or header[3] >> 4 != 4 or
                        struct.unpack_from("<H", header, 12)[0] != ESP32S3_CHIP_ID):
                    raise DeployerError("固件镜像头不匹配：需要 ESP32-S3、16 MB 的完整启动和应用镜像。")
    except OSError as exc:
        raise DeployerError(f"无法读取内置固件：{exc.strerror or type(exc).__name__}") from exc


class Deployer:
    def __init__(self, spec: FirmwareSpec, command_prefix: Sequence[str] | None = None,
                 log_callback: Callable[[str], None] | None = None,
                 progress_callback: Callable[[float], None] | None = None,
                 detect_timeout: float = 55, flash_timeout: float = 600):
        self.spec = spec
        self.command_prefix = list(command_prefix) if command_prefix else default_command_prefix()
        self.log_callback = log_callback or (lambda _line: None)
        self.progress_callback = progress_callback or (lambda _value: None)
        self.detect_timeout = detect_timeout
        self.flash_timeout = flash_timeout
        self._cancel = threading.Event()
        self._operation_lock = threading.Lock()
        self._process_lock = threading.RLock()
        self._process: subprocess.Popen | None = None

    def validate_firmware(self) -> None:
        validate_firmware(self.spec)

    def cancel(self) -> None:
        """Permanently cancel this instance; create a new Deployer to retry."""
        # Set the event before taking the lock: Popen may currently be creating
        # a child, and must see this request before publishing that child.
        self._cancel.set()
        with self._process_lock:
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass

    def _port_still_present(self, port: PortInfo) -> None:
        current = choose_port(list_candidate_ports(include_device=port.device), port.device)
        if current.identity != port.identity:
            raise DeployerError("所选串口对应的设备已改变，请重新检测开发板。")

    def _enter_operation(self) -> None:
        if not self._operation_lock.acquire(blocking=False):
            raise DeployerError("当前已有部署任务正在运行。")
        if self._cancel.is_set():
            self._operation_lock.release()
            raise CancelledError("操作已取消；重试时请重新开始部署任务。")

    def detect(self, port: PortInfo, reset_mode: str = "auto") -> DeviceInfo:
        self._enter_operation()
        try:
            return self._detect(port, reset_mode)
        finally:
            self._operation_lock.release()

    def _detect(self, port: PortInfo, reset_mode: str) -> DeviceInfo:
        self._port_still_present(port)
        self.log_callback(f"正在只读检测 {port.device}；不会写入 Flash。")
        args = self._base_args(port, reset_mode, "no-reset")
        args.extend(["--no-stub", "flash-id"])
        output = self._run_esptool(args, self.detect_timeout)
        self._port_still_present(port)
        return parse_device_output(output, port)

    @staticmethod
    def _base_args(port: PortInfo, reset_mode: str, after: str) -> list[str]:
        return ["--chip", "esp32s3", "--port", port.device, "--baud", "460800",
                "--before", reset_mode_for(port, reset_mode), "--after", after,
                "--connect-attempts", "3"]

    def flash(self, port: PortInfo, expected: DeviceInfo,
              reset_mode: str = "auto") -> DeviceInfo:
        self._enter_operation()
        try:
            self.validate_firmware()
            if expected.port_identity != port.identity:
                raise DeployerError("设备选择已变化，请先检测当前开发板。")
            _check_device_values(expected.chip, expected.flash_size_mb,
                                 expected.mac, expected.features)
            self.progress_callback(0)
            current = self._detect(port, reset_mode)
            if current.mac != expected.mac or current.port_identity != expected.port_identity:
                raise DeployerError("开发板身份已变化，已停止；请重新检测当前开发板。")
            self._port_still_present(port)
            self.log_callback("芯片与固件校验通过，开始写入；请保持连接和供电。")
            args = self._base_args(port, reset_mode, "hard-reset")
            args.extend(["--guard-mac", expected.mac,
                         "--guard-firmware-sha256", self.spec.sha256,
                         "--guard-firmware-size", str(self.spec.size),
                         "write-flash", "--flash-size", "16MB", "--compress",
                         "0x0", str(Path(self.spec.path).resolve())])
            output = self._run_esptool(args, self.flash_timeout)
            if "Hash of data verified." not in _ANSI.sub("", output):
                raise DeployerError("写入进程已结束，但没有固件校验成功证据；请勿将本次部署视为成功。")
            self.progress_callback(100)
            self.log_callback("固件写入成功，Flash 内容校验通过。")
            return current
        finally:
            self._operation_lock.release()

    def _run_esptool(self, args: Sequence[str], timeout: float) -> str:
        if self._cancel.is_set():
            raise CancelledError("操作已取消。")
        descriptor, log_path = tempfile.mkstemp(prefix="esp32-deploy-", suffix=".log")
        os.close(descriptor)
        process = None
        chunks: list[str] = []
        pending = ""
        output_chars = 0
        env = os.environ.copy()
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                    "NO_COLOR": "1", "TERM": "dumb", "ESPTOOL_OPEN_PORT_ATTEMPTS": "1"})
        command = self.command_prefix + ["--esptool", "--worker-log", log_path] + list(args)

        def consume(text: str, final: bool = False) -> None:
            nonlocal pending, output_chars
            output_chars += len(text)
            if output_chars > 4 * 1024 * 1024:
                raise DeployerError("设备输出异常过多，已停止操作。")
            chunks.append(text)
            pending += text
            lines = re.split(r"[\r\n]", pending)
            pending = "" if final else lines.pop()
            for line in lines:
                clean = _ANSI.sub("", line).strip()
                if not clean:
                    continue
                self.log_callback(clean)
                if re.search(r"\bWriting at\b", clean, re.I):
                    progress = re.search(r"(\d+(?:\.\d+)?)\s*%", clean)
                    if progress:
                        self.progress_callback(min(99.0, float(progress.group(1))))

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace", newline="") as reader:
                with self._process_lock:
                    if self._cancel.is_set():
                        raise CancelledError("操作已取消。")
                    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        shell=False, env=env,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self._process = process
                    # Cancellation can arrive while Popen creates the process.
                    # Publish and stop it while still holding the same lock so
                    # cancel() cannot miss a newly created writer.
                    if self._cancel.is_set():
                        if process.poll() is None:
                            try:
                                process.terminate()
                            except OSError:
                                pass
                        raise CancelledError("操作已取消；若写入中断，需要重新完整部署。")
                deadline = time.monotonic() + timeout
                while True:
                    consume(reader.read())
                    if self._cancel.is_set():
                        raise CancelledError("操作已取消；若写入中断，需要重新完整部署。")
                    returncode = process.poll()
                    if returncode is not None:
                        consume(reader.read(), final=True)
                        if returncode != 0:
                            tail = "\n".join("".join(chunks).splitlines()[-7:]).strip()
                            raise DeployerError(f"设备操作失败（退出码 {returncode}）。" +
                                                (f"\n{tail}" if tail else "\n请查看连接、驱动和下载模式。"))
                        return "".join(chunks)
                    if time.monotonic() >= deadline:
                        raise DeployerError("设备操作超时，已停止；请检查 USB 数据线、串口占用和下载模式。")
                    time.sleep(0.05)
        except OSError as exc:
            raise DeployerError(f"无法运行固件工具：{exc.strerror or type(exc).__name__}") from exc
        finally:
            try:
                if process is not None and process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            finally:
                with self._process_lock:
                    if self._process is process:
                        self._process = None
                try:
                    os.unlink(log_path)
                except OSError:
                    pass


def _extract_option(args: list[str], name: str, required: bool = False) -> str | None:
    if args.count(name) > 1:
        raise DeployerError(f"重复的工具参数：{name}")
    if name not in args:
        if required:
            raise DeployerError(f"缺少工具参数：{name}")
        return None
    index = args.index(name)
    if index + 1 >= len(args) or args[index + 1].startswith("--"):
        raise DeployerError(f"工具参数缺少值：{name}")
    value = args[index + 1]
    del args[index:index + 2]
    return value


def _guarded_write(args: list[str], expected_mac: str, sha256: str, size: int) -> None:
    """Validate the exact connected chip and reuse that connection for writing."""
    import esptool
    copy = args.copy()
    port = _extract_option(copy, "--port", required=True)
    chip = _extract_option(copy, "--chip", required=True)
    before = _extract_option(copy, "--before", required=True)
    if chip != "esp32s3" or before not in RESET_MODES - {"auto"}:
        raise DeployerError("写入工具的芯片或下载模式参数无效。")
    # The GUI always writes one approved full image at address zero.
    if len(args) < 2 or args[-2] != "0x0":
        raise DeployerError("只允许从地址 0 写入已批准的完整合并镜像。")
    validate_firmware(FirmwareSpec(args[-1], sha256, size))
    esp = esptool.get_default_connected_device([port], port=port,
        connect_attempts=3, initial_baud=115200, chip="esp32s3", before=before)
    if esp is None:
        raise DeployerError("无法连接开发板。")
    try:
        if esp.secure_download_mode:
            raise DeployerError("设备启用了安全下载保护，本工具不会绕过芯片保护。")
        features = ", ".join(esp.get_chip_features())
        chip_name = esp.CHIP_NAME
        mac = ":".join(f"{byte:02x}" for byte in esp.read_mac())
        esptool.attach_flash(esp)
        flash_size = esptool.cmds.detect_flash_size(esp)
        size_match = re.fullmatch(r"(\d+)MB", flash_size or "", re.I)
        flash_mb = int(size_match.group(1)) if size_match else -1
        normalized_mac, _ = _check_device_values(chip_name, flash_mb, mac, features)
        if normalized_mac != expected_mac.lower():
            raise DeployerError("写入连接上的芯片身份发生变化，已停止；没有写入固件。")
        print("Live device identity verified; writing on the same serial connection.", flush=True)
        esptool.main(args, esp=esp)
    finally:
        esp._port.close()


def esptool_entry(argv: Sequence[str]) -> int:
    """Dispatch ``app --esptool --worker-log PATH ...`` before importing the GUI."""
    args = list(argv)
    previous_stdout, previous_stderr = sys.stdout, sys.stderr
    stream = None
    try:
        log_path = _extract_option(args, "--worker-log")
        if log_path:
            stream = open(log_path, "w", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = stream
        forbidden = {"--force", "--ignore-flash-encryption-efuse-setting", "--erase-all",
                     "--encrypt", "erase-flash", "erase_flash", "erase-region", "write-mem",
                     "--no-verify", "--override-vddsdio"}
        if any(arg.split("=", 1)[0] in forbidden for arg in args):
            raise DeployerError("本部署工具不允许强制写入、擦除整片或绕过芯片保护。")
        guard_mac = _extract_option(args, "--guard-mac")
        guard_sha = _extract_option(args, "--guard-firmware-sha256")
        guard_size = _extract_option(args, "--guard-firmware-size")
        if "write-flash" in args:
            if not guard_mac or not guard_sha or not guard_size:
                raise DeployerError("写入前缺少已验证的设备和固件身份。")
            _guarded_write(args, guard_mac, guard_sha, int(guard_size))
        elif any(item in args for item in ("flash-id", "version", "--help", "-h")):
            import esptool
            esptool.main(args)
        else:
            raise DeployerError("不支持的设备操作。")
        return 0
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, (int, type(None))) else 1
    except Exception as exc:
        message = str(exc)
        if sys.stderr is not None:
            print(f"错误：{message}", file=sys.stderr, flush=True)
        return 2
    finally:
        if stream is not None:
            stream.flush()
            sys.stdout, sys.stderr = previous_stdout, previous_stderr
            stream.close()
