"""The user's names for cameras and takes (names.py)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import names  # noqa: E402


class NamesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "names.json")
        self.names = names.Names(self.path)

    def test_name_a_camera_and_a_take_and_keep_them(self):
        self.names.set("cameras", "cam2", "  Drum   kit ")
        self.names.set("takes", "20261119-140512", "Sunday jam")
        self.assertEqual(self.names.camera("cam2"), "Drum kit")
        self.assertEqual(self.names.take("20261119-140512"), "Sunday jam")
        again = names.Names(self.path)                          # after a hubd restart
        self.assertEqual(again.all(), {"cameras": {"cam2": "Drum kit"}, "takes": {"20261119-140512": "Sunday jam"}})

    def test_an_empty_name_or_the_id_goes_back_to_the_default(self):
        self.names.set("cameras", "cam1", "Wide")
        self.names.set("cameras", "cam1", "")
        self.names.set("cameras", "cam3", "cam3")
        self.assertEqual(self.names.all()["cameras"], {})
        self.assertIsNone(self.names.camera("cam1"))

    def test_a_phones_later_parts_share_its_name(self):
        self.names.set("cameras", "pauls-pixel-part2", "Paul, handheld")
        self.assertEqual(self.names.all()["cameras"], {"pauls-pixel": "Paul, handheld"})
        self.assertEqual(self.names.camera("pauls-pixel-part3"), "Paul, handheld")

    def test_names_are_one_line(self):
        self.names.set("takes", "20261119-140512", "Late\nset\x07 two")
        self.assertEqual(self.names.take("20261119-140512"), "Late set two")

    def test_bad_requests(self):
        for kind, key, name in (("songs", "cam1", "x"), ("cameras", "../etc", "x"), ("takes", "today", "x"),
                                ("cameras", None, "x"), ("cameras", "cam1", "x" * 33),
                                ("takes", "20261119-140512", "x" * 81)):
            with self.assertRaises(ValueError):
                self.names.set(kind, key, name)
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
