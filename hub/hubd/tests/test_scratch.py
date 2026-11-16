import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import scratch  # noqa: E402


class R:
    def __init__(self, code=0, err=""):
        self.returncode, self.stderr = code, err


class MixerTest(unittest.TestCase):
    def test_card_of(self):
        self.assertEqual(scratch.card_of("plughw:CARD=Device,DEV=0"), "Device")
        self.assertEqual(scratch.card_of("hw:2,0"), "2")
        self.assertEqual(scratch.card_of("plughw:2,0"), "2")
        self.assertIsNone(scratch.card_of("default"))

    def test_mixer_set_on_the_mic_card_and_failures_reported(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return R(1, "amixer: Unable to find simple control 'Nope',0") if "Nope" in cmd else R()
        s = scratch.Scratch({"mixer": {"Mic": 6, "Auto Gain Control": "off", "Nope": 1}}, "/tmp", lambda m: None, run=run)
        problems = s.set_mixer("plughw:CARD=Device,DEV=0")
        self.assertIn(["amixer", "-q", "-c", "Device", "sset", "Mic", "6"], calls)
        self.assertIn(["amixer", "-q", "-c", "Device", "sset", "Auto Gain Control", "off"], calls)
        self.assertEqual(len(problems), 1)
        self.assertIn("Nope", problems[0])

    def test_no_mixer_config_runs_nothing(self):
        s = scratch.Scratch({}, "/tmp", lambda m: None, run=lambda *a, **k: self.fail("ran amixer"))
        self.assertEqual(s.set_mixer("hw:2,0"), [])


class FakeArecord:
    """Gives some audio, then blocks until terminated; exits with 1 like arecord on SIGTERM."""
    def __init__(self, code_on_eof=1):
        r, self.w = os.pipe()
        self.stdout = os.fdopen(r, "rb")
        os.write(self.w, b"\0" * 9600 * 3)
        self.code = code_on_eof

    def terminate(self):
        try:
            os.close(self.w)
        except OSError:
            pass

    kill = terminate

    def wait(self, timeout=None):
        return self.code


class StopTest(unittest.TestCase):
    def run_take(self, proc, crash=False):
        d = tempfile.mkdtemp()
        s = scratch.Scratch({}, d, lambda m: None, popen=lambda *a, **k: proc, find_device=lambda: "hw:9,0",
                            run=lambda *a, **k: R())
        s.start("20261116-000000")
        if crash:
            proc.terminate()               # arecord dies on its own
            s.thread.join(5)
        return s.stop()

    def test_our_stop_is_not_a_problem(self):
        meta = self.run_take(FakeArecord())
        self.assertEqual(meta["problems"], [])
        self.assertEqual(meta["samples"], 14400)

    def test_arecord_dying_by_itself_is_a_problem(self):
        meta = self.run_take(FakeArecord(), crash=True)
        self.assertTrue(any("arecord stopped" in p for p in meta["problems"]))


if __name__ == "__main__":
    unittest.main()
