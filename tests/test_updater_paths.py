"""Keep the Windows linker away from unsupported Unicode output paths."""
from pathlib import Path
import json
import queue
import sys
import tempfile
import unittest
from unittest import mock

import updater
from main import Application


class CompilerPathTests(unittest.TestCase):
    def test_compiler_cache_is_private_and_does_not_use_global_profile(self):
        with tempfile.TemporaryDirectory(prefix="esp32-cli-") as directory:
            root = Path(directory)
            data = root / "arduino/data"
            core = data / "packages/esp32/hardware/esp32" / updater.CORE_VERSION / "platform.txt"
            core.parent.mkdir(parents=True)
            core.write_text("test core", encoding="utf-8")
            library = root / "arduino/user/libraries/NimBLE-Arduino/library.properties"
            library.parent.mkdir(parents=True)
            library.write_text("version=2.5.1\n", encoding="utf-8")
            (root / "arduino/ready.json").write_text(json.dumps({"cli": updater.CLI_VERSION,
                "core": updater.CORE_VERSION, "nimble": updater.NIMBLE_VERSION}), encoding="utf-8")
            manager = updater.UpdateManager(root, root / "arduino-cli.exe")
            with mock.patch.object(updater, "verify_cli"), \
                    mock.patch.object(manager, "_run", return_value="Version: 1.5.1") as run:
                manager._ensure_tools()
            config = json.loads((root / "arduino-cli.yaml").read_text("utf-8"))
            self.assertEqual(Path(config["build_cache"]["path"]), (root / "arduino/build-cache").resolve())
            self.assertNotIn("proxy", config["network"])
            run.assert_called_once_with(["version"], timeout=60)

    def test_default_update_cache_stays_in_application_data(self):
        app = Application.__new__(Application)
        app.data_root = Path(tempfile.gettempdir()) / "application-data"
        app.events = queue.Queue()
        with mock.patch.dict(updater.os.environ, {"ESP32_ONECLICK_UPDATE_CACHE": ""}):
            manager = app._new_update_manager()
        self.assertEqual(manager.cache_root, app.data_root / "updates")

    def test_explicit_update_cache_can_avoid_unsupported_profile_path(self):
        app = Application.__new__(Application)
        app.data_root = Path(tempfile.gettempdir()) / "用户目录"
        app.events = queue.Queue()
        cache = Path(tempfile.gettempdir()) / "ESP32 update cache"
        with mock.patch.dict(updater.os.environ, {"ESP32_ONECLICK_UPDATE_CACHE": str(cache)}):
            manager = app._new_update_manager()
        self.assertEqual(manager.cache_root, cache.absolute())

    def test_ascii_paths_with_spaces_are_preserved(self):
        path = Path("C:/Users/test/update cache")
        with mock.patch.object(updater.sys, "platform", "win32"):
            self.assertEqual(updater._compiler_directory(path), path.resolve())

    def test_unicode_windows_directory_is_rejected_before_compilation(self):
        path = Path("C:/Users/test/更新缓存")
        with mock.patch.object(updater.sys, "platform", "win32"):
            with self.assertRaisesRegex(updater.UpdateError, "当前固件保持不变"):
                updater._compiler_directory(path)

    def test_canonical_unicode_directory_is_checked_instead_of_alias(self):
        path = Path("C:/Users/test/UPDATE~1")
        with mock.patch.object(updater.sys, "platform", "win32"), \
                mock.patch.object(Path, "resolve", return_value=Path("C:/Users/test/更新缓存")):
            with self.assertRaisesRegex(updater.UpdateError, "英文字符"):
                updater._compiler_directory(path)

    def test_non_windows_unicode_paths_are_preserved(self):
        path = Path("/tmp/更新缓存")
        with mock.patch.object(updater.sys, "platform", "linux"):
            self.assertEqual(updater._compiler_directory(path), path)

    @unittest.skipUnless(sys.platform == "win32", "Windows filesystem API")
    def test_real_unicode_cache_fails_before_install_and_preserves_firmware(self):
        with tempfile.TemporaryDirectory(prefix="链接 编译路径 ") as directory:
            path = Path(directory)
            active = path / "active.json"
            active.write_bytes(b"previous firmware")
            manager = updater.UpdateManager(path, path / "arduino-cli.exe")
            with mock.patch.object(updater, "verify_cli"), mock.patch.object(manager, "_run") as run:
                with self.assertRaisesRegex(updater.UpdateError, "英文字符"):
                    manager._ensure_tools()
            run.assert_not_called()
            self.assertEqual(active.read_bytes(), b"previous firmware")


if __name__ == "__main__":
    unittest.main()
