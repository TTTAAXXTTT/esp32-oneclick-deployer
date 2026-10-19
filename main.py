"""Windows deployer with bundled firmware and official-source updates."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import traceback

APP_VERSION = "1.1.0"


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def load_firmware():
    from deployer_core import FirmwareSpec

    base = resource_root() / "firmware"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("firmware_file") != "agent-n16r8.bin":
        raise ValueError("固件清单中的文件名不正确，请重新解压完整软件包。")
    spec = FirmwareSpec(
        path=base / manifest["firmware_file"],
        sha256=manifest["sha256"],
        size=int(manifest["size"]),
        flash_size_mb=int(manifest["flash_size_mb"]),
        chip_id=int(manifest["chip_id"]),
    )
    return manifest, spec


def self_test(report_path: str) -> int:
    """Check the packaged GUI/runtime/worker without opening any serial port."""
    import tempfile
    import tkinter as tk
    from deployer_core import validate_firmware, default_command_prefix

    report: dict = {"ok": False, "serial_port_opened": False, "app_version": APP_VERSION}
    try:
        manifest, spec = load_firmware()
        validate_firmware(spec)
        window = tk.Tk()
        window.withdraw()
        window.update_idletasks()
        report["tk_patchlevel"] = window.tk.call("info", "patchlevel")
        window.destroy()
        from updater import verify_cli
        cli = resource_root() / "tools" / "arduino-cli.exe"
        verify_cli(cli)
        cli_check = subprocess.run(
            [str(cli), "version", "--format", "json"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if cli_check.returncode != 0 or json.loads(cli_check.stdout).get("VersionString") != "1.5.1":
            raise RuntimeError("The bundled Arduino CLI did not pass its version check.")
        report["arduino_cli_version"] = "1.5.1"
        with tempfile.TemporaryDirectory(prefix="esp32-selftest-") as directory:
            log = Path(directory) / "worker.log"
            command = default_command_prefix() + ["--esptool", "--worker-log", str(log), "version"]
            result = subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            if result.returncode != 0 or "5.1.0" not in output:
                raise RuntimeError("The bundled esptool worker did not pass its version check: " + output)
            report["worker_version"] = "5.1.0"
        report.update(ok=True, target=manifest["target"], firmware_sha256=spec.sha256,
                      firmware_bytes=spec.size, source_commit=manifest["source_commit"])
    except Exception:
        report["error"] = traceback.format_exc()
    destination = Path(report_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


class Application:
    BG = "#F1F5F9"
    WHITE = "#FFFFFF"
    INK = "#152238"
    MUTED = "#65758B"
    ACCENT = "#2563EB"
    GREEN = "#15803D"
    BORDER = "#DDE5EF"

    def __init__(self, window):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk, self.window = tk, ttk, window
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.update_busy = False
        self.update_kind = ""
        self.update_candidate = None
        self.update_manager = None
        self.closing = False
        self.active = None
        self.ports = []
        self.stage = "check"
        self.ready = False
        self.port_signature = None
        self.session_lines: list[str] = []
        self.bundled_manifest, self.bundled_spec = load_firmware()
        self.manifest, self.spec = self.bundled_manifest, self.bundled_spec
        self.data_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ESP32OneClick"
        self.settings_path = self.data_root / "settings.json"
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        self.auto_update_value = tk.BooleanVar(value=settings.get("auto_check_updates", True) is not False
                                              if isinstance(settings, dict) else True)
        cache_warning = ""
        try:
            cached = self._new_update_manager().load_cached()
            if cached:
                self.manifest, self.spec = cached
        except Exception as error:
            cache_warning = "更新缓存不可用，已使用内置固件：" + str(error)
        data = self.data_root / "logs"
        data.mkdir(parents=True, exist_ok=True)
        self.log_path = data / f"deploy-{dt.datetime.now():%Y%m%d-%H%M%S}.log"

        window.title(f"ESP32 一键部署 v{APP_VERSION}")
        window.geometry("940x925")
        window.minsize(880, 910)
        window.configure(bg=self.BG)
        window.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._layout()
        # A windowed executable has no usable stderr. Tk's default exception
        # reporter can otherwise turn a callback error into a second fatal error.
        window.report_callback_exception = self._report_callback_exception
        self._log(f"ESP32-S3 N16R8 部署工具 v{APP_VERSION} 已启动。")
        self._log("固件来源版本：" + self.manifest["source_commit"][:12])
        if cache_warning:
            self._log(cache_warning)
        self._refresh_ports()
        self.window.after(80, self._drain)
        self.window.after(3000, self._auto_refresh)
        self.window.after(1800, self._auto_check_update)
        threading.Thread(target=self._check_firmware, daemon=True).start()

    def _style(self):
        s = self.ttk.Style(self.window)
        s.theme_use("clam")
        s.configure("TCombobox", padding=5, font=("Microsoft YaHei UI", 10))
        s.configure("Blue.Horizontal.TProgressbar", troughcolor="#E4ECF7",
                    background=self.ACCENT, bordercolor="#E4ECF7", lightcolor=self.ACCENT,
                    darkcolor=self.ACCENT, thickness=10)
        s.configure("Action.TButton", font=("Microsoft YaHei UI", 12, "bold"),
                    foreground="white", background=self.ACCENT, padding=(22, 10), borderwidth=0)
        s.map("Action.TButton", background=[("disabled", "#B6C6DE"), ("active", "#1D4ED8")],
              foreground=[("disabled", "#F8FAFC")])
        s.configure("Quiet.TButton", font=("Microsoft YaHei UI", 10), padding=(13, 8),
                    foreground=self.INK, background="#EAF0F8", borderwidth=0)
        s.map("Quiet.TButton", background=[("active", "#DCE7F5")])

    def _label(self, parent, text, size=10, color=None, bold=False, **kwargs):
        return self.tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color or self.INK,
                             font=("Microsoft YaHei UI", size, "bold" if bold else "normal"), **kwargs)

    def _card(self, parent):
        return self.tk.Frame(parent, bg=self.WHITE, highlightbackground=self.BORDER,
                             highlightthickness=1, padx=20, pady=12)

    def _layout(self):
        tk, ttk = self.tk, self.ttk
        main = tk.Frame(self.window, bg=self.BG, padx=26, pady=16)
        main.pack(fill="both", expand=True)

        header = tk.Frame(main, bg=self.BG)
        header.pack(fill="x", pady=(0, 10))
        chip = tk.Canvas(header, width=52, height=52, bg=self.BG, highlightthickness=0)
        chip.pack(side="left", padx=(0, 13))
        chip.create_rectangle(10, 10, 42, 42, fill=self.ACCENT, outline=self.ACCENT)
        chip.create_rectangle(19, 19, 33, 33, outline="white", width=2)
        for n in (17, 26, 35):
            for x0, y0, x1, y1 in ((n, 4, n, 10), (n, 42, n, 48),
                                  (4, n, 10, n), (42, n, 48, n)):
                chip.create_line(x0, y0, x1, y1, fill=self.ACCENT, width=3)
        heading = tk.Frame(header, bg=self.BG)
        heading.pack(side="left")
        self._label(heading, "ESP32 一键部署", size=22, bold=True).pack(anchor="w")
        self._label(heading, "连接开发板，点击部署，完成后接回 iPhone。", color=self.MUTED).pack(anchor="w", pady=(3, 0))
        self._label(header, "Windows x64\n支持在线更新", size=9, color=self.MUTED, justify="right").pack(side="right")

        device = self._card(main)
        device.pack(fill="x", pady=(0, 12))
        top = tk.Frame(device, bg=self.WHITE)
        top.pack(fill="x")
        self._label(top, "1  连接开发板", size=12, bold=True).pack(side="left")
        self._label(top, "ESP32-S3  ·  N16R8", color=self.ACCENT, bold=True).pack(side="right")
        self._label(device, "将 USB/OTG 接口用数据线连到电脑；只有一个候选设备时会自动选中。",
                    color=self.MUTED).pack(anchor="w", pady=(4, 7))
        row = tk.Frame(device, bg=self.WHITE)
        row.pack(fill="x")
        self.port_value = tk.StringVar()
        self.port_box = ttk.Combobox(row, textvariable=self.port_value)
        self.port_box.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.port_box.bind("<<ComboboxSelected>>", lambda _event: self._update_actions())
        self.port_box.bind("<KeyRelease>", lambda _event: self._update_actions())
        self.refresh_button = ttk.Button(row, text="刷新", style="Quiet.TButton", command=self._refresh_ports)
        self.refresh_button.pack(side="left")
        self.port_hint = self._label(device, "正在查找开发板……", size=9, color=self.MUTED)
        self.port_hint.pack(anchor="w", pady=(4, 0))
        options = tk.Frame(device, bg=self.WHITE)
        options.pack(fill="x", pady=(6, 0))
        self._label(options, "连接方式", size=9, color=self.MUTED).pack(side="left", padx=(0, 10))
        self.mode_value = tk.StringVar(value="自动进入下载模式（推荐）")
        self.mode_box = ttk.Combobox(options, textvariable=self.mode_value, state="readonly", width=31,
                                     values=("自动进入下载模式（推荐）", "已按 BOOT + RESET，直接连接"))
        self.mode_box.pack(side="left")
        self._label(options, "手动端口可直接输入 COM 编号", size=9, color=self.MUTED).pack(side="right")

        firmware = self._card(main)
        firmware.pack(fill="x", pady=(0, 12))
        fw_head = tk.Frame(firmware, bg=self.WHITE)
        fw_head.pack(fill="x")
        self._label(fw_head, "2  部署固件", size=12, bold=True).pack(side="left")
        self.firmware_badge = self._label(fw_head, "正在检查固件", size=9, color=self.MUTED)
        self.firmware_badge.pack(side="right")
        self._label(firmware, "ESP32 iPhone Agent", size=14, bold=True).pack(anchor="w", pady=(6, 2))
        self._label(firmware, "16 MB Flash / 8 MB PSRAM  ·  内置固件可离线刷写", size=9, color=self.MUTED).pack(anchor="w")
        update_row = tk.Frame(firmware, bg=self.WHITE)
        update_row.pack(fill="x", pady=(5, 3))
        self.firmware_version = self._label(update_row, self._firmware_caption(), size=9, color=self.MUTED)
        self.firmware_version.pack(side="left")
        self.prepare_update_button = ttk.Button(update_row, text="下载并准备更新", style="Quiet.TButton",
                                               command=lambda: self._start_update("prepare"))
        self.prepare_update_button.pack(side="right", padx=(8, 0))
        self.check_update_button = ttk.Button(update_row, text="检查更新", style="Quiet.TButton",
                                             command=lambda: self._start_update("check"))
        self.check_update_button.pack(side="right")
        update_options = tk.Frame(firmware, bg=self.WHITE)
        update_options.pack(fill="x")
        tk.Checkbutton(update_options, text="启动时自动检查", variable=self.auto_update_value,
                       command=self._save_settings, bg=self.WHITE, activebackground=self.WHITE,
                       fg=self.MUTED, font=("Microsoft YaHei UI", 9), bd=0, highlightthickness=0).pack(side="left")
        self.fallback_button = ttk.Button(update_options, text="使用内置固件", style="Quiet.TButton",
                                          command=self._use_bundled)
        self.fallback_button.pack(side="right")
        self.cancel_update_button = ttk.Button(update_options, text="取消更新", style="Quiet.TButton",
                                               command=self._cancel_update)
        self.cancel_update_button.pack(side="right", padx=(0, 8))
        self.update_status = self._label(firmware, "更新来自官方源码；首次准备需下载约 2 GB 工具链并自动编译。",
                                         size=9, color=self.MUTED, wraplength=800, justify="left")
        self.update_status.pack(anchor="w", pady=(3, 2))
        self._label(firmware, "点击部署将替换板上的原有程序和配置。", size=9, color="#8B5A19").pack(anchor="w", pady=(4, 8))
        action_row = tk.Frame(firmware, bg=self.WHITE)
        action_row.pack(fill="x")
        self.detect_button = ttk.Button(action_row, text="检测连接", style="Quiet.TButton",
                                         command=lambda: self._start("detect"))
        self.detect_button.pack(side="left")
        self.deploy_button = ttk.Button(action_row, text="一键部署", style="Action.TButton",
                                         command=lambda: self._start("flash"))
        self.deploy_button.pack(side="right")

        progress = self._card(main)
        progress.pack(fill="x", pady=(0, 12))
        stages = tk.Frame(progress, bg=self.WHITE)
        stages.pack(fill="x", pady=(0, 7))
        self.stage_labels = {}
        for index, (key, title) in enumerate((("check", "校验固件"), ("detect", "识别板子"),
                                              ("write", "写入固件"), ("done", "完成"))):
            label = self._label(stages, f"{index + 1}  {title}", size=9, color=self.MUTED)
            label.pack(side="left", expand=True)
            self.stage_labels[key] = label
        line = tk.Frame(progress, bg=self.WHITE)
        line.pack(fill="x")
        self.progress_value = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(line, style="Blue.Horizontal.TProgressbar", maximum=100,
                                            variable=self.progress_value)
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.percent = self._label(line, "0%", size=10, bold=True, width=5)
        self.percent.pack(side="right")
        self.status_title = self._label(progress, "正在准备", size=11, bold=True)
        self.status_title.pack(anchor="w", pady=(7, 2))
        self.status_detail = self._label(progress, "固件校验完成后即可连接开发板。", size=9,
                                          color=self.MUTED, justify="left", wraplength=800)
        self.status_detail.pack(anchor="w")

        log_header = tk.Frame(main, bg=self.BG)
        log_header.pack(fill="x", pady=(1, 5))
        self._label(log_header, "部署日志", size=10, bold=True).pack(side="left")
        tk.Button(log_header, text="保存日志", command=self._save_log, bg=self.BG, fg=self.ACCENT,
                  relief="flat", bd=0, cursor="hand2", font=("Microsoft YaHei UI", 9)).pack(side="right")
        # Reserve the footer before the expanding log pane so it remains visible
        # at the minimum window height, including the taller update controls.
        self._label(main, "连接失败？按住 BOOT → 按一下 RESET/EN → 松开 BOOT，再刷新并选择“直接连接”。",
                    size=9, color=self.MUTED).pack(side="bottom", anchor="w", pady=(9, 0))
        log_frame = tk.Frame(main, bg="#101C2D")
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=6, bg="#101C2D", fg="#D5E2F2", insertbackground="white",
                                relief="flat", bd=0, padx=12, pady=9, wrap="word", state="disabled",
                                font=("Consolas", 9), takefocus=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self._update_actions()

    def _check_firmware(self):
        try:
            from deployer_core import validate_firmware
            validate_firmware(self.spec)
            self.events.put(("ready", None))
        except Exception as error:
            self.events.put(("failed_init", str(error)))

    def _firmware_caption(self):
        kind = "内置" if self.spec == self.bundled_spec else "已下载更新"
        return f"当前固件：{self.manifest['source_commit'][:12]}（{kind}）"

    def _new_update_manager(self):
        from updater import UpdateManager
        return UpdateManager(
            self.data_root / "updates", resource_root() / "tools" / "arduino-cli.exe",
            log_callback=lambda line: self.events.put(("log", line)),
            progress_callback=lambda value: self.events.put(("update_progress", value)),
        )

    def _save_settings(self):
        import tempfile
        destination = None
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.settings_path.parent,
                                             prefix="settings-", suffix=".tmp", delete=False) as output:
                destination = Path(output.name)
                json.dump({"auto_check_updates": bool(self.auto_update_value.get())}, output)
            os.replace(destination, self.settings_path)
        except OSError as error:
            self._log("更新设置保存失败：" + str(error))
        finally:
            if destination is not None:
                destination.unlink(missing_ok=True)

    def _auto_check_update(self):
        if self.closing:
            return
        if self.auto_update_value.get() and self.ready and not self.busy and not self.update_busy:
            self._start_update("check")
        self.window.after(6 * 60 * 60 * 1000, self._auto_check_update)

    def _start_update(self, kind):
        if self.closing or self.busy or self.update_busy:
            return
        if kind == "prepare" and (not self.update_candidate or not self.update_candidate.available):
            return
        self.update_kind = kind
        self.update_busy = True
        self.update_manager = self._new_update_manager()
        candidate, manifest = self.update_candidate, dict(self.manifest)
        self.update_status.configure(text="正在检查官方固件更新……" if kind == "check" else
                                     "正在下载依赖并编译 N16R8 固件；首次准备可能较久，可取消。", fg=self.ACCENT)
        self._update_actions()
        manager = self.update_manager
        def run():
            try:
                if self.closing:
                    manager.cancel()
                    return
                result = manager.check(manifest) if kind == "check" else manager.prepare(candidate)
                self.events.put(("update_checked" if kind == "check" else "update_prepared", result))
            except Exception as error:
                self.events.put(("update_error", str(error)))
            finally:
                self.events.put(("update_idle", None))
        threading.Thread(target=run, daemon=True).start()

    def _cancel_update(self):
        if self.update_busy and self.update_manager:
            self.update_manager.cancel()
            self.update_status.configure(text="正在取消更新，当前固件保持可用……", fg=self.MUTED)

    def _use_bundled(self):
        if self.busy or self.update_busy:
            return
        self.manifest, self.spec = self.bundled_manifest, self.bundled_spec
        self.update_candidate = None
        self.firmware_version.configure(text=self._firmware_caption())
        self.update_status.configure(text="本次部署使用随软件附带的固件，可再次检查更新。", fg=self.MUTED)
        self.ready = False
        self._update_actions()
        threading.Thread(target=self._check_firmware, daemon=True).start()

    def _selected_device(self) -> str | None:
        match = re.match(r"\s*(COM[1-9][0-9]*)\b", self.port_value.get(), re.I)
        return match.group(1).upper() if match else None

    def _refresh_ports(self):
        if self.busy or self.closing:
            return
        # ttk creates popup children in Tcl, outside tkinter's widget registry.
        # focus_get() tries to resolve those children and raises KeyError('popdown').
        # Use Tcl paths directly and leave an open dropdown undisturbed.
        focus_path = str(self.window.tk.call("focus"))
        port_path = str(self.port_box)
        if any(focus_path.startswith(str(box) + ".") for box in (self.port_box, self.mode_box)):
            return
        editing = focus_path == port_path
        from deployer_core import list_candidate_ports
        previous = self._selected_device()
        try:
            self.ports = list_candidate_ports(include_device=previous)
        except Exception as error:
            self.port_hint.config(text="串口列表暂不可用，请稍后刷新。")
            self._log("刷新失败：" + str(error))
            return
        labels = tuple(p.label for p in self.ports)
        self.port_box.configure(values=labels)
        selected = next((p for p in self.ports if p.device.casefold() == (previous or "").casefold()), None)
        if not editing:
            if selected:
                self.port_value.set(selected.label)
            elif len(self.ports) == 1:
                self.port_value.set(self.ports[0].label)
            elif not previous or not self.ports:
                self.port_value.set("")
        count = len(self.ports)
        self.port_hint.config(text=("已自动选中开发板。" if count == 1 else
                                      f"发现 {count} 个候选串口，请选择目标开发板。" if count else
                                      "未发现开发板。请连接 USB 数据线；也可手动输入 COM 编号。"))
        if labels != self.port_signature:
            self.port_signature = labels
            self._log(f"设备列表已更新：{count} 个候选串口。")
        self._update_actions()

    def _auto_refresh(self):
        if self.closing:
            return
        try:
            if not self.busy:
                self._refresh_ports()
        finally:
            if not self.closing:
                self.window.after(3000, self._auto_refresh)

    def _report_callback_exception(self, exc_type, value, tb):
        """Keep callback diagnostics local without writing to console streams."""
        if self.closing:
            return
        detail = "".join(traceback.format_exception(exc_type, value, tb))
        try:
            self._log("界面任务异常：\n" + detail)
        except Exception:
            # Logging must also work if the affected callback involved the UI.
            try:
                with self.log_path.open("a", encoding="utf-8") as output:
                    output.write(detail + "\n")
            except OSError:
                pass

    def _update_actions(self):
        locked = self.busy or self.update_busy
        available = self.ready and not locked and self._selected_device() is not None
        for button in (self.detect_button, self.deploy_button):
            button.configure(state="normal" if available else "disabled")
        self.refresh_button.configure(state="disabled" if self.busy else "normal")
        self.port_box.configure(state="disabled" if self.busy else "normal")
        self.mode_box.configure(state="disabled" if self.busy else "readonly")
        self.deploy_button.configure(text="正在部署……" if self.busy else "一键部署")
        self.check_update_button.configure(state="disabled" if locked else "normal")
        can_prepare = bool(self.update_candidate and self.update_candidate.available)
        self.prepare_update_button.configure(state="normal" if can_prepare and not locked else "disabled")
        self.cancel_update_button.configure(state="normal" if self.update_busy else "disabled")
        self.fallback_button.configure(state="normal" if not locked and self.spec != self.bundled_spec else "disabled")

    def _start(self, kind):
        if self.busy or self.update_busy or not self.ready:
            return
        from deployer_core import choose_port, list_candidate_ports
        try:
            device = self._selected_device()
            port = choose_port(list_candidate_ports(include_device=device), device)
        except Exception as error:
            self._show_error(str(error))
            return
        mode = "no-reset" if self.mode_value.get().startswith("已按") else "auto"
        self.busy = True
        self.progress_value.set(0)
        self.percent.config(text="0%")
        self._update_actions()
        self._log("开始部署。" if kind == "flash" else "开始检测连接；不会写入 Flash。")
        threading.Thread(target=self._work, args=(kind, port, mode), daemon=True).start()

    def _work(self, kind, port, mode):
        from deployer_core import Deployer
        try:
            if self.closing:
                return
            engine = Deployer(self.spec, log_callback=lambda line: self.events.put(("log", line)),
                              progress_callback=lambda value: self.events.put(("progress", value)))
            self.active = engine
            if self.closing:
                engine.cancel()
                return
            self.events.put(("stage", "check"))
            engine.validate_firmware()
            self.events.put(("stage", "detect"))
            info = engine.detect(port, reset_mode=mode)
            if self.closing:
                engine.cancel()
                return
            self.events.put(("device", (port.device, info)))
            if kind == "detect":
                self.events.put(("detected", info))
            else:
                self.events.put(("stage", "write"))
                engine.flash(port, expected=info, reset_mode=mode)
                self.events.put(("complete", info))
        except Exception as error:
            self.events.put(("error", str(error)))
        finally:
            self.active = None
            self.events.put(("idle", None))

    def _set_stage(self, stage):
        self.stage = stage
        titles = {
            "check": ("正在校验固件", "检查文件完整性和 N16R8 固件配置。", 3),
            "detect": ("正在识别开发板", "核对 ESP32-S3 芯片、16 MB Flash 和 8 MB PSRAM。", 8),
            "write": ("正在写入固件", "请保持连接，完成写入后会自动校验。", 12),
            "done": ("部署成功，校验已通过", "按一下 RESET/EN 正常启动。然后将 USB/OTG 接回 iPhone，打开 ESP32 Agent。", 100),
        }
        title, detail, value = titles[stage]
        for key, label in self.stage_labels.items():
            label.configure(fg=self.GREEN if stage == "done" else self.ACCENT if key == stage else self.MUTED)
        self.status_title.configure(text=title, fg=self.GREEN if stage == "done" else self.INK)
        self.status_detail.configure(text=detail)
        self.progress_value.set(value)
        self.percent.configure(text=f"{value}%")

    def _show_error(self, message):
        self.status_title.configure(text="没有完成部署", fg="#B42318")
        self.status_detail.configure(text=message)
        self._log("错误：" + message)

    def _drain(self):
        if self.closing:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._log(value)
                elif kind == "stage":
                    self._set_stage(value)
                elif kind == "progress" and self.stage == "write":
                    percent = min(97, 12 + 0.85 * max(0, min(100, value)))
                    self.progress_value.set(percent)
                    self.percent.configure(text=f"{percent:.0f}%")
                elif kind == "ready":
                    self.ready = True
                    self.firmware_badge.configure(text="SHA-256 校验通过", fg=self.GREEN)
                    self.status_title.configure(text="准备就绪，请连接开发板")
                    self.status_detail.configure(text="选中目标串口后，点击“一键部署”。")
                    self._log("当前固件完整性校验通过。")
                    self._update_actions()
                elif kind == "failed_init":
                    self.ready = False
                    self.firmware_badge.configure(text="固件不可用", fg="#B42318")
                    self._show_error(value)
                elif kind == "device":
                    port, info = value
                    self._log(f"{port}：ESP32-S3 / {info.flash_size_mb} MB Flash / {info.psram_mb} MB PSRAM，已确认目标板型。")
                elif kind == "detected":
                    self.status_title.configure(text="开发板检测通过", fg=self.GREEN)
                    self.status_detail.configure(text="目标板为 N16R8，可以点击“一键部署”。若退出检测，请按 RESET/EN 恢复运行。")
                    self.progress_value.set(10)
                    self.percent.configure(text="10%")
                elif kind == "complete":
                    self._set_stage("done")
                    self._log("部署完成：写入进程正常结束，设备端哈希校验通过。")
                elif kind == "error":
                    self._show_error(value)
                elif kind == "idle":
                    self.busy = False
                    self._update_actions()
                elif kind == "update_checked":
                    self.update_candidate = value
                    message = (f"发现固件更新 {value.source_commit[:12]}，点击“下载并准备更新”。" if value.available
                               else "已检查官方固件代码，当前固件已是最新。")
                    self.update_status.configure(text=message, fg=self.ACCENT if value.available else self.GREEN)
                    self._log(message)
                elif kind == "update_prepared":
                    self.manifest, self.spec = value
                    self.update_candidate = None
                    self.ready = True
                    self.firmware_version.configure(text=self._firmware_caption())
                    self.firmware_badge.configure(text="SHA-256 校验通过", fg=self.GREEN)
                    self.update_status.configure(text="更新固件已准备并校验，可点击“一键部署”写入开发板。", fg=self.GREEN)
                    self._log("更新固件准备成功，尚未写入开发板。版本：" + self.manifest["source_commit"])
                elif kind == "update_error":
                    self.update_status.configure(text="更新未完成，当前固件仍可使用：" + value, fg="#B42318")
                    self._log("更新未完成：" + value)
                elif kind == "update_progress":
                    # Build output is shown in the log; never reuse flash progress
                    # for a network/build job or imply that the board was written.
                    pass
                elif kind == "update_idle":
                    self.update_busy = False
                    self.update_manager = None
                    self._update_actions()
        except queue.Empty:
            pass
        finally:
            if not self.closing:
                self.window.after(80, self._drain)

    def _log(self, text):
        # Keep the current user's board details local. Strip terminal controls.
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text)).replace("\r", "\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return
        self.log_text.configure(state="normal")
        for line in lines:
            stamped = f"[{dt.datetime.now():%H:%M:%S}] {line}"
            self.session_lines.append(stamped)
            self.log_text.insert("end", stamped + "\n")
        self.log_text.see("end")
        if int(self.log_text.index("end-1c").split(".")[0]) > 650:
            self.log_text.delete("1.0", "150.0")
        self.log_text.configure(state="disabled")
        try:
            with self.log_path.open("a", encoding="utf-8") as output:
                output.write("\n".join(self.session_lines[-len(lines):]) + "\n")
        except OSError:
            pass

    def _save_log(self):
        from tkinter import filedialog
        destination = filedialog.asksaveasfilename(
            parent=self.window, title="保存部署日志", defaultextension=".log",
            initialfile=self.log_path.name, filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt")],
        )
        if destination:
            try:
                Path(destination).write_text("\n".join(self.session_lines) + "\n", encoding="utf-8")
                self._log("日志已保存。")
            except OSError as error:
                self._show_error("日志保存失败：" + str(error))

    def close(self):
        from tkinter import messagebox
        if self.busy and not messagebox.askyesno(
            "正在部署", "任务尚未结束。现在退出可能使固件不完整，需要重新刷写。是否退出？", parent=self.window,
        ):
            return
        self.closing = True
        if self.active:
            self.active.cancel()
        if self.update_manager:
            self.update_manager.cancel()
        self.window.destroy()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--esptool":
        from deployer_core import esptool_entry
        return esptool_entry(sys.argv[2:])
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        return self_test(sys.argv[2])
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    import tkinter as tk
    from tkinter import messagebox
    window = tk.Tk()
    try:
        Application(window)
    except Exception as error:
        window.withdraw()
        messagebox.showerror("软件无法启动", "请完整解压部署包，并保留 _internal 文件夹。\n\n" + str(error), parent=window)
        window.destroy()
        return 1
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
