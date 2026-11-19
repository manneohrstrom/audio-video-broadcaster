"""The YouTube uploader (youtube_upload.py) against a fake YouTube that follows Google's
resumable upload protocol: sessions, 308 "resume incomplete" with a Range header, the final
video resource; plus the token endpoint. No network."""
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "hub/ingest"))
import youtube_upload as yt  # noqa: E402

TOKEN_URL = "https://oauth2.example/token"
UPLOAD_URL = "https://upload.example/youtube/v3/videos?uploadType=resumable&part=snippet,status"
SESSION = "https://upload.example/session/1"


class FakeYouTube:
    """Keeps the bytes it was sent. fail_next: a list of (method, status or "drop") to answer
    the next matching requests with, e.g. [("PUT", 503)] or [("PUT", "drop")]."""

    def __init__(self):
        self.received = bytearray()
        self.size = None
        self.metadata = None
        self.sessions = 0
        self.tokens = 0
        self.token_status = 200
        self.fail_next = []
        self.calls = []
        self.session_expired = False

    def request(self, method, url, headers=None, body=b""):
        headers = headers or {}
        self.calls.append((method, url, dict(headers)))
        if url == TOKEN_URL:
            self.tokens += 1
            if self.token_status != 200:
                return self.token_status, {}, json.dumps({"error": "invalid_grant"}).encode()
            form = urllib.parse.parse_qs(body.decode())
            assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["refresh-me"]
            return 200, {}, json.dumps({"access_token": "token-%d" % self.tokens, "expires_in": 3600}).encode()
        assert headers.get("Authorization", "").startswith("Bearer token-")
        for i, (fail_method, answer) in enumerate(self.fail_next):
            if fail_method == method:
                del self.fail_next[i]
                if answer == "drop":
                    raise yt.Retry("network: connection reset")
                return answer, {}, json.dumps({"error": {"errors": [{"reason": answer_reason(answer)}],
                                                         "message": "fake"}}).encode()
        if method == "POST" and url == UPLOAD_URL:
            self.sessions += 1
            self.size = int(headers["X-Upload-Content-Length"])
            self.metadata = json.loads(body)
            self.received = bytearray()
            self.session_expired = False
            return 200, {"location": SESSION}, b""
        if method == "PUT" and url == SESSION:
            if self.session_expired:
                return 404, {}, b""
            content_range = headers["Content-Range"]
            if content_range.startswith("bytes */"):           # how far did you get?
                return self.progress()
            start, end_total = content_range[len("bytes "):].split("-")
            end, total = (int(x) for x in end_total.split("/"))
            assert int(start) == len(self.received), (start, len(self.received))
            assert int(total) == self.size and len(body) == end - int(start) + 1
            self.received += body
            return self.progress()
        raise AssertionError("unexpected %s %s" % (method, url))

    def progress(self):
        if len(self.received) == self.size:
            return 200, {}, json.dumps({"id": "abc123XYZ_", "status": {"privacyStatus": "private"}}).encode()
        headers = {"range": "bytes=0-%d" % (len(self.received) - 1)} if self.received else {}
        return 308, headers, b""


def answer_reason(status):
    return {403: "quotaExceeded", 400: "invalidTitle", 503: "backendError"}.get(status, "")


class UploaderTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.spool = os.path.join(self.folder, "uploads")
        self.credentials = os.path.join(self.folder, "credentials.json")
        with open(self.credentials, "w") as f:
            json.dump({"client_id": "id", "client_secret": "secret", "refresh_token": "refresh-me"}, f)
        self.now = [1000.0]
        self.busy = [False]
        self.youtube = FakeYouTube()
        self.uploader = yt.Uploader({"credentials_path": self.credentials, "spool_dir": self.spool, "token_url": TOKEN_URL,
                                     "upload_url": UPLOAD_URL, "chunk_bytes": 256 * 1024},
                                    self.youtube, clock=lambda: self.now[0], rig_busy=lambda: self.busy[0])
        self.video = os.urandom(256 * 1024 * 3 + 1234)              # 4 chunks, the last one short
        rendered = os.path.join(self.folder, "song-video.tmp.mp4")
        with open(rendered, "wb") as f:
            f.write(self.video)
        metadata = yt.video_metadata("Track 1", {"on": True, "text": "Blue <in> Green", "lines": ["Anna, piano", " "]},
                                     "20261117-201741")
        self.record = yt.spool_video(self.spool, rendered, "20261117-201741_track-1_video2", metadata,
                                     {"take": "20261117-201741", "song": "track-1", "video_number": 2},
                                     clock=lambda: self.now[0])

    def record_now(self):
        return yt.read_record(self.spool, self.record["key"])

    def test_uploads_in_chunks_as_a_private_music_video(self):
        self.assertEqual(self.uploader.run_once(), "done")
        self.assertEqual(bytes(self.youtube.received), self.video)
        self.assertEqual(self.youtube.metadata["status"]["privacyStatus"], "private")
        self.assertEqual(self.youtube.metadata["snippet"]["categoryId"], "10")
        record = self.record_now()
        self.assertEqual((record["status"], record["progress"], record["video_id"]), ("done", 1.0, "abc123XYZ_"))
        self.assertEqual(record["url"], "https://youtu.be/abc123XYZ_")
        self.assertFalse(os.path.exists(os.path.join(self.spool, self.record["key"] + ".mp4")))   # the drive keeps it
        self.assertEqual(self.youtube.tokens, 1)
        self.assertEqual(self.uploader.run_once(), "nothing to upload")

    def test_carries_on_after_a_dropped_connection(self):
        uploader_calls = []
        original = self.youtube.request

        def drop_third_put(method, url, headers=None, body=b""):
            uploader_calls.append(method)
            if method == "PUT" and uploader_calls.count("PUT") == 3:   # after one chunk got through
                raise yt.Retry("network: connection reset")
            return original(method, url, headers, body)
        self.uploader.http = type("H", (), {"request": staticmethod(drop_third_put)})()
        self.assertEqual(self.uploader.run_once(), "retrying")
        record = self.record_now()
        self.assertEqual(record["status"], "retrying")
        self.assertIn("trying again in 30 s", record["note"])
        self.assertEqual(self.uploader.run_once(), "nothing to upload")      # waits its turn
        self.now[0] += 31
        self.assertEqual(self.uploader.run_once(), "done")
        self.assertEqual(bytes(self.youtube.received), self.video)
        self.assertEqual(self.youtube.sessions, 1)                   # the same session, not from the start

    def test_a_restart_resumes_the_session(self):
        self.uploader.rig_busy = lambda: len(self.youtube.received) >= 2 * 256 * 1024   # a take starts
        self.assertEqual(self.uploader.run_once(), "paused")
        self.assertEqual(self.record_now()["status"], "paused")
        self.assertEqual(len(self.youtube.received), 2 * 256 * 1024)
        # hub-upload restarts: a new uploader, the record on disk is all it knows.
        fresh = yt.Uploader(self.uploader.cfg, self.youtube, clock=lambda: self.now[0], rig_busy=lambda: False)
        self.assertEqual(fresh.run_once(), "done")
        self.assertEqual(bytes(self.youtube.received), self.video)
        self.assertEqual(self.youtube.sessions, 1)

    def test_waits_while_recording(self):
        self.busy[0] = True
        self.assertEqual(self.uploader.run_once(), "rig busy")
        self.assertEqual(self.youtube.calls, [])

    def test_an_expired_session_starts_again(self):
        self.uploader.cfg["chunk_bytes"] = 1 << 30
        self.record["session_uri"] = SESSION
        yt.write_record(self.spool, self.record)
        self.youtube.session_expired = True
        self.youtube.size = len(self.video)
        self.assertEqual(self.uploader.run_once(), "done")
        self.assertEqual(self.youtube.sessions, 1)
        self.assertEqual(bytes(self.youtube.received), self.video)

    def test_the_daily_limit_waits_an_hour(self):
        self.youtube.fail_next = [("POST", 403)]
        self.assertEqual(self.uploader.run_once(), "waiting")
        record = self.record_now()
        self.assertIn("daily limit", record["note"])
        self.assertEqual(record["retry_at"], self.now[0] + 3600)

    def test_a_refused_video_is_an_error_for_a_person(self):
        self.youtube.fail_next = [("POST", 400)]
        self.assertEqual(self.uploader.run_once(), "error")
        self.assertIn("invalidTitle", self.record_now()["error"])
        self.assertEqual(self.uploader.run_once(), "nothing to upload")
        self.assertTrue(os.path.exists(os.path.join(self.spool, self.record["key"] + ".mp4")))  # kept

    def test_server_trouble_is_retried(self):
        self.youtube.fail_next = [("POST", 503)]
        self.assertEqual(self.uploader.run_once(), "retrying")
        self.now[0] += 31
        self.assertEqual(self.uploader.run_once(), "done")

    def test_an_expired_token_is_renewed_once(self):
        self.youtube.fail_next = [("POST", 401)]
        self.assertEqual(self.uploader.run_once(), "done")
        self.assertEqual(self.youtube.tokens, 2)

    def test_a_revoked_login_says_sign_in_again(self):
        self.youtube.token_status = 400
        self.assertEqual(self.uploader.run_once(), "login problem")
        self.assertIn("youtube_login.py", self.record_now()["note"])

    def test_not_signed_in_does_nothing(self):
        os.remove(self.credentials)
        self.assertEqual(self.uploader.run_once(), "not signed in")
        self.assertEqual(self.record_now()["status"], "queued")


