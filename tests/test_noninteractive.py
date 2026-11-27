"""A repo that asks for a password never reaches a prompt, an askpass program or a dialog, on any git version."""

import http.server
import os
import threading
import unittest
from pathlib import Path
from unittest import mock

import fake_orca
from helpers import Mac, isolate_home, tmpdir


class NeedsAuth(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="presets"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class NonInteractiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        isolate_home(self.tmp.name)
        fake_orca.install(self.tmp.name)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), NeedsAuth)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.marker = Path(self.tmp.name) / "askpass-ran"
        self.askpass = Path(self.tmp.name) / "askpass"
        self.askpass.write_text(f"#!/bin/sh\necho \"$1\" >> '{self.marker}'\necho guessed\n")
        self.askpass.chmod(0o755)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_inherited_askpass_is_never_run(self):
        mac = Mac(self.tmp.name, "mac-a", f"http://127.0.0.1:{self.server.server_port}/presets.git")
        mac.save("filament", "PLA", x=["1"])
        shell = {"GIT_ASKPASS": str(self.askpass), "SSH_ASKPASS": str(self.askpass), "DISPLAY": ":0"}
        with mock.patch.dict(os.environ, shell):
            r = mac.sync()
        self.assertFalse(self.marker.exists(), "git ran the inherited askpass program")
        self.assertTrue(r.offline)
        self.assertIn("no saved token", r.error)


if __name__ == "__main__":
    unittest.main()
