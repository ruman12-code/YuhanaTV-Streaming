"""XMLTV generation and canonical channel-id mapping (spec sections 17, 18)."""
import sys, tempfile, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import Channel
from src.epg.xmltv import (XmltvWriter, EpgChannel, Programme, clean_xml_text, fmt_time)
from src.sources.iptv_org.channels import (normalise_name, loose_name, load_catalog,
                                           match_channel, guide_coverage)

T0 = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)


def prog(cid="X.bd", offset=0, hours=1, title="Show", **kw):
    return Programme(cid, T0 + timedelta(hours=offset),
                     T0 + timedelta(hours=offset + hours), title, **kw)


class TestXmltv(unittest.TestCase):
    def setUp(self):
        self.w = XmltvWriter()
        self.w.add_channel(EpgChannel(id="X.bd", display_names=["Channel X"],
                                      icon="https://h/l.png"))

    def test_document_is_well_formed_xml(self):
        from xml.etree import ElementTree
        self.w.add_programme(prog())
        root = ElementTree.fromstring(self.w.render())
        self.assertEqual(root.tag, "tv")
        self.assertEqual(len(root.findall("channel")), 1)
        self.assertEqual(len(root.findall("programme")), 1)

    def test_declares_utf8_and_the_xmltv_doctype(self):
        out = self.w.render()
        self.assertIn('<?xml version="1.0" encoding="UTF-8"?>', out)
        self.assertIn("<!DOCTYPE tv SYSTEM \"xmltv.dtd\">", out)

    def test_programme_for_an_unknown_channel_is_dropped(self):
        self.assertFalse(self.w.add_programme(prog(cid="Nope.bd")))
        self.assertEqual(self.w.dropped_programmes, 1)
        self.assertNotIn("Nope.bd", self.w.render())

    def test_programme_ending_before_it_starts_is_dropped(self):
        bad = Programme("X.bd", T0 + timedelta(hours=2), T0, "Backwards")
        self.assertFalse(self.w.add_programme(bad))

    def test_programme_without_a_title_is_dropped(self):
        self.assertFalse(self.w.add_programme(prog(title="")))

    def test_no_schedule_is_fabricated_for_a_bare_channel(self):
        """A channel with no data gets an entry and no programmes. Ever."""
        out = self.w.render()
        self.assertIn('id="X.bd"', out)
        self.assertNotIn("<programme", out)
        self.assertNotIn("No information", out)

    def test_special_characters_are_escaped(self):
        self.w.add_programme(prog(title='Tom & Jerry <"best">'))
        out = self.w.render()
        self.assertIn("Tom &amp; Jerry &lt;", out)
        from xml.etree import ElementTree
        ElementTree.fromstring(out)

    def test_illegal_control_characters_are_removed(self):
        self.w.add_programme(prog(title="Bad\x00Title\x08"))
        from xml.etree import ElementTree
        root = ElementTree.fromstring(self.w.render())
        self.assertEqual(root.find("programme/title").text, "BadTitle")

    def test_output_is_never_gzipped(self):
        """SS IPTV documents that gzip is not allowed for xmltv."""
        target = Path(tempfile.mkdtemp()) / "epg.xml"
        self.w.write(target)
        self.assertFalse(target.read_bytes().startswith(b"\x1f\x8b"))
        self.assertTrue(target.read_bytes().startswith(b"<?xml"))

    def test_size_budget_truncates_instead_of_overflowing(self):
        w = XmltvWriter(max_bytes=1200)
        w.add_channel(EpgChannel(id="X.bd", display_names=["Channel X"]))
        for i in range(200):
            w.add_programme(prog(offset=i, title=f"Programme number {i}"))
        out = w.render()
        self.assertLessEqual(len(out.encode()), 1200)
        self.assertGreater(w.dropped_programmes, 0)
        from xml.etree import ElementTree
        ElementTree.fromstring(out)

    def test_earliest_programmes_survive_truncation(self):
        w = XmltvWriter(max_bytes=1400)
        w.add_channel(EpgChannel(id="X.bd", display_names=["X"]))
        for i in reversed(range(50)):
            w.add_programme(prog(offset=i, title=f"P{i}"))
        self.assertIn("P0", w.render(), "tonight's listings must outrank next week's")

    def test_timestamps_carry_an_offset(self):
        self.assertRegex(fmt_time(T0), r"^\d{14} [+-]\d{4}$")

    def test_naive_datetimes_are_treated_as_utc(self):
        self.assertTrue(fmt_time(datetime(2026, 1, 1, 0, 0)).endswith("+0000"))

    def test_coverage_statistics(self):
        self.w.add_channel(EpgChannel(id="Y.bd", display_names=["Y"]))
        self.w.add_programme(prog())
        s = self.w.stats(total_channels=4)
        self.assertEqual((s["channels_in_epg"], s["channels_with_programmes"],
                          s["channels_without_programmes"]), (2, 1, 1))
        self.assertEqual(s["coverage_percent"], 25.0)


