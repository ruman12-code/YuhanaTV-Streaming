"""Playlist-tree validation tests, including circular references (spec section 33)."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.validators.m3u_validator import M3UValidator

BASE = "https://example.test/playlists"


def cfg_for(root):
    c = config.load(use_cache=False)
    c["site"]["base_url"] = "https://example.test"
    c["site"]["playlist_path"] = "/playlists"
    return c


def write(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def link(name, url):
    return f'#EXTINF:0 type="playlist",{name}\n{url}\n'


def stream(name, url):
    return f'#EXTINF:-1 type="stream",{name}\n{url}\n'


class TestTree(unittest.TestCase):
    def _run(self, files, entry="master.m3u"):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for rel, body in files.items():
            write(root, rel, body)
        v = M3UValidator(cfg_for(root), root)
        return v.validate_tree(entry)

    def test_valid_tree_passes(self):
        r = self._run({
            "master.m3u": "#EXTM3U\n" + link("Live", f"{BASE}/live/live-tv.m3u"),
            "live/live-tv.m3u": "#EXTM3U\n" + link("BD", f"{BASE}/live/bangladesh.m3u"),
            "live/bangladesh.m3u": "#EXTM3U\n" + stream("BTV", "https://o.test/a.m3u8"),
        })
        self.assertTrue(r["ok"], r["issues"])
        self.assertEqual(r["cycles"], [])

    def test_direct_cycle_a_to_a_is_rejected(self):
        r = self._run({"master.m3u": "#EXTM3U\n" + link("Self", f"{BASE}/master.m3u")})
        self.assertFalse(r["ok"])
        self.assertTrue(r["cycles"])

    def test_three_node_cycle_a_b_c_a_is_rejected(self):
        """The exact case named in the specification: A -> B -> C -> A."""
        r = self._run({
            "master.m3u": "#EXTM3U\n" + link("A", f"{BASE}/a.m3u"),
            "a.m3u": "#EXTM3U\n" + link("B", f"{BASE}/b.m3u"),
            "b.m3u": "#EXTM3U\n" + link("C", f"{BASE}/c.m3u"),
            "c.m3u": "#EXTM3U\n" + link("A again", f"{BASE}/a.m3u"),
        })
        self.assertFalse(r["ok"], "a cycle must fail validation")
        self.assertEqual(len(r["cycles"]), 1)
        cycle = r["cycles"][0]
        for node in ("a.m3u", "b.m3u", "c.m3u"):
            self.assertIn(node, cycle)

    def test_missing_child_playlist_is_an_error(self):
        r = self._run({"master.m3u": "#EXTM3U\n" + link("Gone", f"{BASE}/nope.m3u")})
        self.assertFalse(r["ok"])
        self.assertTrue(any(i["code"] == "MISSING_CHILD" for i in r["issues"]))

    def test_unreachable_playlist_is_warned(self):
        r = self._run({
            "master.m3u": "#EXTM3U\n" + stream("A", "https://o.test/a.m3u8"),
            "orphan.m3u": "#EXTM3U\n" + stream("B", "https://o.test/b.m3u8"),
        })
        self.assertIn("orphan.m3u", r["unreachable"])

    def test_missing_header_is_an_error(self):
        r = self._run({"master.m3u": stream("A", "https://o.test/a.m3u8")})
        self.assertFalse(r["ok"])
        self.assertTrue(any(i["code"] == "HEADER" for i in r["issues"]))

    def test_bom_is_an_error(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "master.m3u").write_bytes(
            b"\xef\xbb\xbf#EXTM3U\n" + stream("A", "https://o.test/a.m3u8").encode())
        r = M3UValidator(cfg_for(root), root).validate_tree()
        self.assertTrue(any(i["code"] == "BOM" for i in r["issues"]))

    def test_dangerous_url_is_rejected(self):
        r = self._run({"master.m3u": "#EXTM3U\n" + stream("X", "javascript:alert(1)")})
        self.assertFalse(r["ok"])
        self.assertTrue(any(i["code"] == "BAD_URL" for i in r["issues"]))

    def test_extinf_without_url_is_an_error(self):
        r = self._run({"master.m3u": '#EXTM3U\n#EXTINF:-1 type="stream",Orphan\n'})
        self.assertFalse(r["ok"])
        self.assertTrue(any(i["code"] in ("ORPHAN_EXTINF", "EMPTY") for i in r["issues"]))

    def test_bad_extsize_value_is_an_error(self):
        r = self._run({"master.m3u": '#EXTM3U\n#EXTINF:-1,A\n#EXTSIZE: huge\nhttps://o.test/a.m3u8\n'})
        self.assertFalse(r["ok"])
        self.assertTrue(any(i["code"] == "SIZE_VALUE" for i in r["issues"]))

    def test_bad_type_value_is_an_error(self):
        r = self._run({"master.m3u": '#EXTM3U\n#EXTINF:-1 type="hologram",A\nhttps://o.test/a.m3u8\n'})
        self.assertFalse(r["ok"])
        self.assertTrue(any(i["code"] == "TYPE_VALUE" for i in r["issues"]))

    def tearDown(self):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
