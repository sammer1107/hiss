"""End-to-end tests: drive the real CLI against sessions kept in a temporary HISS_HOME.

Run: python tests/test_hiss.py
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

RUN_PY = Path(__file__).resolve().parent.parent / "run.py"


class HissTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory()
        cls.env = dict(os.environ, HISS_HOME=cls.home.name)
        cls.name = cls.start("t")

    @classmethod
    def tearDownClass(cls):
        cls.hiss("stop", cls.name)
        cls.home.cleanup()

    @classmethod
    def start(cls, prefix):
        out = cls.hiss("start", prefix)[1]
        return out.split("started session ")[1].split()[0]

    @classmethod
    def hiss(cls, *args, code=None):
        r = subprocess.run([sys.executable, str(RUN_PY), *args], input=code, env=cls.env,
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout, r.stderr

    def run_code(self, code, *flags):
        return self.hiss("run", self.name, *flags, code=code)

    def test_same_name_gets_separate_sessions(self):
        other = self.start("t")
        try:
            self.assertNotEqual(other, self.name)
            self.hiss("run", other, "-c", "mine = 1")
            self.assertIn("NameError", self.run_code("mine")[1])
        finally:
            self.hiss("stop", other)

    def test_stop_of_dead_session_kills_nothing(self):
        other = self.start("dead")
        state = Path(self.home.name) / other / "state.json"
        saved = state.read_text()
        self.hiss("stop", other)
        state.write_text(saved.replace('"pid": ', '"pid": 1').replace('"port": ', '"port": 1'))
        code, _, err = self.hiss("stop", other)
        self.assertEqual(code, 3)
        self.assertIn("was not running", err)
        self.assertFalse(state.exists())

    def test_only_last_statement_echoes(self):
        self.assertEqual(self.run_code('x = {"a": 1}; x.keys(); len(x)\n')[:2], (0, "1\n"))

    def test_state_persists_and_blocks_need_no_blank_line(self):
        self.run_code("def f():\n    return 41\ny = f()\n")
        self.assertEqual(self.run_code("y + 1")[1], "42\n")

    def test_first_error_stops_the_snippet(self):
        code, out, _ = self.run_code('1/0\nprint("after")\n')
        self.assertEqual(code, 1)
        self.assertIn("ZeroDivisionError", out)
        self.assertNotIn("after", out)

    def test_child_process_output_is_captured(self):
        out = self.run_code(f'import subprocess; subprocess.run([{sys.executable!r}, "-c", "print(7)"]); 0')[1]
        out = out.removesuffix("0\n")
        self.assertEqual(out, "7\n")

    def test_long_run_wait_and_busy(self):
        code, out, _ = self.run_code('import time\nprint("a")\ntime.sleep(2)\n"b"\n', "--timeout", "0.5")
        self.assertEqual((code, out), (2, "a\n"))
        self.assertEqual(self.run_code("1")[0], 3)  # busy
        self.assertEqual(self.hiss("wait", self.name)[:2], (0, "'b'\n"))

    def test_interrupt_keeps_the_session(self):
        self.run_code("z = 5")
        self.run_code("import time; time.sleep(30)", "--timeout", "0.5")
        code, out, _ = self.hiss("interrupt", self.name)
        self.assertEqual(code, 1)
        self.assertIn("KeyboardInterrupt", out)
        self.assertEqual(self.run_code("z")[1], "5\n")

    def test_long_output_is_cut_with_pointer_to_full_text(self):
        out = self.run_code('print("\\n".join(map(str, range(1000))))', "--max", "100")[1]
        self.assertIn("characters cut; full text:", out)
        full = out.split("full text: ")[1].split(" ...]")[0]
        self.assertEqual(len(Path(full).read_text().splitlines()), 1000)

    def test_start_cleans_up_after_dead_sessions(self):
        home = Path(self.home.name)
        now = time.time()

        def fake(name, age, transcript_age):
            d = home / name
            (d / "out").mkdir(parents=True)
            (d / "out" / "1.txt").touch()
            (d / "console.txt").touch()
            if transcript_age is not None:
                (d / "transcript.log").touch()
                os.utime(d / "transcript.log", (now - transcript_age,) * 2)
            os.utime(d, (now - age,) * 2)
            return d

        old = fake("old-aaaa", 9 * 86400, 8 * 86400)
        recent = fake("recent-bbbb", 3600, 3600)
        starting = fake("starting-cccc", 0, None)
        other = self.start("x")
        try:
            self.assertFalse(old.exists())
            self.assertEqual(sorted(p.name for p in recent.iterdir()), ["transcript.log"])
            self.assertTrue((starting / "console.txt").exists())
            self.assertEqual(self.run_code("1")[1], "1\n")  # the live session is untouched
        finally:
            self.hiss("stop", other)

    def test_missing_session_is_an_error(self):
        self.assertEqual(self.hiss("run", "nope", "-c", "1")[0], 3)


if __name__ == "__main__":
    unittest.main()
