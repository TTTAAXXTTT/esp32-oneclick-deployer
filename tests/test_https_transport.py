"""Real local TLS handshakes; never modify OS trust, contact a board or flash it."""
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
import zipfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import https_transport
import updater


class HttpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="TLS 证书测试 ")
        cls.root = Path(cls.temporary.name)
        cls.now = dt.datetime.now(dt.timezone.utc)
        cls.ca_key, cls.ca = cls.authority("Bundled test root")
        _, cls.system_ca = cls.authority("Existing system test root")
        cls.ca_file = cls.root / "cacert.pem"
        cls.ca_file.write_bytes(cls.ca.public_bytes(serialization.Encoding.PEM))
        cls.system_ca_file = cls.root / "system.pem"
        cls.system_ca_file.write_bytes(cls.system_ca.public_bytes(serialization.Encoding.PEM))
        cls.certificates = {}
        for label, host, expired in (("valid", "localhost", False),
                                      ("wrong-host", "other.invalid", False),
                                      ("expired", "localhost", True)):
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            certificate = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
                .issuer_name(cls.ca.subject).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(cls.now - dt.timedelta(days=2))
                .not_valid_after(cls.now + dt.timedelta(days=-1 if expired else 7))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
                .sign(cls.ca_key, hashes.SHA256()))
            cert_path, key_path = cls.root / f"{label}.pem", cls.root / f"{label}.key"
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            cls.certificates[label] = cert_path, key_path

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def authority(cls, name):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(cls.now - dt.timedelta(days=3))
            .not_valid_after(cls.now + dt.timedelta(days=14))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False, key_agreement=False,
                key_cert_sign=True, crl_sign=True, encipher_only=None, decipher_only=None), critical=True)
            .sign(key, hashes.SHA256()))
        return key, certificate

    @contextlib.contextmanager
    def server(self, certificate="valid", body=b"verified download", redirect=None):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.requests_seen += 1
                self.send_response(302 if redirect else 200)
                if redirect:
                    self.send_header("Location", redirect)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.requests_seen = 0
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*self.certificates[certificate])
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            yield f"https://localhost:{server.server_port}/download", server
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    @contextlib.contextmanager
    def roots(self, bundle=None):
        # Reproduce a machine with no preinstalled public roots without changing
        # this computer's Windows certificate store or global SSL configuration.
        with mock.patch.object(https_transport.ssl, "create_default_context",
                side_effect=lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)), \
                mock.patch.object(https_transport.certifi, "where", return_value=str(bundle or self.ca_file)):
            yield

    @contextlib.contextmanager
    def manager_for(self, url):
        original_validate = updater._validate_url
        def validate(value):
            if value != url:
                original_validate(value)
        with tempfile.TemporaryDirectory(prefix="TLS 下载 ") as directory, \
                mock.patch.object(updater, "_validate_url", side_effect=validate):
            root = Path(directory)
            manager = updater.UpdateManager(root, root / "arduino-cli.exe")
            yield manager, root

    def test_legacy_empty_trust_store_reproduces_certificate_error(self):
        with self.server() as (url, server):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)))
            with self.assertRaises(urllib.error.URLError) as caught:
                opener.open(url, timeout=3)
            self.assertTrue(https_transport.is_certificate_verification_error(caught.exception))
            self.assertEqual(server.requests_seen, 0)

    def test_bundled_roots_restore_download_with_empty_system_store(self):
        with self.server() as (url, server), self.roots(), self.manager_for(url) as (manager, root):
            manager._download(url, root / "source.zip", 1024)
            self.assertEqual((root / "source.zip").read_bytes(), b"verified download")
            self.assertEqual(server.requests_seen, 1)

    def test_system_roots_are_retained_alongside_bundle(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=str(self.system_ca_file))
        with mock.patch.object(https_transport.ssl, "create_default_context", return_value=context), \
                mock.patch.object(https_transport.certifi, "where", return_value=str(self.ca_file)):
            combined = https_transport.create_https_context()
        roots = {hashlib.sha256(cert).hexdigest() for cert in combined.get_ca_certs(binary_form=True)}
        for certificate in (self.ca, self.system_ca):
            self.assertIn(hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest(), roots)
        self.assertTrue(combined.check_hostname)
        self.assertEqual(combined.verify_mode, ssl.CERT_REQUIRED)

    def rejected_download(self, certificate, bundle=None):
        with self.server(certificate) as (url, server), self.roots(bundle), \
                self.manager_for(url) as (manager, root):
            active = root / "active.json"
            active.write_bytes(b"last-known-good")
            destination = root / "source.zip"
            destination.write_bytes(b"partial previous download")
            with self.assertRaises(updater.UpdateCertificateError) as caught:
                manager._download(url, destination, 1024)
            self.assertIn("HTTPS", str(caught.exception))
            self.assertIn("当前固件保持不变", str(caught.exception))
            self.assertEqual(active.read_bytes(), b"last-known-good")
            self.assertFalse(destination.exists())
            self.assertEqual(server.requests_seen, 0)

    def test_expired_server_certificate_is_rejected(self):
        self.rejected_download("expired")

    def test_wrong_hostname_is_rejected(self):
        self.rejected_download("wrong-host")

    def test_untrusted_issuer_is_rejected(self):
        self.rejected_download("valid", self.system_ca_file)

    def test_certificate_failure_does_not_retry_another_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = updater.UpdateManager(Path(directory), Path(directory) / "cli.exe")
            with mock.patch.object(manager, "_download", side_effect=updater.UpdateCertificateError("certificate")) as download:
                with self.assertRaises(updater.UpdateCertificateError):
                    manager._resolve_main()
            download.assert_called_once()

    def test_missing_bundle_fails_on_download_without_breaking_offline_manager(self):
        with self.server() as (url, server), self.roots(self.root / "missing.pem"), \
                self.manager_for(url) as (manager, root):
            self.assertIsNone(manager.load_cached())
            with self.assertRaisesRegex(updater.UpdateError, "重新完整解压"):
                manager._download(url, root / "source.zip", 1024)
            self.assertFalse((root / "source.zip").exists())
            self.assertEqual(server.requests_seen, 0)

    def test_invalid_bundle_fails_closed(self):
        invalid = self.root / "invalid.pem"
        invalid.write_text("not a certificate", encoding="utf-8")
        with self.roots(invalid), self.assertRaises(https_transport.CertificateBundleError):
            https_transport.create_https_context()

    def test_opener_loads_certificates_lazily(self):
        with mock.patch.object(https_transport, "create_https_context") as create:
            https_transport.build_https_opener()
            create.assert_not_called()

    def test_redirect_to_http_is_still_rejected(self):
        with self.server(redirect="http://github.com/unsafe") as (url, _), self.roots(), \
                self.manager_for(url) as (manager, root):
            with self.assertRaisesRegex(updater.UpdateError, "HTTPS"):
                manager._download(url, root / "source.zip", 1024)
            self.assertFalse((root / "source.zip").exists())

    def test_real_public_bundle_keeps_verification_enabled(self):
        context = https_transport.create_https_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(context.cert_store_stats()["x509_ca"], 100)

    def test_unrelated_network_errors_are_not_reported_as_certificate_errors(self):
        for error in (urllib.error.URLError("offline"), TimeoutError(), ssl.SSLError("connection reset")):
            with self.subTest(error=type(error).__name__):
                self.assertFalse(https_transport.is_certificate_verification_error(error))

    def test_wrapped_certificate_errors_and_cycles(self):
        certificate = ssl.SSLCertVerificationError(1, "untrusted issuer")
        self.assertTrue(https_transport.is_certificate_verification_error(urllib.error.URLError(certificate)))
        cycle = urllib.error.URLError("placeholder")
        cycle.reason = cycle
        self.assertFalse(https_transport.is_certificate_verification_error(cycle))

    def build_inputs(self, root, corrupt=False):
        spec = importlib.util.spec_from_file_location("prepare_build_inputs",
            Path(__file__).resolve().parents[1] / "prepare-build-inputs.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT = root
        (root / "firmware").mkdir()
        (root / "tools").mkdir()
        firmware, cli = b"verified firmware", b"verified build tool"
        firmware_meta = {"sha256": hashlib.sha256(firmware).hexdigest()}
        cli_meta = {"sha256": hashlib.sha256(cli).hexdigest()}
        (root / "firmware/manifest.json").write_text(json.dumps(firmware_meta), encoding="utf-8")
        (root / "tools/manifest.json").write_text(json.dumps({"arduino_cli": cli_meta}), encoding="utf-8")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as output:
            prefix = "ESP32-OneClick-Source-v1.1.1/"
            output.writestr(prefix + "firmware/agent-n16r8.bin", firmware)
            output.writestr(prefix + "tools/arduino-cli.exe", b"corrupt" if corrupt else cli)
        return module, archive.getvalue(), firmware, cli

    def test_source_bootstrap_download_uses_bundled_trust(self):
        with tempfile.TemporaryDirectory(prefix="源码 下载 ") as directory:
            root = Path(directory)
            module, data, firmware, cli = self.build_inputs(root)
            with self.server(body=data) as (url, _), self.roots():
                module.URL = url
                module.main()
            self.assertEqual((root / "firmware/agent-n16r8.bin").read_bytes(), firmware)
            self.assertEqual((root / "tools/arduino-cli.exe").read_bytes(), cli)

    def test_source_bootstrap_bad_hash_preserves_both_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module, data, _, _ = self.build_inputs(root, corrupt=True)
            (root / "firmware/agent-n16r8.bin").write_bytes(b"previous firmware")
            (root / "tools/arduino-cli.exe").write_bytes(b"previous tool")
            with self.server(body=data) as (url, _), self.roots():
                module.URL = url
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    module.main()
            self.assertEqual((root / "firmware/agent-n16r8.bin").read_bytes(), b"previous firmware")
            self.assertEqual((root / "tools/arduino-cli.exe").read_bytes(), b"previous tool")

    def test_source_bootstrap_offline_when_inputs_already_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module, _, firmware, cli = self.build_inputs(root)
            (root / "firmware/agent-n16r8.bin").write_bytes(firmware)
            (root / "tools/arduino-cli.exe").write_bytes(cli)
            with mock.patch.object(module, "build_https_opener") as open_https:
                module.main()
                open_https.assert_not_called()


if __name__ == "__main__":
    unittest.main()
