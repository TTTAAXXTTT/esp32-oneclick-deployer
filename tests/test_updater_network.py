"""Proxy handoff to the bundled CLI; no external process or network is used."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import updater


class CliNetworkTests(unittest.TestCase):
    def run_cli(self, proxy, bypass=False, output=b"Version: 1.5.1\n", code=0):
        logs, captured = [], {}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = updater.UpdateManager(root, root / "arduino-cli.exe", log_callback=logs.append)
            def launch(command, **kwargs):
                captured.update(kwargs["env"])
                kwargs["stdout"].write(output)
                kwargs["stdout"].flush()
                process = mock.Mock()
                process.poll.return_value = code
                return process
            with mock.patch.object(updater.urllib.request, "getproxies", return_value=proxy), \
                    mock.patch.object(updater.urllib.request, "proxy_bypass", return_value=bypass), \
                    mock.patch.object(updater.subprocess, "Popen", side_effect=launch), \
                    mock.patch.dict(updater.os.environ, {"ARDUINO_NETWORK_PROXY": "http://stale.invalid:1"}):
                try:
                    result = manager._run(["version"])
                except updater.UpdateError as error:
                    result = str(error)
            self.assertFalse((root / "arduino-cli.yaml").exists())
        return captured, logs, result

    def test_https_proxy_is_forwarded_only_in_child_environment(self):
        environment, _, output = self.run_cli({"https": "http://proxy.invalid:8080"})
        self.assertEqual(environment["ARDUINO_NETWORK_PROXY"], "http://proxy.invalid:8080")
        self.assertIn("1.5.1", output)

    def test_github_proxy_bypass_is_respected(self):
        environment, _, _ = self.run_cli({"https": "http://proxy.invalid:8080"}, bypass=True)
        self.assertNotIn("ARDUINO_NETWORK_PROXY", environment)

    def test_direct_network_does_not_inherit_stale_global_arduino_proxy(self):
        environment, _, _ = self.run_cli({})
        self.assertNotIn("ARDUINO_NETWORK_PROXY", environment)

    def test_proxy_credentials_are_redacted_from_logs_and_failure(self):
        proxy = "http://alice:private-password@proxy.invalid:8080"
        environment, logs, error = self.run_cli({"https": proxy},
            output=f"Download failed through {proxy}\n".encode(), code=1)
        self.assertEqual(environment["ARDUINO_NETWORK_PROXY"], proxy)
        self.assertNotIn("private-password", error)
        self.assertNotIn("private-password", "\n".join(logs))
        self.assertIn("configured proxy", error)

    def test_proxy_without_scheme_is_normalized(self):
        environment, _, _ = self.run_cli({"https": "proxy.invalid:8080"})
        self.assertEqual(environment["ARDUINO_NETWORK_PROXY"], "http://proxy.invalid:8080")


if __name__ == "__main__":
    unittest.main()
