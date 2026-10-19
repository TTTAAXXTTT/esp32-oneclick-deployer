"""Update tests use temporary sources/builds; no serial port or real install."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock
import urllib.error
import zipfile

from deployer_core import CancelledError
import updater


COMMIT = "a" * 40
OTHER = "b" * 40


def packet(payload):
    return f"{len(payload) + 4:04x}".encode() + payload


def source_files(root):
    sketch = root / "hardware" / "hid_bridge"
    sketch.mkdir(parents=True)
    (sketch / "hid_bridge.ino").write_bytes(b"void setup(){}\nvoid loop(){}\n")
    (sketch / "nimble_cfg.h").write_bytes(b"#pragma once\n")
    (root / "LICENSE").write_text("GPL and MIT notices", encoding="utf-8")
    return sketch


def valid_image(path):
    data = bytearray(b"\xff" * 16777216)
    for offset in (0, 0x10000):
        data[offset] = 0xE9
        data[offset + 1] = 3
        data[offset + 3] = 0x40
        struct.pack_into("<H", data, offset + 12, 9)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class FakeResponse(io.BytesIO):
    def __init__(self, content, length=None, url=updater.REF_URL):
        super().__init__(content)
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self.url = url

    def geturl(self):
        return self.url


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="更新 test ")
        self.root = Path(self.temporary.name)
        self.logs = []
        self.manager = updater.UpdateManager(self.root / "cache", self.root / "tools" / "arduino-cli.exe",
                                             log_callback=self.logs.append)
        self.manager.cache_root.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def candidate(self):
        source = self.manager.cache_root / "sources" / COMMIT / "source"
        sketch = source_files(source)
        return updater.UpdateCandidate(COMMIT, updater.hardware_digest(sketch), source, True)

    def archive(self, members=None):
        path = self.root / "source.zip"
        if members is None:
            prefix = f"esp32-iphone-agent-{COMMIT}/"
            members = [(prefix + "hardware/hid_bridge/hid_bridge.ino", b"void setup(){}"),
                       (prefix + "hardware/hid_bridge/nimble_cfg.h", b"#pragma once"),
                       (prefix + "LICENSE", b"MIT")]
        with zipfile.ZipFile(path, "w") as bundle:
            for name, data in members:
                bundle.writestr(name, data)
        return path

    def compile_fake(self, args, **kwargs):
        self.assertEqual(args[0], "compile")
        self.assertNotIn("--upload", args)
        self.assertNotIn("--port", args)
        self.assertIn(updater.FQBN, args)
        build = Path(args[args.index("--build-path") + 1])
        valid_image(build / "hid_bridge.ino.merged.bin")
        flags = [v for v in args if v.startswith("compiler.")]
        self.assertEqual(len(flags), 2)
        self.assertTrue(all('=-include "' in f and f.endswith('nimble_cfg.h"') for f in flags))
        return "compiled"

    def test_git_protocol_exact_main_not_head(self):
        data = packet(b"# service=git-upload-pack\n") + b"0000"
        data += packet((OTHER + " HEAD\0multi_ack\n").encode())
        data += packet((COMMIT + " refs/heads/main\n").encode()) + b"0000"
        self.assertEqual(updater.parse_main_advertisement(data), COMMIT)

    def test_git_rejects_bad_or_ambiguous_data(self):
        main = packet((COMMIT + " refs/heads/main\n").encode())
        for data in (b"nope", b"0008ab", b"0003", main + main, b"0000"):
            with self.subTest(data=data), self.assertRaises(updater.UpdateError):
                updater.parse_main_advertisement(data)

    def test_hardware_digest_includes_names_and_contents(self):
        sketch = source_files(self.root / "one")
        digest = updater.hardware_digest(sketch)
        (sketch / "extra.h").write_bytes(b"1")
        self.assertNotEqual(digest, updater.hardware_digest(sketch))
        changed = updater.hardware_digest(sketch)
        (sketch / "extra.h").rename(sketch / "renamed.h")
        self.assertNotEqual(changed, updater.hardware_digest(sketch))

    def test_digest_rejects_incomplete_sketch(self):
        with self.assertRaises(updater.UpdateError):
            updater.hardware_digest(self.root)

    def test_download_https_and_hosts_only(self):
        for url in ("http://github.com/x", "https://github.com.evil.test/x",
                    "https://user@github.com/x", "https://github.com:444/x"):
            with self.subTest(url=url), self.assertRaises(updater.UpdateError):
                self.manager._download(url, self.root / "download", 10)

    def test_download_declared_and_streamed_limits(self):
        for response in (FakeResponse(b"123", 20), FakeResponse(b"123456"), FakeResponse(b"123", 5)):
            with self.subTest(response=response), mock.patch.object(self.manager._opener, "open", return_value=response):
                with self.assertRaises(updater.UpdateError):
                    self.manager._download(updater.REF_URL, self.root / "download", 5)
                self.assertFalse((self.root / "download").exists())

    def test_network_failure_preserves_active(self):
        active = self.manager.cache_root / "active.json"
        active.write_bytes(b"last known good")
        with mock.patch.object(self.manager._opener, "open", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(updater.UpdateError):
                self.manager.check({})
        self.assertEqual(active.read_bytes(), b"last known good")

    def test_atom_fallback_is_bounded_and_parses_commit(self):
        xml = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>'
               f'tag:github.com,2008:Grit::Commit/{COMMIT}</id></entry></feed>').encode()

        def download(url, path, limit):
            if url == updater.REF_URL:
                raise updater.UpdateError("403")
            self.assertEqual(limit, 1024 * 1024)
            path.write_bytes(xml)

        with mock.patch.object(self.manager, "_download", side_effect=download):
            self.assertEqual(self.manager._resolve_main(), COMMIT)

    def test_extract_valid_source(self):
        source = self.manager._extract_source(self.archive(), self.root / "extracted", COMMIT)
        self.assertTrue((source / "hardware" / "hid_bridge" / "hid_bridge.ino").exists())

    def test_zip_traversal_collision_and_symlink_rejected(self):
        prefix = f"esp32-iphone-agent-{COMMIT}/"
        cases = [[(prefix + "../escape", b"x")],
                 [(prefix + "A.txt", b"x"), (prefix + "a.txt", b"y")],
                 [(prefix + "file", b"x"), (prefix + "file/child", b"x")],
                 [(prefix + "C:ads", b"x")], [(prefix + "a\\b", b"x")]]
        for members in cases:
            with self.subTest(members=members), self.assertRaises(updater.UpdateError):
                self.manager._extract_source(self.archive(members), self.root / "rejected", COMMIT)
        link = zipfile.ZipInfo(prefix + "link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(updater.UpdateError):
            self.manager._extract_source(self.archive([(link, b"../../outside")]), self.root / "symlink", COMMIT)
        self.assertFalse((self.root / "escape").exists())

    def test_corrupt_archive_rejected(self):
        path = self.root / "broken.zip"
        path.write_bytes(b"not a zip")
        with self.assertRaises(updater.UpdateError):
            self.manager._extract_source(path, self.root / "rejected", COMMIT)

    def test_check_ignores_nonhardware_updates_and_does_not_build(self):
        candidate = self.candidate()
        with mock.patch.object(self.manager, "_resolve_main", return_value=COMMIT), \
                mock.patch.object(self.manager, "_ensure_tools") as install:
            result = self.manager.check({"source_commit": OTHER, "hardware_sha256": candidate.hardware_sha256})
        self.assertFalse(result.available)
        install.assert_not_called()

    def test_check_downloads_pinned_commit(self):
        archive = self.archive()

        def download(url, path, limit):
            self.assertTrue(url.endswith("/zip/" + COMMIT))
            self.assertEqual(limit, updater.MAX_ARCHIVE_BYTES)
            path.write_bytes(archive.read_bytes())

        with mock.patch.object(self.manager, "_resolve_main", return_value=COMMIT), \
                mock.patch.object(self.manager, "_download", side_effect=download):
            result = self.manager.check({"hardware_sha256": "c" * 64})
        self.assertTrue(result.available)
        self.assertTrue((result.source_dir.parent / "official-source.zip").exists())

    def test_prepare_validates_and_atomically_activates(self):
        candidate = self.candidate()
        with mock.patch.object(self.manager, "_ensure_tools"), \
                mock.patch.object(self.manager, "_run", side_effect=self.compile_fake):
            manifest, spec = self.manager.prepare(candidate)
        self.assertEqual(manifest["source_commit"], COMMIT)
        self.assertEqual(Path(spec.path).stat().st_size, 16777216)
        cached = self.manager.load_cached()
        self.assertIsNotNone(cached)
        self.assertEqual(cached[0]["sha256"], manifest["sha256"])
        Path(spec.path).write_bytes(b"corrupt")
        self.assertIsNone(self.manager.load_cached())

    def test_build_failure_preserves_active(self):
        candidate = self.candidate()
        active = self.manager.cache_root / "active.json"
        active.write_bytes(b"previous")
        with mock.patch.object(self.manager, "_ensure_tools"), \
                mock.patch.object(self.manager, "_run", side_effect=updater.UpdateError("compile failed")):
            with self.assertRaises(updater.UpdateError):
                self.manager.prepare(candidate)
        self.assertEqual(active.read_bytes(), b"previous")

    def test_corrupt_build_never_activates(self):
        candidate = self.candidate()

        def corrupt(args, **kwargs):
            path = Path(args[args.index("--build-path") + 1])
            path.mkdir()
            (path / "hid_bridge.ino.merged.bin").write_bytes(b"invalid")

        with mock.patch.object(self.manager, "_ensure_tools"), mock.patch.object(self.manager, "_run", side_effect=corrupt):
            with self.assertRaises(updater.DeployerError):
                self.manager.prepare(candidate)
        self.assertFalse((self.manager.cache_root / "active.json").exists())

    def test_cancel_after_build_keeps_previous_active(self):
        candidate = self.candidate()
        active = self.manager.cache_root / "active.json"
        active.write_bytes(b"previous")

        def cancel(args, **kwargs):
            self.compile_fake(args, **kwargs)
            self.manager.cancel()

        with mock.patch.object(self.manager, "_ensure_tools"), mock.patch.object(self.manager, "_run", side_effect=cancel):
            with self.assertRaises(CancelledError):
                self.manager.prepare(candidate)
        self.assertEqual(active.read_bytes(), b"previous")

    def test_cancel_is_permanent_and_never_launches(self):
        self.manager.cancel()
        with mock.patch("updater.subprocess.Popen") as popen:
            with self.assertRaises(CancelledError):
                self.manager._run(["version"])
            popen.assert_not_called()

    def test_prepare_rejects_changed_or_external_sources(self):
        candidate = self.candidate()
        (candidate.source_dir / "hardware" / "hid_bridge" / "nimble_cfg.h").write_bytes(b"changed")
        with self.assertRaises(updater.UpdateError):
            self.manager.prepare(candidate)
        external = updater.UpdateCandidate(COMMIT, "c" * 64, self.root, True)
        with self.assertRaises(updater.UpdateError):
            self.manager.prepare(external)

    def test_load_cached_invalid_records_fail_closed(self):
        active = self.manager.cache_root / "active.json"
        for value in ([], 5, None, "x", {"schema": 1, "directory": "../outside"}, {"schema": 99}):
            with self.subTest(value=value):
                active.write_text(json.dumps(value), encoding="utf-8")
                self.assertIsNone(self.manager.load_cached())

    def test_verify_cli_hash_version_and_size(self):
        path = self.root / "tools" / "arduino-cli.exe"
        path.parent.mkdir()
        path.write_bytes(b"tool")
        manifest = {"schema_version": 1, "arduino_cli": {
            "version": "1.5.1", "filename": path.name, "size": 4, "platform": "windows-amd64",
            "sha256": hashlib.sha256(b"tool").hexdigest()}}
        (path.parent / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        updater.verify_cli(path)
        path.write_bytes(b"evil")
        with self.assertRaises(updater.UpdateError):
            updater.verify_cli(path)


if __name__ == "__main__":
    unittest.main()
