"""Safety and orchestration tests; none open a serial port or flash hardware."""
from __future__ import annotations

from contextlib import redirect_stderr
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import struct
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deployer_core as core


PORT = core.PortInfo("COM3", "USB JTAG", 0x303A, 0x1001, "board-a", "1-2")
DETECT_OUTPUT = """esptool v5.1.0
Chip type:          ESP32-S3 (QFN56) (revision v0.2)
Features:           Wi-Fi, BT 5 (LE), Embedded PSRAM 8MB (AP_3v3)
MAC:                c4:9e:7e:18:c2:b0
Detected flash size: 16MB
"""


class FirmwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.path = Path(cls.folder.name) / "approved.bin"
        data = bytearray(b"\xff") * core.FLASH_BYTES
        for offset in (0, 0x10000):
            data[offset:offset + 24] = b"\0" * 24
            data[offset] = 0xE9
            data[offset + 1] = 3
            data[offset + 3] = 0x4F
            struct.pack_into("<H", data, offset + 12, 9)
        cls.path.write_bytes(data)
        cls.spec = core.FirmwareSpec(cls.path, hashlib.sha256(data).hexdigest())

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_approved_merged_image(self):
        core.validate_firmware(self.spec)

    def test_incorrect_hash_rejected(self):
        with self.assertRaisesRegex(core.DeployerError, "SHA-256"):
            core.validate_firmware(replace(self.spec, sha256="0" * 64))

    def test_short_image_rejected_even_with_matching_hash(self):
        path = Path(self.folder.name) / "truncated.bin"
        path.write_bytes(b"\xe9" * 100)
        spec = core.FirmwareSpec(path, hashlib.sha256(path.read_bytes()).hexdigest())
        with self.assertRaisesRegex(core.DeployerError, "长度"):
            core.validate_firmware(spec)

    def test_manifest_cannot_allow_other_capacity(self):
        with self.assertRaisesRegex(core.DeployerError, "清单"):
            core.validate_firmware(replace(self.spec, flash_size_mb=8))

    def test_firmware_header_requires_s3_and_16mb(self):
        for offset, value in ((12, 0), (3, 0x3F), (0x10000 + 12, 5)):
            with self.subTest(offset=offset, value=value):
                path = Path(self.folder.name) / "wrong-header.bin"
                data = bytearray(self.path.read_bytes())
                data[offset] = value
                path.write_bytes(data)
                spec = core.FirmwareSpec(path, hashlib.sha256(data).hexdigest())
                with self.assertRaisesRegex(core.DeployerError, "镜像头"):
                    core.validate_firmware(spec)