CATALOG = [
    {"id": "SomoyTV.bd", "name": "Somoy TV", "country": "BD", "logo": "https://h/s.png"},
    {"id": "Channeli.bd", "name": "Channel i", "country": "BD"},
    {"id": "SomoyTV.us", "name": "Somoy TV", "country": "US"},
    {"id": "NatGeo.us", "name": "National Geographic", "country": "US",
     "alt_names": ["Nat Geo"]},
    {"id": "Dead.bd", "name": "Gone TV", "country": "BD", "closed": True},
]


def chan(name, country="bd", **kw):
    return Channel(id="x", name=name, country=country,
                   stream_url="https://h/a.m3u8", **kw)


class TestCanonicalMapping(unittest.TestCase):
    def setUp(self):
        self.index = load_catalog(CATALOG)

    def test_short_names_are_not_destroyed_by_noise_stripping(self):
        """'Channel i' must not fold to 'i'."""
        self.assertEqual(normalise_name("Channel i"), "channel i")
        self.assertEqual(loose_name("Channel i"), "channel i")
        self.assertIsNotNone(match_channel(chan("Channel i"), self.index))

    def test_quality_suffix_is_ignored(self):
        m = match_channel(chan("National Geographic HD", country="us"), self.index)
        self.assertEqual(m.id, "NatGeo.us")

    def test_alternative_name_matches(self):
        self.assertEqual(match_channel(chan("Nat Geo", country="us"), self.index).id,
                         "NatGeo.us")

    def test_country_disambiguates_identical_names(self):
        self.assertEqual(match_channel(chan("Somoy TV", country="bd"), self.index).id,
                         "SomoyTV.bd")
        self.assertEqual(match_channel(chan("Somoy TV", country="us"), self.index).id,
                         "SomoyTV.us")

    def test_ambiguous_name_without_a_country_is_refused(self):
        """Guessing would attach another country's schedule to the channel."""
        self.assertIsNone(match_channel(chan("Somoy TV", country=""), self.index))

    def test_unambiguous_name_without_a_country_is_accepted(self):
        self.assertEqual(match_channel(chan("Channel i", country=""), self.index).id,
                         "Channeli.bd")

    def test_unknown_channel_returns_none(self):
        self.assertIsNone(match_channel(chan("Totally Made Up TV"), self.index))

    def test_empty_name_returns_none(self):
        self.assertIsNone(match_channel(chan(""), self.index))

    def test_closed_channels_are_deprioritised(self):
        self.assertIsNone(match_channel(chan("Nonexistent"), self.index))
        self.assertEqual(match_channel(chan("Gone TV"), self.index).id, "Dead.bd")

    def test_guide_coverage_filters_to_our_ids(self):
        guides = [
            {"channel": "SomoyTV.bd", "site": "example.com", "lang": "bn", "site_id": "1"},
            {"channel": "Elsewhere.xx", "site": "other.com", "lang": "en", "site_id": "9"},
        ]
        cov = guide_coverage(guides, {"SomoyTV.bd", "Channeli.bd"})
        self.assertEqual(list(cov), ["SomoyTV.bd"])
        self.assertEqual(cov["SomoyTV.bd"][0]["site"], "example.com")


if __name__ == "__main__":
    unittest.main()
