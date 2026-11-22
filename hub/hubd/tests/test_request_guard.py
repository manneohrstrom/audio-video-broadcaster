"""The request guard: other websites can't command the hub (release 1.0)."""
import http.client
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hubd  # noqa: E402

HUB = "studio-hub.local"


class FakeHub:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append("stop")
        return {}

    def request_delete_take(self, take_id):
        self.calls.append("delete")
        return {}


class RequestGuardTest(unittest.TestCase):
    def setUp(self):
        before = hubd.Handler.hub
        self.addCleanup(setattr, hubd.Handler, "hub", before)
        self.hub = hubd.Handler.hub = FakeHub()
        server = hubd.ThreadingHTTPServer(("127.0.0.1", 0), hubd.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def send(self, host=HUB, origin=None, path="/api/stop", method="POST"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        headers = {"Host": host}
        if origin is not None:
            headers["Origin"] = origin
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        response.read()
        return response.status

    def test_other_sites_are_refused(self):
        for origin in ("http://evil.example", "null", "", "http://cam1.local", "http://studio-hub.local:8000",
                       "http://studio-hub.local.evil.example", "file://studio-hub.local"):
            for path in ("/api/stop", "/api/takes/20990101-000000/delete"):
                self.assertEqual(self.send(origin=origin, path=path), 403, (origin, path))
        self.assertEqual(self.send(origin="http://evil.example", path="/api/time", method="GET"), 403)
        self.assertEqual(self.send(origin="http://evil.example", path="/api/editor/x", method="PUT"), 403)
        self.assertEqual(self.hub.calls, [])

    def test_a_name_from_public_dns_is_refused(self):
        """DNS rebinding: the attacker's name points at the hub, so Origin and Host agree."""
        for host in ("evil.example", "studio-hub.local.evil.example", "192.168.1.5.evil.example", "[bad"):
            self.assertEqual(self.send(host=host, origin="http://" + host), 403, host)
            self.assertEqual(self.send(host=host, path="/api/time", method="GET"), 403, host)
        self.assertEqual(self.hub.calls, [])

    def test_the_hubs_own_pages_work(self):
        for host in (HUB, "Studio-Hub.local.", "studio-hub", "studio-hub.lan", "studio-hub.home.arpa", "192.168.1.5",
                     "[fe80::1]", "localhost:8000"):
            self.assertEqual(self.send(host=host, origin="http://" + host), 200, host)

    def test_tools_without_an_origin_work(self):
        self.assertEqual(self.send(), 200)                      # curl, hub-ingest, the deploy's guard
        self.assertEqual(self.send(host="127.0.0.1", path="/api/time", method="GET"), 200)


if __name__ == "__main__":
    unittest.main()