class PortAndIdentityTests(unittest.TestCase):
    def test_multiple_ports_require_explicit_selection(self):
        other = replace(PORT, device="COM7")
        with self.assertRaisesRegex(core.DeployerError, "多个"):
            core.choose_port([PORT, other])
        self.assertEqual(core.choose_port([PORT, other], "com7"), other)

    def test_disconnected_explicit_port_is_not_replaced_by_another(self):
        with self.assertRaisesRegex(core.DeployerError, "已断开"):
            core.choose_port([PORT], "COM7")

    @patch("serial.tools.list_ports.comports")
    def test_candidate_filter_and_explicit_selection_preservation(self, comports):
        def raw(device, vid):
            return types.SimpleNamespace(device=device, description="device", vid=vid,
                pid=1, serial_number=None, location=None)
        comports.return_value = [raw("COM9", 0x9999), raw("COM3", 0x1A86),
                                raw("COM7", 0x10C4), raw("COM4", 0x303A)]
        self.assertEqual([p.device for p in core.list_candidate_ports()],
                         ["COM3", "COM4", "COM7"])
        self.assertEqual([p.device for p in core.list_candidate_ports("COM9")],
                         ["COM3", "COM4", "COM7", "COM9"])

    def test_reset_selection_uses_native_usb_only_for_known_pid(self):
        self.assertEqual(core.reset_mode_for(PORT), "usb-reset")
        self.assertEqual(core.reset_mode_for(replace(PORT, vid=0x1A86)), "default-reset")
        self.assertEqual(core.reset_mode_for(replace(PORT, pid=0x0001)), "default-reset")
        self.assertEqual(core.reset_mode_for(PORT, "no-reset"), "no-reset")
        with self.assertRaises(core.DeployerError):
            core.reset_mode_for(PORT, "no-reset-no-sync")

    def test_real_esptool_output_parses_and_requires_s3_n16r8(self):
        info = core.parse_device_output(DETECT_OUTPUT, PORT)
        self.assertEqual((info.flash_size_mb, info.psram_mb, info.mac),
                         (16, 8, "c4:9e:7e:18:c2:b0"))
        self.assertEqual(info.port_identity, PORT.identity)
        for wrong in (DETECT_OUTPUT.replace("ESP32-S3", "ESP32-C3"),
                      DETECT_OUTPUT.replace("16MB", "8MB"),
                      DETECT_OUTPUT.replace("PSRAM 8MB", "PSRAM 2MB"),
                      DETECT_OUTPUT.replace("c4:9e:7e:18:c2:b0", "00:00:00:00:00:00"),
                      DETECT_OUTPUT.replace("Detected flash size: 16MB", "")):
            with self.subTest(output=wrong), self.assertRaises(core.DeployerError):
                core.parse_device_output(wrong, PORT)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.spec = core.FirmwareSpec("not-opened-by-mocked-tests.bin", "a" * 64)
        self.info = core.parse_device_output(DETECT_OUTPUT, PORT)
        self.progress = []
        self.deployer = core.Deployer(self.spec, ["program.exe"],
                                      progress_callback=self.progress.append)

    @patch.object(core.Deployer, "validate_firmware")
    @patch("deployer_core.list_candidate_ports", return_value=[PORT])
    def test_flash_success_requires_verified_output_and_rechecks_device(self, _ports, _firmware):
        with patch.object(self.deployer, "_run_esptool", side_effect=[
                DETECT_OUTPUT, "Wrote 16777216 bytes\nHash of data verified.\n"]) as run:
            result = self.deployer.flash(PORT, self.info)
        self.assertEqual(result.mac, self.info.mac)
        self.assertEqual(self.progress, [0, 100])
        detect_args, write_args = run.call_args_list[0].args[0], run.call_args_list[1].args[0]
        self.assertIn("flash-id", detect_args)
        self.assertIn("--no-stub", detect_args)
        self.assertNotIn("write-flash", detect_args)
        self.assertIn("write-flash", write_args)
        self.assertIn("--guard-mac", write_args)
        self.assertEqual(write_args[write_args.index("--guard-mac") + 1], self.info.mac)
        self.assertEqual(write_args[-2], "0x0")
        self.assertNotIn("--force", write_args)
        self.assertNotIn("--erase-all", write_args)

    @patch.object(core.Deployer, "validate_firmware")
    @patch("deployer_core.list_candidate_ports", return_value=[PORT])
    def test_zero_exit_without_hash_verification_is_failure(self, _ports, _firmware):
        with patch.object(self.deployer, "_run_esptool", side_effect=[DETECT_OUTPUT, "Wrote bytes"]):
            with self.assertRaisesRegex(core.DeployerError, "没有固件校验成功证据"):
                self.deployer.flash(PORT, self.info)
        self.assertNotIn(100, self.progress)

    @patch.object(core.Deployer, "validate_firmware")
    @patch("deployer_core.list_candidate_ports", return_value=[PORT])
    def test_changed_chip_mac_never_starts_write(self, _ports, _firmware):
        with patch.object(self.deployer, "_run_esptool", return_value=
                DETECT_OUTPUT.replace("c4:9e:7e:18:c2:b0", "c4:9e:7e:18:c2:b1")) as run:
            with self.assertRaisesRegex(core.DeployerError, "身份已变化"):
                self.deployer.flash(PORT, self.info)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("write-flash", run.call_args.args[0])

    @patch("deployer_core.list_candidate_ports", return_value=[replace(PORT, serial_number="board-b")])
    def test_changed_serial_device_stops_before_any_process(self, _ports):
        with patch.object(self.deployer, "_run_esptool") as run:
            with self.assertRaisesRegex(core.DeployerError, "设备已改变"):
                self.deployer.detect(PORT)
        run.assert_not_called()

    @patch.object(core.Deployer, "validate_firmware")
    def test_stale_selected_port_cannot_be_flushed(self, _firmware):
        with patch.object(self.deployer, "_run_esptool") as run:
            with self.assertRaisesRegex(core.DeployerError, "设备选择已变化"):
                self.deployer.flash(replace(PORT, device="COM9"), self.info)
        run.assert_not_called()

    @patch("deployer_core.list_candidate_ports", return_value=[PORT])
    def test_cancel_between_detect_and_flash_is_permanent_for_instance(self, _ports):
        with patch.object(self.deployer, "_run_esptool", return_value=DETECT_OUTPUT) as run:
            detected = self.deployer.detect(PORT)
            self.deployer.cancel()
            with self.assertRaises(core.CancelledError):
                self.deployer.flash(PORT, detected)
            with self.assertRaises(core.CancelledError):
                self.deployer.detect(PORT)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(self.deployer._cancel.is_set())
        self.assertFalse(self.deployer._operation_lock.locked())