class MetadataTest(unittest.TestCase):
    def test_title_and_lines_from_the_editor(self):
        meta = yt.video_metadata("Track 1", {"on": True, "text": "  Blue <in> Green ", "lines": ["Anna, piano", "", "Ben, bass"]},
                                 "20261117-201741")
        self.assertEqual(meta["snippet"]["title"], "Blue in Green")
        self.assertEqual(meta["snippet"]["description"], "Anna, piano\nBen, bass\n\nRecorded 2026-11-17.")

    def test_without_a_title_the_songs_name(self):
        meta = yt.video_metadata("Track 1", {"on": False, "text": "Ignored", "lines": ["x"]}, "20261117-201741")
        self.assertEqual(meta["snippet"]["title"], "Track 1")
        self.assertEqual(meta["snippet"]["description"], "Recorded 2026-11-17.")
        self.assertEqual(len(yt.video_metadata("x" * 300, None, "20261117-201741")["snippet"]["title"]), 100)

    def test_the_takes_name_from_the_hub_page(self):
        meta = yt.video_metadata("Track 1", {"on": True, "text": "Blue in Green", "lines": ["Anna, piano"]},
                                 "20261117-201741", " Sunday <jam> ")
        self.assertEqual(meta["snippet"]["description"], "Anna, piano\n\nSunday jam, recorded 2026-11-17.")


class HttpTest(unittest.TestCase):
    def test_a_308_is_an_answer_not_a_redirect(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(308)
                self.send_header("Range", "bytes=0-99")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        status, headers, _ = yt.Http(5).request("PUT", "http://127.0.0.1:%d/s" % server.server_port,
                                               {"Content-Length": "3"}, b"abc")
        self.assertEqual((status, yt.next_byte(headers)), (308, 100))

    def test_hub_unreachable_counts_as_busy(self):
        self.assertTrue(yt.hub_is_busy(yt.Http(2), "http://127.0.0.1:9"))


if __name__ == "__main__":
    unittest.main()
