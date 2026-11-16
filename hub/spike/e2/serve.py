#!/usr/bin/env python3
"""Spike E2: serve the playback test page and takes, with HTTP Range (seeking).

usage: python3 serve.py <media dir> [port]
  <media dir>/<take id>/{camN.mov, <phone>.mov, take.json, [audio.wav|.mp3, audio.json]}
Then open http://localhost:<port>/ in Chrome on the Mac. Standard library only."""
import http.server, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.abspath(sys.argv[1])
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8766
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".json": "application/json",
         ".mov": "video/mp4", ".mp4": "video/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg"}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/takes":
            takes = {t: sorted(os.listdir(os.path.join(MEDIA, t))) for t in sorted(os.listdir(MEDIA))
                     if os.path.isdir(os.path.join(MEDIA, t))}
            return self.send(200, json.dumps(takes).encode(), TYPES[".json"])
        if p == "/":
            p = "/e2.html"
        if p.startswith("/media/"):
            path = os.path.normpath(os.path.join(MEDIA, p[len("/media/"):]))
            if not path.startswith(MEDIA + os.sep):
                return self.send(404, b"no")
        else:
            path = os.path.normpath(os.path.join(HERE, p.lstrip("/")))
            if not path.startswith(HERE + os.sep):
                return self.send(404, b"no")
        if not os.path.isfile(path):
            return self.send(404, b"not found")
        size = os.path.getsize(path)
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", TYPES.get(os.path.splitext(path)[1], "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    b = f.read(min(1 << 20, left))
                    if not b:
                        break
                    self.wfile.write(b)
                    left -= len(b)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def send(self, code, body, ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


print("E2: http://localhost:%d/  (media: %s)" % (PORT, MEDIA))
http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
