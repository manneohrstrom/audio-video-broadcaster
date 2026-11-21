"""The video's start and end (trim_ns): what the render makes of an edit that has them."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import edit_render  # noqa: E402

NS = 1_000_000_000


def edit(**changes):
    document = {"range_ns": [1000 * NS, 1100 * NS], "bounce_start_ns": 990 * NS, "dissolve_s": 0.3,
                "cuts": [{"at_ns": 0, "camera": "cam1"}, {"at_ns": 10 * NS, "camera": "cam2"},
                         {"at_ns": 40 * NS, "camera": "cam3"}, {"at_ns": 90 * NS, "camera": "cam1"}],
                "title": {"on": True, "text": "Song", "lines": [], "colour": "#ffffff", "start_s": 15.0, "duration_s": 6}}
    document.update(changes)
    return document


class TrimTest(unittest.TestCase):
    def test_without_a_trim_nothing_changes(self):
        original = edit()
        self.assertIs(edit_render.trimmed(original), original)
        with_none = edit(trim_ns=None)
        self.assertIs(edit_render.trimmed(with_none), with_none)

    def test_range_and_cuts_move_to_the_start(self):
        result = edit_render.trimmed(edit(trim_ns=[12 * NS, 80 * NS]))
        self.assertEqual(result["range_ns"], [1012 * NS, 1080 * NS])
        # cam2 was on at 12 s: it opens the video; cam3 moves to 28 s; cam1 at 90 s is after the end.
        self.assertEqual([(c["at_ns"], c["camera"]) for c in result["cuts"]], [(0, "cam2"), (28 * NS, "cam3")])
        self.assertEqual(result["title"], edit()["title"])                 # the sequence runs from the video's start
        self.assertIsNone(result["trim_ns"])

    def test_a_start_on_a_cut(self):
        result = edit_render.trimmed(edit(trim_ns=[40 * NS, 100 * NS]))
        self.assertEqual([(c["at_ns"], c["camera"]) for c in result["cuts"]], [(0, "cam3"), (50 * NS, "cam1")])

    def test_the_sound_follows_the_range(self):
        renderer_edit = edit_render.trimmed(edit(trim_ns=[12 * NS, 80 * NS]))
        self.assertAlmostEqual((renderer_edit["range_ns"][0] - renderer_edit["bounce_start_ns"]) / NS, 22.0)

    def test_the_sound_fades_at_both_ends(self):
        command = edit_render.encoder_command("bounce.wav", 22.0, 300, "out.mp4")
        fades = command[command.index("-af") + 1]
        self.assertIn("afade=t=in:d=0.020", fades)
        self.assertIn("afade=t=out:st=9.980000", fades)


if __name__ == "__main__":
    unittest.main()
