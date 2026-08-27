import unittest
from core.url_parser import URLParser

class TestURLParser(unittest.TestCase):
    def test_tabriz_url_with_session(self):
        url = "https://tuvc2.tabrizu.ac.ir/ppnxoh87231e/?page=m&session=breezxoauim2bv3m3sak9"
        parsed = URLParser.parse(url)
        self.assertEqual(parsed.host, "tuvc2.tabrizu.ac.ir")
        self.assertEqual(parsed.meeting_id, "ppnxoh87231e")
        self.assertEqual(parsed.session_token, "breezxoauim2bv3m3sak9")
        self.assertEqual(parsed.room_url, "https://tuvc2.tabrizu.ac.ir/ppnxoh87231e")
        self.assertEqual(parsed.output_zip_url, "https://tuvc2.tabrizu.ac.ir/ppnxoh87231e/output/ppnxoh87231e.zip?download=zip")
        self.assertEqual(parsed.mainstream_url, "https://tuvc2.tabrizu.ac.ir/ppnxoh87231e/output/mainstream.xml")

    def test_clean_room_url(self):
        url = "http://vc.sharif.edu/p99887766/"
        parsed = URLParser.parse(url)
        self.assertEqual(parsed.scheme, "http")
        self.assertEqual(parsed.meeting_id, "p99887766")
        self.assertIsNone(parsed.session_token)

    def test_output_path_url(self):
        url = "https://adobe.ut.ac.ir/r12345/output/r12345.zip?download=zip"
        parsed = URLParser.parse(url)
        self.assertEqual(parsed.meeting_id, "r12345")
        self.assertEqual(parsed.room_url, "https://adobe.ut.ac.ir/r12345")

if __name__ == "__main__":
    unittest.main()
