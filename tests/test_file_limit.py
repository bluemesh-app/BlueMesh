#!/usr/bin/env python3
"""Regression checks for the private-file limit shared by the GUI and fake backend."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 256 * 213 = 54,528 bytes of reassembled packet, less 69 framing + 255 name
# + 16 MIME = 54,188 content.
EXPECTED = 54_188


def constant(path, name):
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\s*=\s*([0-9_]+)\b", text, re.MULTILINE)
    if not match:
        raise AssertionError(f"{name} not found in {path}")
    return int(match.group(1).replace("_", ""))


class PrivateFileLimitTest(unittest.TestCase):
    def test_gui_and_fake_backend_mirror_the_backend_limit(self):
        self.assertEqual(constant(ROOT / "gui/bluemesh.py", "MAX_FILE_BYTES"), EXPECTED)
        self.assertEqual(
            constant(ROOT / "tests/fake_backend.py", "MAX_PRIVATE_FILE_BYTES"),
            EXPECTED,
        )

    def test_fake_backend_reports_and_enforces_the_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            big = Path(tmp) / "big.bin"
            big.write_bytes(b"x" * (EXPECTED + 1))
            env = os.environ.copy()
            env["FAKE_LOG"] = str(Path(tmp) / "fake.log")
            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "tests/fake_backend.py")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            try:
                ready = json.loads(proc.stdout.readline())
                self.assertEqual(ready["ev"], "ready")
                self.assertEqual(ready["max_private_file_bytes"], EXPECTED)

                proc.stdin.write(json.dumps({
                    "cmd": "send_file",
                    "path": str(big),
                    "peer_id": "a1b2c3d4e5f60718",
                    "req": 1,
                }) + "\n")
                proc.stdin.flush()

                sent = None
                for _ in range(10):
                    line = proc.stdout.readline()
                    if not line:
                        break
                    event = json.loads(line)
                    if event.get("ev") == "sent":
                        sent = event
                        break
                self.assertIsNotNone(sent, "fake backend did not answer send_file")
                self.assertFalse(sent["ok"])
                self.assertIn("54,188", sent["error"])
                self.assertIn("52.9", sent["error"])
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe:
                        pipe.close()


if __name__ == "__main__":
    unittest.main()
