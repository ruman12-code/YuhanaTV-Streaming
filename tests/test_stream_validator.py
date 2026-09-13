"""Stream-validator classification tests against a local HLS origin."""
import sys, threading, unittest, http.server, socketserver, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.models import Channel
from src.validators.stream_validator import StreamValidator, resolution_label

MASTER = (b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=854x480\n480/i.m3u8\n'
          b'#EXT-X-STREAM-INF:BANDWIDTH=5200000,RESOLUTION=1920x1080,CODECS="avc1.640028"\n1080/i.m3u8\n')
MEDIA = b'#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg.ts\n'
VARIANT = b'#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\n../seg.ts\n'
ROUTES = {
    "/master.m3u8": MASTER, "/1080/i.m3u8": VARIANT, "/480/i.m3u8": VARIANT,
    "/media.m3u8": MEDIA, "/seg.ts": b"\x47" * 2048,
    "/empty.m3u8": b"#EXTM3U\n", "/html.m3u8": b"<html>no</html>",
    "/badseg.m3u8": b"#EXTM3U\n#EXTINF:6.0,\nmissing.ts\n",
    "/deadmaster.m3u8": b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=1280x720\ngone.m3u8\n',
}


class _H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/boom.m3u8":
            self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
        if p in ROUTES:
            body = ROUTES[p]
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()


class TestValidator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        cfg = config.load(use_cache=False)
        cfg["security"]["block_private_ip_targets"] = False
        cfg["validation"].update({"retries": 0, "per_host_delay_seconds": 0})
        self.v = StreamValidator(cfg)

    def _check(self, path):
        return self.v.validate(Channel(id="t", name="t",
                                       stream_url=f"http://127.0.0.1:{self.port}{path}"))

    def test_master_followed_to_segment_is_active(self):
        r = self._check("/master.m3u8")
        self.assertEqual(r.status, "ACTIVE")
        self.assertEqual(r.manifest_kind, "master")
        self.assertEqual(r.resolution, "1920x1080")
        self.assertEqual(r.resolution_label, "1080p")
        self.assertTrue(r.segment_ok)
        self.assertIn("segment", [s["name"] for s in r.stages])

    def test_media_playlist_with_segment_is_active(self):
        r = self._check("/media.m3u8")
        self.assertEqual(r.status, "ACTIVE")
        self.assertTrue(r.segment_ok)

    def test_master_with_dead_variant_is_not_active(self):
        r = self._check("/deadmaster.m3u8")
        self.assertEqual(r.status, "DEGRADED")

    def test_unfetchable_segment_is_degraded(self):
        r = self._check("/badseg.m3u8")
        self.assertEqual(r.status, "DEGRADED")
        self.assertFalse(r.segment_ok)

    def test_http_error_is_offline(self):
        self.assertEqual(self._check("/boom.m3u8").status, "OFFLINE")

    def test_non_manifest_body_is_invalid(self):
        self.assertEqual(self._check("/html.m3u8").status, "INVALID")

    def test_manifest_without_variants_or_segments_is_invalid(self):
        self.assertEqual(self._check("/empty.m3u8").status, "INVALID")

    def test_dns_failure_is_offline(self):
        r = self.v.validate(Channel(id="t", name="t",
                                    stream_url="https://no-such-host.invalid/a.m3u8"))
        self.assertEqual(r.status, "OFFLINE")

    def test_unsafe_url_is_invalid(self):
        r = self.v.validate(Channel(id="t", name="t", stream_url="javascript:alert(1)"))
        self.assertEqual(r.status, "INVALID")

    def test_resolution_labels_come_from_pixels_only(self):
        self.assertEqual(resolution_label(3840, 2160), "4K")
        self.assertEqual(resolution_label(1920, 1080), "1080p")
        self.assertEqual(resolution_label(1280, 720), "720p")
        self.assertEqual(resolution_label(854, 480), "480p")
        self.assertEqual(resolution_label(0, 0), "")


if __name__ == "__main__":
    unittest.main()


class _SlidingH(http.server.BaseHTTPRequestHandler):
    """A live origin whose oldest segment has already expired.

    Reproduces the real failure seen on the first CI run: a healthy live channel
    reported DEGRADED because the validator probed the oldest segment in the
    sliding window, which the origin had already dropped.
    """
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/live.m3u8":
            body = (b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:900\n"
                    b"#EXTINF:6.0,\nexpired.ts\n#EXTINF:6.0,\nmiddle.ts\n#EXTINF:6.0,\nfresh.ts\n")
            return self._ok(body, "application/vnd.apple.mpegurl")
        if p == "/vod.m3u8":
            body = (b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nfirst.ts\n"
                    b"#EXTINF:6.0,\nlast.ts\n#EXT-X-ENDLIST\n")
            return self._ok(body, "application/vnd.apple.mpegurl")
        if p == "/bom.m3u8":
            return self._ok(b"\xef\xbb\xbf\n\n#EXTM3U\n#EXTINF:6.0,\nfresh.ts\n",
                            "application/vnd.apple.mpegurl")
        if p == "/fresh.ts":
            return self._ok(b"\x47" * 1024, "video/mp2t")
        if p == "/first.ts":
            return self._ok(b"\x47" * 1024, "video/mp2t")
        # expired.ts, middle.ts, last.ts -> gone
        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
    def _ok(self, body, ctype):
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)


class TestSlidingWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SlidingH)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        cfg = config.load(use_cache=False)
        cfg["security"]["block_private_ip_targets"] = False
        cfg["validation"].update({"retries": 0, "per_host_delay_seconds": 0,
                                  "retry_backoff_seconds": [0, 0]})
        self.v = StreamValidator(cfg)

    def _check(self, path):
        return self.v.validate(Channel(id="t", name="t",
                                       stream_url=f"http://127.0.0.1:{self.port}{path}"))

    def test_live_playlist_probes_the_newest_segment(self):
        """The oldest segment is 404; the channel is still healthy."""
        r = self._check("/live.m3u8")
        self.assertEqual(r.status, "ACTIVE",
                         "probing an expired segment must not condemn a live channel")
        self.assertTrue(r.segment_ok)

    def test_vod_playlist_probes_the_first_segment(self):
        """An ENDLIST playlist does not slide, so a player starts at the front."""
        r = self._check("/vod.m3u8")
        self.assertEqual(r.status, "ACTIVE")
        self.assertTrue(r.segment_ok)

    def test_bom_and_leading_blank_lines_are_tolerated(self):
        r = self._check("/bom.m3u8")
        self.assertEqual(r.status, "ACTIVE")

    def test_segment_selection_helper(self):
        live = "#EXTM3U\n#EXTINF:6,\na.ts\n#EXTINF:6,\nb.ts\n#EXTINF:6,\nc.ts\n"
        vod = live + "#EXT-X-ENDLIST\n"
        base = "https://h/live/index.m3u8"
        self.assertEqual(self.v._probe_segment_url(live, base), "https://h/live/c.ts")
        self.assertEqual(self.v._probe_segment_url(vod, base), "https://h/live/a.ts")
        self.assertEqual(self.v._probe_segment_url("#EXTM3U\n", base), "")
