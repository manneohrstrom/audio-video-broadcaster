"""The AI cut (ai_cut*.py, gemini.py): placing the model's cuts, the prompts and settings,
Gemini's errors in words, and a whole run against a fake Gemini."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_cut  # noqa: E402
import ai_cut_place  # noqa: E402
import ai_cut_prompts  # noqa: E402
import gemini  # noqa: E402

S = 1_000_000_000
START = 1_000 * S          # the song range's start on the hub clock


def camera(camera_id, first=START, last=START + 120 * S, rough=(), shot=None, shows=()):
    return {"id": camera_id, "label": camera_id, "kind": "pi", "mount": "fixed", "first_frame_ns": first,
            "last_frame_ns": last, "rough_sections_ns": [list(r) for r in rough],
            "tags": {"shows": list(shows), "shot": shot, "note": ""}}


class PlaceCutsTest(unittest.TestCase):
    def setUp(self):
        self.range_ns = [START, START + 120 * S]
        self.to_song = ai_cut_place.bounce_to_song(START - 2 * S, 0.0, START)     # the bounce starts 2 s earlier
        self.beats = {"beats_s": [i * 0.5 for i in range(300)], "downbeats_s": [i * 2.0 for i in range(75)]}
        self.cameras = [camera("cam1", shot="wide", shows=["whole band"]), camera("cam2"),
                        camera("phone-a", first=START + 30 * S), camera("phone-b", rough=[(START + 60 * S, START + 61 * S)])]

    def place(self, decisions):
        return ai_cut_place.place_cuts(decisions, self.cameras, self.range_ns, self.to_song, self.beats)

    def test_times_go_onto_bars_and_the_first_shot_starts_the_song(self):
        cuts, left_out = self.place([{"time_s": 6.4, "camera": "cam2", "why": "head"},       # song 4.4 s: bar at 4
                                     {"time_s": 12.3, "camera": "cam1", "why": "solo starts"}])
        self.assertEqual([(c["at_ns"], c["camera"], c["by"]) for c in cuts], [(0, "cam2", "ai"), (10 * S, "cam1", "ai")])
        self.assertEqual(cuts[0]["note"], "moved -0.4 s onto the bar; starts at the song's start (asked for 4.0 s in)")
        self.assertIn("moved -0.3 s onto the bar", cuts[1]["note"])
        self.assertEqual(cuts[1]["reason"], "solo starts")
        self.assertEqual(left_out, [])

    def test_no_footage_takes_the_wide_camera_and_dropped_frames_are_only_said(self):
        cuts, _ = self.place([{"time_s": 2.0, "camera": "cam2", "why": "a"},
                              {"time_s": 12.0, "camera": "phone-a", "why": "sax"},       # phone-a starts at 30 s
                              {"time_s": 42.0, "camera": "cam2", "why": "b"},
                              {"time_s": 58.0, "camera": "phone-b", "why": "keys"}])
        self.assertEqual(cuts[1]["camera"], "cam1")
        self.assertIn("phone-a has no footage here: cam1 instead", cuts[1]["note"])
        self.assertEqual(cuts[3]["camera"], "phone-b")
        self.assertIn("drops frames", cuts[3]["note"])

    def test_unknown_too_close_outside_and_repeated_cuts_are_left_out(self):
        cuts, left_out = self.place([{"time_s": 2.0, "camera": "cam1", "why": "a"},
                                     {"time_s": 6.0, "camera": "nope", "why": "b"},
                                     {"time_s": 12.0, "camera": "cam2", "why": "c"},
                                     {"time_s": 13.0, "camera": "cam1", "why": "d"},       # 1 s later
                                     {"time_s": 22.0, "camera": "cam2", "why": "e"},       # same camera again
                                     {"time_s": 500.0, "camera": "cam1", "why": "f"}])
        self.assertEqual([c["camera"] for c in cuts], ["cam1", "cam2"])
        self.assertEqual(sorted(entry["left_out"] for entry in left_out),
                         ["after the song's end", "less than 2 s after the cut before", "no such camera",
                          "the same camera as the shot before"])

    def test_camera_moves_go_on_the_cuts(self):
        cuts, _ = self.place([{"time_s": 2.0, "camera": "cam2", "why": "intro", "move": "right"},
                              {"time_s": 12.0, "camera": "phone-a", "why": "sax solo", "move": "in"},   # cam1 stands in
                              {"time_s": 32.0, "camera": "cam2", "why": "keys", "move": "none"},
                              {"time_s": 42.0, "camera": "cam1", "why": "head", "move": "zoom"}])
        self.assertEqual([c["camera"] for c in cuts], ["cam2", "cam1", "cam2", "cam1"])
        self.assertEqual([c.get("move") for c in cuts],
                         [{"kind": "right", "amount": 0.5}, {"kind": "in", "amount": 0.5}, None, None])

    def test_the_edit_schema_asks_for_a_move(self):
        item = ai_cut_prompts.edit_schema(["cam1"])["properties"]["cuts"]["items"]
        self.assertEqual(item["properties"]["move"]["enum"], ["none", "in", "out", "left", "right"])
        self.assertIn("move", item["required"])
        self.assertIn('"in" on a player\'s close-up', ai_cut_prompts.EDIT_FIXED)

    def test_a_cut_before_the_video_opens_it(self):
        cuts, left_out = self.place([{"time_s": 0.0, "camera": "cam2", "why": "count-in"},     # song -2 s
                                     {"time_s": 0.6, "camera": "cam1", "why": "intro"},        # song -1.4 s
                                     {"time_s": 12.0, "camera": "cam2", "why": "head"}])
        self.assertEqual([(c["at_ns"], c["camera"]) for c in cuts], [(0, "cam1"), (10 * S, "cam2")])
        self.assertIn("before the video starts", cuts[0]["note"])
        self.assertEqual(left_out[0]["left_out"], "before the video starts, and not the last such cut")


class PromptsTest(unittest.TestCase):
    def test_placeholders_are_filled_and_unknown_ones_refused(self):
        settings = ai_cut_prompts.validated_settings({"listen_direction": "Listen to {{song_name}} ({{ song_length }})."})
        prompts = ai_cut_prompts.prompts(settings, {"song_name": "First Light", "song_length": "12:00",
                                                    "bars": "1=0:00.2", "cameras": "| cam1 |"})
        self.assertTrue(prompts["listen"].startswith("Listen to First Light (12:00)."))
        self.assertIn("1=0:00.2", prompts["listen"])
        self.assertIn("| cam1 |", prompts["edit"])
        with self.assertRaisesRegex(ValueError, "unknown placeholder"):
            ai_cut_prompts.validated_settings({"edit_direction": "{{mood}}"})

    def test_thinking_is_a_level_or_a_budget_by_model(self):
        level = ai_cut_prompts.generation_config(ai_cut_prompts.validated_settings({"thinking": "low"}), {})
        self.assertEqual(level["thinkingConfig"], {"thinkingLevel": "low"})
        budget = ai_cut_prompts.generation_config(
            ai_cut_prompts.validated_settings({"model": "gemini-2.5-pro", "thinking": "minimal", "temperature": 0.4}), {})
        self.assertEqual((budget["thinkingConfig"], budget["temperature"]), ({"thinkingBudget": 128}, 0.4))
        plain = ai_cut_prompts.generation_config(ai_cut_prompts.validated_settings({}), {"type": "object"})
        self.assertNotIn("thinkingConfig", plain)
        self.assertEqual(plain["responseMimeType"], "application/json")

    def test_bars_and_camera_table(self):
        self.assertEqual(ai_cut_prompts.bar_list([0.2, 2.9, 65.25], per_line=2), "1=0:00.2  2=0:02.9\n3=1:05.2")
        table = ai_cut_prompts.camera_table([camera("cam1", shot="wide", shows=["whole band"])])
        self.assertIn("| cam1 | cam1 | whole band | wide | fixed on the wall |  |", table)


class ErrorsTest(unittest.TestCase):
    def test_credit_rate_and_key_are_told_apart(self):
        self.assertEqual(gemini.classify(429, "Your prepayment credits are depleted. Please go to AI Studio"), "credit")
        self.assertEqual(gemini.classify(429, "You exceeded your current quota, please check your plan and billing "
                                              "details. Quota exceeded for metric: x, limit: 5. Please retry in 31s."), "rate")
        self.assertEqual(gemini.classify(400, "API key not valid. Please pass a valid API key."), "key")
        self.assertEqual(gemini.classify(404, "models/gemini-9 is not found"), "model")
        self.assertEqual(gemini.classify(503, "The model is overloaded."), "busy")
        self.assertIn("top it up", str(gemini.GeminiError("credit")))


class FakeGemini:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls, self.uploads = [], 0

    def has_key(self):
        return True

    def upload(self, path, mime_type, name):
        self.uploads += 1
        return "https://files/abc"

    def generate(self, model, contents, config):
        self.calls.append((model, contents, config))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        usage = {"promptTokenCount": 25000, "candidatesTokenCount": 1000, "thoughtsTokenCount": 3000,
                 "promptTokensDetails": [{"modality": "AUDIO", "tokenCount": 23000}, {"modality": "TEXT", "tokenCount": 2000}]}
        return json.dumps(answer), usage, "STOP", model


class FakeEditor:
    def __init__(self, folder):
        self.bounce = os.path.join(folder, "mix.wav")
        with open(self.bounce, "wb") as f:
            f.write(b"RIFF....")

    def project(self, take, song, version):
        return {"song": {"name": "First Light"}, "take_name": None, "range_ns": [START, START + 120 * S],
                "bounce": {"start_ns": START, "duration_s": 120.0, "sync": {"drift_ppm": 0.0}},
                "cameras": [camera("cam1", shot="wide", shows=["whole band"]), camera("cam2", shot="close", shows=["drums"])]}

    def bounce_files(self, take, song, version):
        return self.bounce, {"beats_s": [i * 0.5 for i in range(240)], "downbeats_s": [i * 2.0 for i in range(60)]}


LISTENING = {"instruments": ["drums"], "sections": [{"start": 0, "end": 120, "kind": "head"}],
             "activity": [], "moments": [{"time": 40.0, "what": "drum fill"}]}
EDIT = {"cuts": [{"time_s": 0.0, "camera": "cam1", "why": "head"}, {"time_s": 40.1, "camera": "cam2", "why": "fill"}],
        "summary": "wide, then the fill"}


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        prices = os.path.join(self.tmp, "prices.json")
        with open(prices, "w") as f:
            json.dump({"models": {"gemini-3-flash-preview": {"input": 0.5, "audio": 1.0, "output": 3.0}}}, f)
        self.client = FakeGemini([LISTENING, EDIT, EDIT])
        self.ai = ai_cut.AiCut(self.tmp, self.client, FakeEditor(self.tmp), prices, clock=lambda: 1_795_870_000.0,
                               spawn=lambda fn: fn())
        self.ai.small_bounce = self.small_bounce                       # no ffmpeg in the test

    def small_bounce(self, path):
        small = os.path.join(self.tmp, "ai_cut", "audio", "small.mp3")
        os.makedirs(os.path.dirname(small), exist_ok=True)
        shutil.copy(path, small)
        return small

    def test_a_run_listens_edits_places_and_prices(self):
        started = self.ai.start("20261127-192905", "track-1", 1)
        run = self.ai.run_document("20261127-192905", "track-1", started["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertEqual([(c["at_ns"], c["camera"]) for c in run["cuts"]], [(0, "cam1"), (40 * S, "cam2")])
        # 2000 text x 0.5 + 23000 audio x 1.0 + 4000 output x 3.0 per million, twice
        self.assertAlmostEqual(run["cost_usd"], 2 * (0.001 + 0.023 + 0.012), places=6)
        conversation = self.client.calls[1][1]
        self.assertEqual([turn["role"] for turn in conversation], ["user", "model", "user"])
        self.assertEqual(self.client.calls[1][2]["responseJsonSchema"]["properties"]["cuts"]["items"]
                         ["properties"]["camera"]["enum"], ["cam1", "cam2"])
        self.assertEqual(self.ai.runs("20261127-192905", "track-1")[0]["cuts"], 2)
        # Everything to look into it by hand: the config with the schema as sent, the audio file.
        self.assertEqual(run["editing"]["config"]["responseJsonSchema"]["required"], ["cuts", "summary"])
        self.assertTrue(self.ai.run_audio("20261127-192905", "track-1", run["id"]).endswith("/ai_cut/audio/small.mp3"))
        preview = self.ai.preview("20261127-192905", "track-1", 1)
        self.assertEqual(preview["configs"]["listen"]["responseMimeType"], "application/json")
        self.assertIn("There is video from 0:00.0 to 2:00.0", preview["prompts"]["edit"])

    def test_the_listening_is_reused_when_nothing_it_depends_on_changed(self):
        self.ai.start("20261127-192905", "track-1", 1)
        self.ai.clock = lambda: 1_795_870_100.0
        second = self.ai.start("20261127-192905", "track-1", 1)
        run = self.ai.run_document("20261127-192905", "track-1", second["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertTrue(run["listening"]["reused_from"])
        self.assertEqual(len(self.client.calls), 3)                    # listen, edit, edit
        self.assertEqual(self.client.uploads, 1)                        # the upload is reused too
        self.client.answers = [LISTENING, EDIT]
        self.ai.clock = lambda: 1_795_870_200.0
        third = self.ai.start("20261127-192905", "track-1", 1, listen_again=True)
        self.assertNotIn("reused_from", self.ai.run_document("20261127-192905", "track-1", third["id"])["listening"])

    def test_a_run_cut_off_by_a_restart_says_so(self):
        self.ai.spawn = lambda fn: None                                # hubd stops before the run does
        started = self.ai.start("20261127-192905", "track-1", 1)
        again = ai_cut.AiCut(self.tmp, self.client, self.ai.editor, self.ai.prices_path)
        run = again.run_document("20261127-192905", "track-1", started["id"])
        self.assertEqual((run["status"], run["error"]["message"]), ("error", "cut off: the hub restarted during the run"))

    def test_a_runs_own_direction_goes_into_the_edit_prompt_only(self):
        self.ai.start("20261127-192905", "track-1", 1)
        self.ai.clock = lambda: 1_795_870_100.0
        second = self.ai.start("20261127-192905", "track-1", 1, extra="An even spread over the cameras, for {{song_name}}.")
        run = self.ai.run_document("20261127-192905", "track-1", second["id"])
        self.assertIn("For this cut in particular:\nAn even spread over the cameras, for First Light.", run["prompts"]["edit"])
        self.assertLess(run["prompts"]["edit"].index("in particular"), run["prompts"]["edit"].index("---"))
        self.assertNotIn("even spread", run["prompts"]["listen"])
        self.assertTrue(run["listening"]["reused_from"])               # the listening still fits
        self.assertEqual(self.ai.runs("20261127-192905", "track-1")[0]["extra"], "An even spread over the cameras, for {{song_name}}.")

    def test_a_failure_is_kept_with_its_explanation(self):
        self.client.answers = [gemini.GeminiError("credit", "429: Your prepayment credits are depleted")]
        started = self.ai.start("20261127-192905", "track-1", 1)
        run = self.ai.run_document("20261127-192905", "track-1", started["id"])
        self.assertEqual((run["status"], run["error"]["kind"]), ("error", "credit"))
        self.assertIn("top it up", run["error"]["message"])
        self.assertIsNone(self.ai.running)


if __name__ == "__main__":
    unittest.main()
