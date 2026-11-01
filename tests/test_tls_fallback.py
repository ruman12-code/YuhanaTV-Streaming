"""End-to-end: the TLS fallback against a real server with an untrusted cert.

The fallback shipped twice without this test and was broken both times - first
because urllib wraps certificate errors so the handler was unreachable, then
because the unverified context it needs had been stranded after a return and
was never built. Both bugs lived exactly on the path no test exercised. This
test runs that path: a real HTTPS server, a self-signed certificate, and the
real validator.
"""
import http.server
import shutil
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.models import Channel
from src.validators.stream_validator import StreamValidator

HAVE_OPENSSL = shutil.which("openssl") is not None

TS = bytes([0x47]) + bytes(187)
MANIFEST = (b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n"
            b"#EXT-X-MEDIA-SEQUENCE:1\n#EXTINF:4.0,\nseg1.ts\n#EXTINF:4.0,\nseg2.ts\n")


@unittest.skipUnless(HAVE_OPENSSL, "openssl not installed")
class TestTlsFallbackEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        key, cert = cls.tmp / "k.pem", cls.tmp / "c.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1",
                        "-subj", "/CN=localhost"],
                       check=True, capture_output=True, stdin=subprocess.DEVNULL)

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = MANIFEST if self.path.endswith(".m3u8") else TS * 400
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl"
                                 if self.path.endswith(".m3u8") else "video/mp2t")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        cls.httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        cls.httpd.socket = ctx.wrap_socket(cls.httpd.socket, server_side=True)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _validator(self, fallback=True):
        cfg = config.load(use_cache=False)
        cfg["security"]["block_private_ip_targets"] = False   # loopback, this test only
        cfg["validation"]["per_host_delay_seconds"] = 0
        cfg["validation"]["allow_unverified_tls_fallback"] = fallback
        cfg["validation"]["decode_check"] = False
        cfg["validation"]["retries"] = 0
        return StreamValidator(cfg)

    def _channel(self):
        return Channel(id="tls", name="TLS test", category="news",
                       stream_url=f"https://127.0.0.1:{self.port}/live.m3u8")

    def test_an_untrusted_certificate_is_rescued_by_the_fallback(self):
        r = self._validator().validate(self._channel())
        self.assertEqual(r.internal_error, "", "the probe itself must not crash")
        self.assertTrue(r.tls_unverified, "the fallback must actually have run")
        self.assertEqual(r.status, "ACTIVE", r.error)

    def test_without_the_fallback_the_same_stream_fails_on_the_certificate(self):
        """The control: proves the server really does present an untrusted
        chain, so the test above is measuring the fallback and nothing else."""
        r = self._validator(fallback=False).validate(self._channel())
        self.assertEqual(r.internal_error, "")
        self.assertEqual(r.status, "OFFLINE")
        self.assertIn("certificate", r.error.lower())


class TestABugIsNeverBlamedOnTheChannel(unittest.TestCase):
    """47 channels lost their tiles to a missing attribute in this code."""

    def test_a_programming_error_is_reported_as_unmeasured(self):
        v = StreamValidator(config.load(use_cache=False))

        def boom(channel):
            raise AttributeError("'StreamValidator' object has no attribute 'x'")
        v._validate = boom
        r = v.validate(Channel(id="a", name="a", stream_url="https://h/a.m3u8"))
        self.assertIn("AttributeError", r.internal_error)
        self.assertNotIn(r.status, ("OFFLINE", "INVALID", "DEGRADED"))

    def test_a_real_network_failure_is_still_a_failure(self):
        cfg = config.load(use_cache=False)
        cfg["validation"]["retries"] = 0
        v = StreamValidator(cfg)
        r = v.validate(Channel(id="a", name="a",
                               stream_url="https://nonexistent.invalid/a.m3u8"))
        self.assertEqual(r.internal_error, "")
        self.assertEqual(r.status, "OFFLINE")


class TestNoUnreachableCode(unittest.TestCase):
    """The TLS context was stranded after a `return` by an edit and never built.
    Nothing in this codebase should sit after a return, raise, break or
    continue in the same block."""

    def test_no_statement_follows_a_terminator(self):
        import ast
        root = Path(__file__).resolve().parent.parent
        files = (list((root / "src").rglob("*.py")) + list((root / "scripts").glob("*.py"))
                 + list((root / "tools").glob("*.py")))
        found = []
        for f in files:
            for node in ast.walk(ast.parse(f.read_text())):
                for field in ("body", "orelse", "finalbody"):
                    seq = getattr(node, field, None)
                    if not isinstance(seq, list):
                        continue
                    for a, b in zip(seq, seq[1:]):
                        if isinstance(a, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                            found.append(f"{f.relative_to(root)}:{b.lineno}")
        self.assertEqual(found, [], "unreachable code: " + ", ".join(found))