class WorkerProcessTests(unittest.TestCase):
    def make_deployer(self, script, **kwargs):
        return core.Deployer(core.FirmwareSpec("unused", "a" * 64),
                             [sys.executable, "-c", script], **kwargs)

    def test_frozen_and_source_command_prefix(self):
        with patch.object(sys, "frozen", True, create=True):
            self.assertEqual(core.default_command_prefix(), [sys.executable])
        with patch.object(sys, "frozen", False, create=True), patch.object(sys, "argv", ["main.py"]):
            self.assertEqual(core.default_command_prefix(),
                             [sys.executable, str(Path("main.py").resolve())])

    def test_real_child_log_tail_progress_and_cleanup(self):
        script = ("import sys,pathlib; "
                  "p=pathlib.Path(sys.argv[sys.argv.index('--worker-log')+1]); "
                  "p.write_text('中文日志\\nWriting at 0x00000000 51.2%\\r"
                  "Writing at 0x00000001 100.0%\\rHash of data verified.\\n',encoding='utf-8')")
        logs, progress, created_paths = [], [], []
        original_mkstemp = tempfile.mkstemp
        def mkstemp(**kwargs):
            fd, path = original_mkstemp(**kwargs)
            created_paths.append(path)
            return fd, path
        deployer = self.make_deployer(script, log_callback=logs.append,
                                     progress_callback=progress.append)
        with patch("deployer_core.tempfile.mkstemp", side_effect=mkstemp):
            output = deployer._run_esptool(["version"], 5)
        self.assertIn("中文日志", logs)
        self.assertIn("Hash of data verified.", output)
        self.assertEqual(progress, [51.2, 99.0])
        self.assertTrue(created_paths)
        self.assertTrue(all(not Path(p).exists() for p in created_paths))

    def test_nonzero_exit_fails_even_with_success_phrase(self):
        script = ("import sys,pathlib; "
                  "pathlib.Path(sys.argv[sys.argv.index('--worker-log')+1]).write_text("
                  "'Hash of data verified.\\n',encoding='utf-8'); sys.exit(2)")
        with self.assertRaisesRegex(core.DeployerError, "退出码 2"):
            self.make_deployer(script)._run_esptool(["version"], 5)

    def test_timeout_terminates_process_and_clears_handle(self):
        deployer = self.make_deployer("import time; time.sleep(10)")
        with self.assertRaisesRegex(core.DeployerError, "超时"):
            deployer._run_esptool(["version"], 0.1)
        self.assertIsNone(deployer._process)

    def test_cancel_terminates_process_and_clears_handle(self):
        deployer = self.make_deployer("import time; time.sleep(10)")
        timer = threading.Timer(0.15, deployer.cancel)
        timer.start()
        try:
            with self.assertRaises(core.CancelledError):
                deployer._run_esptool(["version"], 5)
        finally:
            timer.cancel()
        self.assertIsNone(deployer._process)

    def test_cancel_while_popen_creates_child_cannot_leave_orphan(self):
        deployer = self.make_deployer("import time; time.sleep(10)")
        original_popen = core.subprocess.Popen
        children = []
        def cancel_before_publication(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            children.append(child)
            # Simulate the cancellation request arriving after OS process
            # creation, before _run_esptool has received/published its handle.
            self.assertIsNone(deployer._process)
            deployer.cancel()
            return child
        with patch("deployer_core.subprocess.Popen", side_effect=cancel_before_publication):
            with self.assertRaises(core.CancelledError):
                deployer._run_esptool(["version"], 5)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertIsNone(deployer._process)
        self.assertTrue(deployer._cancel.is_set())

    def test_cancel_during_log_setup_does_not_start_process(self):
        deployer = self.make_deployer("import time; time.sleep(10)")
        original_mkstemp = tempfile.mkstemp
        def cancel_before_spawn(**kwargs):
            result = original_mkstemp(**kwargs)
            deployer.cancel()
            return result
        with patch("deployer_core.tempfile.mkstemp", side_effect=cancel_before_spawn), \
                patch("deployer_core.subprocess.Popen") as spawn:
            with self.assertRaises(core.CancelledError):
                deployer._run_esptool(["version"], 5)
        spawn.assert_not_called()


class GuardedEntryTests(unittest.TestCase):
    def fake_esptool(self):
        fake = MagicMock()
        esp = fake.get_default_connected_device.return_value
        esp.secure_download_mode = False
        esp.CHIP_NAME = "ESP32-S3"
        esp.get_chip_features.return_value = ["Wi-Fi", "Embedded PSRAM 8MB"]
        esp.read_mac.return_value = (0xC4, 0x9E, 0x7E, 0x18, 0xC2, 0xB0)
        fake.cmds.detect_flash_size.return_value = "16MB"
        return fake, esp

    def args(self):
        return ["--chip", "esp32s3", "--port", "COM3", "--before", "no-reset",
                "write-flash", "--flash-size", "16MB", "0x0", "approved.bin"]

    @patch("deployer_core.validate_firmware")
    def test_guard_reuses_exact_validated_serial_connection(self, validate):
        fake, esp = self.fake_esptool()
        with patch.dict(sys.modules, {"esptool": fake}):
            core._guarded_write(self.args(), "c4:9e:7e:18:c2:b0", "a" * 64, core.FLASH_BYTES)
        fake.main.assert_called_once_with(self.args(), esp=esp)
        esp._port.close.assert_called_once()
        validate.assert_called_once()

    @patch("deployer_core.validate_firmware")
    def test_guard_refuses_swapped_board_on_live_write_connection(self, _validate):
        fake, esp = self.fake_esptool()
        with patch.dict(sys.modules, {"esptool": fake}):
            with self.assertRaisesRegex(core.DeployerError, "身份发生变化"):
                core._guarded_write(self.args(), "c4:9e:7e:18:c2:b1", "a" * 64, core.FLASH_BYTES)
        fake.main.assert_not_called()
        esp._port.close.assert_called_once()

    @patch("deployer_core.validate_firmware")
    def test_guard_refuses_security_mode_and_wrong_flash(self, _validate):
        for protected, size in ((True, "16MB"), (False, "8MB")):
            with self.subTest(protected=protected, size=size):
                fake, esp = self.fake_esptool()
                esp.secure_download_mode = protected
                fake.cmds.detect_flash_size.return_value = size
                with patch.dict(sys.modules, {"esptool": fake}), self.assertRaises(core.DeployerError):
                    core._guarded_write(self.args(), "c4:9e:7e:18:c2:b0", "a" * 64, core.FLASH_BYTES)
                fake.main.assert_not_called()
                esp._port.close.assert_called_once()

    def test_entry_refuses_unguarded_write_and_force_without_importing_esptool(self):
        for args in (self.args(), ["--force", "write-flash", "0x0", "x.bin"],
                     ["erase-flash"], ["--ignore-flash-encryption-efuse-setting", "version"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()):
                self.assertNotEqual(core.esptool_entry(args), 0)

    def test_entry_error_log_utf8_and_stream_restoration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker.log"
            stdout, stderr = sys.stdout, sys.stderr
            self.assertEqual(core.esptool_entry(["--worker-log", str(path), "erase-flash"]), 2)
            self.assertIn("不允许", path.read_text(encoding="utf-8"))
            self.assertIs(sys.stdout, stdout)
            self.assertIs(sys.stderr, stderr)


if __name__ == "__main__":
    unittest.main()
