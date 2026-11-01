"""Verified HTTPS using both the system trust store and bundled public roots.

Windows root stores may not yet contain GitHub's public issuer on a customer's
machine. Frozen applications must carry certifi's PEM file as runtime data. Keep
system roots too, so an administrator-installed proxy CA continues to work.
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request

import certifi


class CertificateBundleError(OSError):
    """The installed application's bundled CA file is missing or invalid."""


def create_https_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        context.load_verify_locations(cafile=certifi.where())
    except (OSError, ValueError) as exc:
        raise CertificateBundleError(
            "无法加载软件附带的 CA 证书包，请重新完整解压最新版本的软件。"
        ) from exc
    context.set_alpn_protocols(["http/1.1"])
    return context


class _VerifiedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self):
        # Recent Python patch releases eagerly construct a default context in
        # HTTPSHandler.__init__. Pass an explicit, secure placeholder instead.
        super().__init__(context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        self._roots_loaded = False

    def https_open(self, request):
        # Only updates need TLS. Missing runtime data must not stop the GUI from
        # starting or prevent using the already validated, bundled firmware.
        if not self._roots_loaded:
            self._context = create_https_context()
            self._roots_loaded = True
        return super().https_open(request)


def build_https_opener(*handlers) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_VerifiedHTTPSHandler(), *handlers)


def is_certificate_verification_error(error: BaseException) -> bool:
    seen = set()
    while isinstance(error, BaseException) and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, ssl.SSLCertVerificationError):
            return True
        if isinstance(error, ssl.SSLError) and getattr(error, "reason", None) == "CERTIFICATE_VERIFY_FAILED":
            return True
        error = error.reason if isinstance(error, urllib.error.URLError) else error.__cause__
    return False
