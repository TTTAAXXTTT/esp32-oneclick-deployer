"""Official-source, N16R8-only updates; this module never opens a serial port.

GitHub's current main commit is resolved before downloading its immutable archive.
An update becomes active only after a pinned local build and full image validation.
All downloaded tools, indexes, sources and builds live in the application's cache.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

from deployer_core import CancelledError, DeployerError, FirmwareSpec, validate_firmware


UPSTREAM = "onehumanbeing/esp32-iphone-agent"
SOURCE_URL = f"https://github.com/{UPSTREAM}"
REF_URL = f"{SOURCE_URL}.git/info/refs?service=git-upload-pack"
INDEX_URL = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"
CORE_VERSION = "3.3.12"
NIMBLE_VERSION = "2.5.1"
CLI_VERSION = "1.5.1"
FQBN = ("esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=cdc,FlashSize=16M,"
        "PSRAM=opi,PartitionScheme=default_8MB")
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXTRACT_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 15000
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ALLOWED_HOSTS = frozenset({"github.com", "api.github.com", "codeload.github.com"})
_DLL_LOCK = threading.Lock()
_WINDOWS_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", re.I)


class UpdateError(DeployerError):
    """Update failure that leaves the last known good firmware active."""


@dataclass(frozen=True)
class UpdateCandidate:
    source_commit: str
    hardware_sha256: str
    source_dir: Path
    available: bool


def verify_cli(cli_path: str | Path) -> None:
    """Verify the packaged executable without launching it or using the network."""
    cli_path = Path(cli_path)
    try:
        manifest_path = cli_path.parent / "manifest.json"
        if manifest_path.stat().st_size > 8192:
            raise UpdateError("编译工具清单大小无效。")
        record = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        manifest = record.get("arduino_cli") if isinstance(record, dict) else None
        if (record.get("schema_version") != 1 or not isinstance(manifest, dict) or
                manifest.get("version") != CLI_VERSION or
                manifest.get("filename") != cli_path.name or
                manifest.get("platform") != "windows-amd64" or
                manifest.get("size") != cli_path.stat().st_size or
                not _DIGEST.fullmatch(str(manifest.get("sha256", "")))):
            raise UpdateError("编译工具清单版本或文件名无效。")
        hasher = hashlib.sha256()
        with cli_path.open("rb") as source:
            while block := source.read(1024 * 1024):
                hasher.update(block)
        if hasher.hexdigest() != manifest["sha256"]:
            raise UpdateError("内置编译工具 SHA-256 校验失败，请重新解压完整软件包。")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise UpdateError("编译工具或其校验清单缺失，请重新解压完整软件包。") from exc


def hardware_digest(sketch_dir: str | Path) -> str:
    """Hash relative filenames and exact file bytes in a stable sorted order."""
    root = Path(sketch_dir)
    if not root.is_dir() or root.is_symlink():
        raise UpdateError("官方源码缺少 hardware/hid_bridge 目录。")
    paths = sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix())
    hasher = hashlib.sha256()
    count = 0
    for path in paths:
        if path.is_symlink():
            raise UpdateError("固件源码包含不支持的符号链接。")
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        hasher.update(len(name).to_bytes(4, "big"))
        hasher.update(name)
        hasher.update(len(content).to_bytes(8, "big"))
        hasher.update(content)
        count += 1
    if not count or not (root / "hid_bridge.ino").is_file() or not (root / "nimble_cfg.h").is_file():
        raise UpdateError("官方固件源码不完整，已保留当前固件。")
    return hasher.hexdigest()


def parse_main_advertisement(payload: bytes) -> str:
    """Read Git protocol v0 pkt-lines, requiring the exact main branch ref."""
    offset = 0
    found = []
    while offset < len(payload):
        if offset + 4 > len(payload):
            raise UpdateError("GitHub 返回了不完整的 Git 版本信息。")
        try:
            length = int(payload[offset:offset + 4], 16)
        except ValueError as exc:
            raise UpdateError("GitHub 返回的 Git 版本信息无效。") from exc
        offset += 4
        if length in (0, 1, 2):
            continue
        if length < 4 or offset + length - 4 > len(payload):
            raise UpdateError("GitHub 返回的 Git 数据长度无效。")
        line = payload[offset:offset + length - 4].split(b"\0", 1)[0].strip()
        offset += length - 4
        match = re.fullmatch(rb"([0-9a-f]{40}) refs/heads/main", line)
        if match:
            found.append(match.group(1).decode("ascii"))
    if len(found) != 1:
        raise UpdateError("无法确认官方 main 分支版本，已保留当前固件。")
    return found[0]


def _validate_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS or
            parsed.port not in (None, 443) or parsed.username or parsed.password):
        raise UpdateError("更新地址不属于受支持的 GitHub HTTPS 源。")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class UpdateManager:
    def __init__(self, cache_root: Path, cli_path: Path,
                 log_callback: Callable[[str], None] | None = None,
                 progress_callback: Callable[[float], None] | None = None):
        self.cache_root = Path(cache_root).resolve()
        self.cli_path = Path(cli_path).resolve()
        self.log_callback = log_callback or (lambda line: None)
        self.progress_callback = progress_callback or (lambda fraction: None)
        self._cancel = threading.Event()
        self._process_lock = threading.Lock()
        self._commit_lock = threading.Lock()
        self._process = None
        self._opener = urllib.request.build_opener(_SafeRedirect())

    def _check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise CancelledError("更新已取消，当前固件保持不变。")

    def _log(self, text: str) -> None:
        self.log_callback(text)

    def cancel(self) -> None:
        with self._commit_lock:
            self._cancel.set()
        with self._process_lock:
            if self._process is not None:
                self._stop_process(self._process)

    def _download(self, url: str, destination: Path, limit: int) -> None:
        self._check_cancelled()
        _validate_url(url)
        request = urllib.request.Request(url, headers={
            "User-Agent": "ESP32-OneClick-Official-Updater/1.1",
            "Accept": "application/octet-stream",
            "Git-Protocol": "version=0",
        })
        destination.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 300
        try:
            with self._opener.open(request, timeout=20) as response:
                _validate_url(response.geturl())
                declared = response.headers.get("Content-Length")
                if declared is not None and (not declared.isdecimal() or int(declared) > limit):
                    raise UpdateError("官方更新下载大小超出限制。")
                actual = 0
                with destination.open("wb") as output:
                    while True:
                        self._check_cancelled()
                        if time.monotonic() > deadline:
                            raise UpdateError("更新下载超时，请检查网络后重试。")
                        block = response.read(128 * 1024)
                        if not block:
                            break
                        actual += len(block)
                        if actual > limit:
                            raise UpdateError("官方更新下载大小超出限制。")
                        output.write(block)
                if declared is not None and actual != int(declared):
                    raise UpdateError("官方更新下载不完整，请重试。")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            destination.unlink(missing_ok=True)
            raise UpdateError(f"无法下载官方更新：{exc}。当前固件保持不变。") from exc
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        self._check_cancelled()

    def _resolve_main(self) -> str:
        with tempfile.TemporaryDirectory(prefix="resolve-", dir=self.cache_root) as temporary:
            path = Path(temporary) / "refs"
            try:
                self._download(REF_URL, path, 8 * 1024 * 1024)
                return parse_main_advertisement(path.read_bytes())
            except CancelledError:
                raise
            except UpdateError:
                self._log("Git 版本查询暂时不可用，正在尝试官方提交订阅源…")
            self._download(f"{SOURCE_URL}/commits/main.atom", path, 1024 * 1024)
            xml = path.read_bytes()
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise UpdateError("官方提交订阅源格式无效。")
            try:
                root = ET.fromstring(xml)
                entry = root.find("{http://www.w3.org/2005/Atom}entry")
                identifier = entry.findtext("{http://www.w3.org/2005/Atom}id") if entry is not None else None
                match = re.fullmatch(r"tag:github\.com,2008:Grit::Commit/([0-9a-f]{40})", identifier or "")
                if match:
                    return match.group(1)
            except ET.ParseError as exc:
                raise UpdateError("官方提交订阅源无法解析。") from exc
            raise UpdateError("无法确认官方 main 分支版本，已保留当前固件。")

    def _extract_source(self, archive: Path, destination: Path, commit: str) -> Path:
        """Validate the whole ZIP before writing any of its members."""
        prefix = f"esp32-iphone-agent-{commit}"
        total = 0
        names = set()
        files = set()
        try:
            with zipfile.ZipFile(archive) as bundle:
                members = bundle.infolist()
                if not members or len(members) > MAX_MEMBERS:
                    raise UpdateError("官方源码压缩包的文件数量无效。")
                for member in members:
                    name = member.filename
                    path = PurePosixPath(name)
                    mode = member.external_attr >> 16
                    parts = path.parts
                    if ("\\" in name or "\0" in name or path.is_absolute() or not parts or
                            parts[0] != prefix or any(p in {"", ".", ".."} or ":" in p or
                            p.rstrip(" .") != p or _WINDOWS_DEVICE.fullmatch(p) for p in parts) or
                            stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
                        raise UpdateError("官方源码压缩包包含不安全的路径或链接。")
                    # Windows aliases must not collide, including implicit directories.
                    key = "/".join(parts).casefold()
                    if key in names:
                        raise UpdateError("官方源码压缩包包含重复文件名。")
                    names.add(key)
                    if not member.is_dir():
                        files.add(key)
                    if member.flag_bits & 1 or member.file_size > MAX_MEMBER_BYTES:
                        raise UpdateError("官方源码压缩包包含不支持的文件。")
                    total += member.file_size
                    if total > MAX_EXTRACT_BYTES:
                        raise UpdateError("官方源码解压大小超出限制。")
                for key in names:
                    parents = key.split("/")[:-1]
                    if any("/".join(parents[:i]) in files for i in range(1, len(parents) + 1)):
                        raise UpdateError("官方源码压缩包包含冲突的文件路径。")
                destination.mkdir(parents=True, exist_ok=True)
                for member in members:
                    self._check_cancelled()
                    relative = PurePosixPath(member.filename).parts[1:]
                    if not relative:
                        continue
                    target = destination.joinpath(*relative)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    written = 0
                    with bundle.open(member) as source, target.open("xb") as output:
                        while block := source.read(128 * 1024):
                            self._check_cancelled()
                            written += len(block)
                            if written > member.file_size:
                                raise UpdateError("官方源码解压大小不匹配。")
                            output.write(block)
                    if written != member.file_size:
                        raise UpdateError("官方源码压缩包不完整。")
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            if isinstance(exc, (UpdateError, CancelledError)):
                raise
            raise UpdateError(f"官方源码无法解压：{exc}") from exc
        hardware_digest(destination / "hardware" / "hid_bridge")
        if not (destination / "LICENSE").is_file():
            raise UpdateError("官方源码缺少许可证文件。")
        return destination

    def check(self, current_manifest: dict) -> UpdateCandidate:
        self._check_cancelled()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._log("正在查询官方 main 分支的最新版本…")
        self.progress_callback(0.02)
        commit = self._resolve_main()
        self._log(f"官方源码版本：{commit}")
        source_dir = self.cache_root / "sources" / commit / "source"
        if not source_dir.is_dir():
            staging_root = self.cache_root / "staging"
            staging_root.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="source-", dir=staging_root) as temporary:
                staging = Path(temporary)
                archive = staging / "source.zip"
                self._log("正在下载官方源码（固定提交版本）…")
                self._download(f"https://codeload.github.com/{UPSTREAM}/zip/{commit}",
                               archive, MAX_ARCHIVE_BYTES)
                ready = staging / "ready"
                self._extract_source(archive, ready / "source", commit)
                self._check_cancelled()
                source_dir.parent.parent.mkdir(parents=True, exist_ok=True)
                if not source_dir.parent.exists():
                    shutil.copy2(archive, ready / "official-source.zip")
                    os.replace(ready, source_dir.parent)
        digest = hardware_digest(source_dir / "hardware" / "hid_bridge")
        baseline = current_manifest.get("hardware_sha256")
        same = digest == baseline if isinstance(baseline, str) and _DIGEST.fullmatch(baseline) else (
            current_manifest.get("source_commit") == commit)
        self._check_cancelled()
        self.progress_callback(0.1)
        return UpdateCandidate(commit, digest, source_dir, not same)

    @staticmethod
    def _stop_process(process) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=8, shell=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _run(self, arguments: list[str], timeout: float = 1800) -> str:
        self._check_cancelled()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        command = [str(self.cli_path), "--config-file", str(self.cache_root / "arduino-cli.yaml"),
                   "--no-color", *arguments]
        # A private config must also win over any global ARDUINO_* environment.
        environment = {k: v for k, v in os.environ.items()
                       if not k.upper().startswith(("ARDUINO_", "_PYI_")) and
                       k.upper() not in {"PYTHONHOME", "PYTHONPATH"}}
        environment["PYTHONUTF8"] = "1"
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        process = None
        chunks = []
        pending = ""
        with tempfile.TemporaryDirectory(prefix="process-", dir=self.cache_root) as temporary:
            log_path = Path(temporary) / "output.log"
            try:
                with log_path.open("wb") as writer, log_path.open("r", encoding="utf-8", errors="replace") as reader:
                    with self._process_lock:
                        self._check_cancelled()
                        # Frozen applications set a DLL search directory for their own
                        # bundled Python. External compiler executables need the normal
                        # Windows DLL search order instead; restore ours immediately.
                        with _DLL_LOCK:
                            frozen_windows = os.name == "nt" and getattr(sys, "frozen", False)
                            if frozen_windows:
                                import ctypes
                                old_dll = ctypes.create_unicode_buffer(32768)
                                ctypes.windll.kernel32.GetDllDirectoryW(len(old_dll), old_dll)
                                ctypes.windll.kernel32.SetDllDirectoryW(None)
                            try:
                                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=writer,
                                    stderr=subprocess.STDOUT, shell=False, env=environment,
                                    cwd=self.cache_root, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                            finally:
                                if frozen_windows:
                                    ctypes.windll.kernel32.SetDllDirectoryW(old_dll.value or None)
                        self._process = process
                        if self._cancel.is_set():
                            self._stop_process(process)
                            self._check_cancelled()
                    deadline = time.monotonic() + timeout
                    while True:
                        text = reader.read()
                        if text:
                            chunks.append(text)
                            pending += text
                            while "\n" in pending:
                                line, pending = pending.split("\n", 1)
                                self._log(line.rstrip("\r"))
                            if sum(map(len, chunks)) > 32 * 1024 * 1024:
                                raise UpdateError("编译输出异常过多，已停止并保留当前固件。")
                        self._check_cancelled()
                        result = process.poll()
                        if result is not None:
                            final = reader.read()
                            chunks.append(final)
                            if pending or final:
                                self._log((pending + final).strip())
                            output = "".join(chunks)
                            if result != 0:
                                tail = "\n".join(output.splitlines()[-8:])
                                raise UpdateError(f"官方固件准备失败（退出码 {result}）。\n{tail}")
                            return output
                        if time.monotonic() > deadline:
                            raise UpdateError("官方固件准备超时，已停止；当前固件保持不变。")
                        time.sleep(0.1)
            except OSError as exc:
                raise UpdateError(f"无法运行内置编译工具：{exc}") from exc
            finally:
                if process is not None and process.poll() is None:
                    self._stop_process(process)
                with self._process_lock:
                    if self._process is process:
                        self._process = None

    def _ensure_tools(self) -> None:
        verify_cli(self.cli_path)
        data = self.cache_root / "arduino" / "data"
        downloads = self.cache_root / "arduino" / "downloads"
        user = self.cache_root / "arduino" / "user"
        for directory in (data, downloads, user):
            directory.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.cache_root / "arduino-cli.yaml", {
            "board_manager": {"additional_urls": [INDEX_URL]},
            "directories": {"data": str(data), "downloads": str(downloads), "user": str(user)},
            "network": {"connection_timeout": "60s"},
            "metrics": {"enabled": False},
        })
        version = self._run(["version"], timeout=60)
        if not re.search(r"\bVersion:\s*1\.5\.1\b", version):
            raise UpdateError("内置 Arduino CLI 版本不匹配，需要 1.5.1。")
        receipt = self.cache_root / "arduino" / "ready.json"
        expected = {"cli": CLI_VERSION, "core": CORE_VERSION, "nimble": NIMBLE_VERSION}
        core_file = data / "packages" / "esp32" / "hardware" / "esp32" / CORE_VERSION / "platform.txt"
        lib_file = user / "libraries" / "NimBLE-Arduino" / "library.properties"
        try:
            ready = json.loads(receipt.read_text(encoding="utf-8")) == expected
            ready = ready and core_file.is_file() and re.search(
                r"(?m)^version=2\.5\.1\s*$", lib_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ready = False
        if ready:
            self._log("已复用本软件缓存的固定版本编译依赖。")
            return
        self._log("首次更新需要下载编译依赖，可能需要较长时间和数 GB 空间…")
        self._run(["core", "update-index"], timeout=600)
        self._run(["core", "install", f"esp32:esp32@{CORE_VERSION}"], timeout=2400)
        self._run(["lib", "update-index"], timeout=600)
        self._run(["lib", "install", f"NimBLE-Arduino@{NIMBLE_VERSION}"], timeout=900)
        self._check_cancelled()
        _atomic_json(receipt, expected)

    def _cached_version(self, version_dir: Path) -> tuple[dict, FirmwareSpec]:
        manifest_path = version_dir / "manifest.json"
        if manifest_path.stat().st_size > 32 * 1024:
            raise UpdateError("缓存固件清单大小无效。")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("target") != "ESP32-S3 N16R8" or
                manifest.get("flash_size_mb") != 16 or manifest.get("psram_mb") != 8 or
                manifest.get("chip_id") != 9 or manifest.get("size") != 16777216 or
                manifest.get("build_fqbn") != FQBN or
                manifest.get("firmware_file") != "agent-n16r8.bin" or
                not _SHA.fullmatch(str(manifest.get("source_commit", ""))) or
                not _DIGEST.fullmatch(str(manifest.get("hardware_sha256", ""))) or
                manifest.get("source_url") != f"{SOURCE_URL}/tree/{manifest.get('source_commit')}"):
            raise UpdateError("缓存固件的型号、来源或编译配置不匹配。")
        source_dir = self.cache_root / "sources" / manifest["source_commit"] / "source"
        if hardware_digest(source_dir / "hardware" / "hid_bridge") != manifest["hardware_sha256"]:
            raise UpdateError("缓存固件源码校验失败。")
        spec = FirmwareSpec(version_dir / "agent-n16r8.bin", manifest["sha256"])
        validate_firmware(spec)
        return manifest, spec

    def load_cached(self) -> tuple[dict, FirmwareSpec] | None:
        try:
            active_path = self.cache_root / "active.json"
            if not active_path.exists():
                return None
            if active_path.stat().st_size > 2048:
                raise UpdateError("缓存激活记录过大。")
            active = json.loads(active_path.read_text(encoding="utf-8"))
            version = active.get("directory", "") if isinstance(active, dict) else ""
            if (not isinstance(active, dict) or active.get("schema") != 1 or
                    not re.fullmatch(r"[0-9a-f]{40}-[0-9a-f]{12}", version)):
                raise UpdateError("缓存激活记录无效。")
            result = self._cached_version(self.cache_root / "firmware" / version)
            if version != result[0]["source_commit"] + "-" + result[0]["hardware_sha256"][:12]:
                raise UpdateError("缓存固件版本记录不一致。")
            return result
        except (OSError, ValueError, KeyError, TypeError, DeployerError) as exc:
            self._log(f"已忽略损坏的更新缓存，继续使用内置固件：{exc}")
            return None

    def prepare(self, candidate: UpdateCandidate) -> tuple[dict, FirmwareSpec]:
        self._check_cancelled()
        if (not isinstance(candidate, UpdateCandidate) or not _SHA.fullmatch(candidate.source_commit) or
                not _DIGEST.fullmatch(candidate.hardware_sha256)):
            raise UpdateError("更新候选版本无效。")
        expected_source = self.cache_root / "sources" / candidate.source_commit / "source"
        if candidate.source_dir.resolve() != expected_source.resolve():
            raise UpdateError("更新源码必须来自本软件校验过的官方缓存。")
        sketch = expected_source / "hardware" / "hid_bridge"
        if hardware_digest(sketch) != candidate.hardware_sha256:
            raise UpdateError("待更新源码已经改变，请重新检查官方更新。")
        self._ensure_tools()
        self.progress_callback(0.35)
        staging_root = self.cache_root / "staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="build-", dir=staging_root) as temporary:
            staging = Path(temporary)
            copied_sketch = staging / "hid_bridge"
            shutil.copytree(sketch, copied_sketch)
            build = staging / "build"
            output = staging / "output"
            # Arduino recipe splitting honours double quotes around a Windows path.
            # Forward slashes avoid backslash escaping by the recipe argument parser.
            include = (copied_sketch / "nimble_cfg.h").resolve().as_posix()
            if '"' in include or "\n" in include or "\r" in include:
                raise UpdateError("更新缓存路径含不支持的字符。")
            forced_include = f'-include "{include}"'
            self._log("正在为 ESP32-S3 N16R8 编译官方固件…")
            self._run(["compile", "--fqbn", FQBN, "--jobs", "2", "--clean",
                       "--build-property", f"compiler.cpp.extra_flags={forced_include}",
                       "--build-property", f"compiler.c.extra_flags={forced_include}",
                       "--build-path", str(build), "--output-dir", str(output), str(copied_sketch)],
                      timeout=2400)
            self._check_cancelled()
            # The pinned core writes the padded 16 MB merged image to build-path.
            merged = build / "hid_bridge.ino.merged.bin"
            if not merged.is_file():
                raise UpdateError("编译没有生成完整合并镜像，已保留当前固件。")
            sha256 = hashlib.sha256(merged.read_bytes()).hexdigest()
            validate_firmware(FirmwareSpec(merged, sha256))
            if hardware_digest(sketch) != candidate.hardware_sha256:
                raise UpdateError("编译期间官方源码缓存发生变化，已保留当前固件。")
            self.progress_callback(0.9)
            manifest = {
                "name": "ESP32 iPhone Agent", "target": "ESP32-S3 N16R8",
                "firmware_file": "agent-n16r8.bin", "sha256": sha256, "size": 16777216,
                "flash_size_mb": 16, "psram_mb": 8, "chip_id": 9,
                "source_commit": candidate.source_commit,
                "hardware_sha256": candidate.hardware_sha256,
                "source_url": f"{SOURCE_URL}/tree/{candidate.source_commit}",
                "build_fqbn": FQBN, "firmware_license": "GPL-3.0-only",
                "build_tools": {"arduino-cli": CLI_VERSION, "esp32-core": CORE_VERSION,
                                "NimBLE-Arduino": NIMBLE_VERSION},
            }
            ready = staging / "ready"
            ready.mkdir()
            shutil.copy2(merged, ready / "agent-n16r8.bin")
            _atomic_json(ready / "manifest.json", manifest)
            self._check_cancelled()
            version = candidate.source_commit + "-" + candidate.hardware_sha256[:12]
            final_dir = self.cache_root / "firmware" / version
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            if final_dir.exists():
                # Never overwrite an existing immutable version with a partial build.
                existing = self._cached_version(final_dir)
                if existing[0]["hardware_sha256"] != candidate.hardware_sha256:
                    raise UpdateError("已有缓存版本不匹配，已保留当前固件。")
                manifest = existing[0]
            else:
                os.replace(ready, final_dir)
            result = self._cached_version(final_dir)
            # Only this final atomic pointer change activates a fully checked image.
            # Hold the cancellation lock so closing cannot race with activation.
            with self._commit_lock:
                self._check_cancelled()
                _atomic_json(self.cache_root / "active.json", {"schema": 1, "directory": version})
            self.progress_callback(1.0)
            self._log("官方固件已准备并校验完成；点击一键部署后才会写入开发板。")
            return result
