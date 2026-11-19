"""A repo URL that redirects is not followed, so no request reaches the other host."""

import http.server
import threading
import unittest

import fake_orca
from helpers import Mac, isolate_home, tmpdir


def serve(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class RedirectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        isolate_home(self.tmp.name)
        fake_orca.install(self.tmp.name)
        self.hits = []
        hits = self.hits

        class Other(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(404)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.other = serve(Other)
        target = f"http://localhost:{self.other.server_port}"

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(301)
                self.send_header("Location", target + self.path)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.first = serve(Redirect)

    def tearDown(self):
        for s in (self.first, self.other):
            s.shutdown()
            s.server_close()
        self.tmp.cleanup()

    def test_redirect_to_another_host_is_not_followed(self):
        url = f"http://127.0.0.1:{self.first.server_port}/team/presets"
        mac = Mac(self.tmp.name, "mac-a", url)
        mac.save("filament", "PLA", nozzle_temperature=["205"])
        r = mac.sync()
        self.assertEqual(self.hits, [], "git followed the redirect")
        self.assertTrue(r.offline)
        self.assertIn("Set the final URL", r.error)


if __name__ == "__main__":
    unittest.main()
