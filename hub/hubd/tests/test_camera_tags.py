"""What each camera shows (camera_tags.py): checked values, current ones, a take's own."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camera_tags  # noqa: E402

TAKE = "20261128-120000"


class CameraTagsTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "camera_tags.json")
        self.tags = camera_tags.CameraTags(self.path)

    def test_values_are_checked_and_cleaned(self):
        saved = self.tags.set_camera("cam1", {"shows": ["Tenor Sax ", "drums", "drums"], "shot": "close",
                                              "note": "  from   the side "})
        self.assertEqual(saved["cam1"], {"shows": ["tenor sax", "drums"], "shot": "close", "note": "from the side"})
        with self.assertRaisesRegex(ValueError, "instrument name"):
            self.tags.set_camera("cam1", {"shows": ["<script>"]})
        with self.assertRaisesRegex(ValueError, "shot"):
            self.tags.set_camera("cam1", {"shot": "extreme"})
        with self.assertRaisesRegex(ValueError, "camera id"):
            self.tags.set_camera("../x", {"shot": "wide"})

    def test_kept_between_restarts_and_empty_forgets(self):
        self.tags.set_camera("phone-k7q2-part2", {"shows": ["whole band"], "shot": "wide"})
        again = camera_tags.CameraTags(self.path)
        self.assertEqual(again.current()["phone-k7q2"]["shows"], ["whole band"])     # a rejoined phone is the same phone
        again.set_camera("phone-k7q2", {})
        self.assertEqual(again.current(), {})

    def test_new_instruments_join_the_list_to_pick_from(self):
        # Who comes in changes from take to take (user, 2026-11-28): no fixed line-up.
        self.assertIn("tenor sax", self.tags.vocabulary()["instruments"])
        self.tags.set_for_take(TAKE, "phone-k7q2", {"shows": ["Oud"], "shot": "close"})
        self.tags.add_instrument("  Kora ")
        again = camera_tags.CameraTags(self.path)
        self.assertEqual(again.vocabulary()["instruments"][-2:], ["oud", "kora"])
        with self.assertRaisesRegex(ValueError, "instrument name"):
            self.tags.add_instrument("whole band")

    def test_a_takes_own_tags_win_over_the_recorded_and_the_current(self):
        self.tags.set_camera("cam1", {"shows": ["keys"], "shot": "medium"})
        self.tags.set_camera("cam2", {"shows": ["drums"], "shot": "close"})
        recorded = {"cam1": {"shows": ["whole band"], "shot": "wide", "note": ""}}
        self.tags.set_for_take(TAKE, "cam2", {"shows": [], "shot": None, "note": ""})
        found = self.tags.for_take(TAKE, recorded)
        self.assertEqual(found["cam1"], ({"shows": ["whole band"], "shot": "wide", "note": ""}, "recorded"))
        self.assertEqual(found["cam2"][1], "take")                  # "shows nothing in particular" is kept
        self.tags.forget_take(TAKE)
        self.assertEqual(self.tags.for_take(TAKE)["cam2"][1], "current")

    def test_described_for_the_camera_table(self):
        self.assertEqual(camera_tags.describe({"shows": ["tenor sax", "trumpet"], "shot": "close", "note": "tripod"}),
                         "tenor sax, trumpet · close · tripod")
        self.assertEqual(camera_tags.describe(camera_tags.empty_tags()), "not said · shot size not said")


if __name__ == "__main__":
    unittest.main()
