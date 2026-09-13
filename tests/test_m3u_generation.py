"""M3U generation and SS IPTV attribute tests (spec section 33)."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.generators.m3u import M3UBuilder, clean_attr, clean_text


class TestBuilder(unittest.TestCase):
    def test_header_and_utf8(self):
        b = M3UBuilder(tvg_url="https://x/epg.xml", default_size="medium")
        b.add_stream("Test", "https://x/a.m3u8")
        out = b.render()
        self.assertTrue(out.startswith("#EXTM3U "))
        self.assertIn('x-tvg-url="https://x/epg.xml"', out)
        self.assertIn('size="medium"', out)
        out.encode("utf-8")  # must be encodable
        self.assertFalse(out.encode("utf-8").startswith(b"\xef\xbb\xbf"), "no BOM")

    def test_live_duration_is_minus_one(self):
        b = M3UBuilder(); b.add_stream("L", "https://x/a.m3u8")
        self.assertIn("#EXTINF:-1 ", b.render())

    def test_library_items_use_duration_zero(self):
        b = M3UBuilder()
        b.add_video("Film", "https://x/a.mp4")
        b.add_playlist("Cat", "https://x/c.m3u")
        for line in b.render().splitlines():
            if line.startswith("#EXTINF"):
                self.assertTrue(line.startswith("#EXTINF:0 "), line)

    def test_extsize_extbg_sit_between_extinf_and_url(self):
        b = M3UBuilder()
        b.add_playlist("Cat", "https://x/c.m3u", size="big", background="#046f55")
        lines = b.render().splitlines()
        i = lines.index("#EXTINF:0 type=\"playlist\",Cat")
        self.assertEqual(lines[i + 1], "#EXTSIZE: big")
        self.assertEqual(lines[i + 2], "#EXTBG: #046f55")
        self.assertEqual(lines[i + 3], "https://x/c.m3u")

    def test_invalid_size_is_dropped_not_emitted(self):
        b = M3UBuilder(); b.add_playlist("C", "https://x/c.m3u", size="enormous")
        self.assertNotIn("#EXTSIZE", b.render())

    def test_every_url_is_on_its_own_line(self):
        b = M3UBuilder()
        b.add_stream("A", "https://x/a.m3u8"); b.add_stream("B", "https://x/b.m3u8")
        urls = [l for l in b.render().splitlines() if l.startswith("https://")]
        self.assertEqual(urls, ["https://x/a.m3u8", "https://x/b.m3u8"])

    def test_newlines_and_quotes_cannot_escape_a_field(self):
        b = M3UBuilder()
        b.add_stream('Evil\nName', "https://x/a.m3u8",
                     description='say "hi"\r\nand break out')
        out = b.render()
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("#EXTINF")]), 1)
        self.assertNotIn('description="say "hi""', out)

    def test_description_is_length_capped(self):
        b = M3UBuilder(); b.add_video("F", "https://x/a.mp4", description="x" * 500)
        line = [l for l in b.render().splitlines() if l.startswith("#EXTINF")][0]
        self.assertLess(len(line), 420)

    def test_content_type_attribute_name_is_configurable(self):
        b = M3UBuilder(type_attr_name="content-type")
        b.add_stream("A", "https://x/a.m3u8")
        self.assertIn('content-type="stream"', b.render())


class TestCleaning(unittest.TestCase):
    def test_control_chars_removed(self):
        self.assertEqual(clean_text("a\x00b\x07c"), "abc")

    def test_quotes_replaced_in_attrs(self):
        self.assertEqual(clean_attr('a"b'), "a'b")


if __name__ == "__main__":
    unittest.main()
