#!/usr/bin/env python3
"""Sign the hub in to YouTube, once (docs/youtube-plan.md, "Setting it up").

Run on the Mac with the client file downloaded from Google Cloud (an OAuth client of type
"Desktop app"):

    python3 mac/youtube_login.py ~/Downloads/client_secret_....json

Your browser opens Google's sign-in: choose the account and then the band's channel, and
allow "Manage your YouTube videos" (upload only: the hub can't delete or change anything
else). The login is saved to ~/.config/studio-multicam/youtube.json (only you can read it,
never in git); the next deploy copies it to the hub:

    cd deploy && .venv/bin/ansible-playbook site.yml --limit hub

Python's standard library only. Google's desktop-app flow: a one-off local address on this
Mac receives the answer, with PKCE so only this run can use it.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
import webbrowser

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
OUT = os.path.expanduser("~/.config/studio-multicam/youtube.json")


def read_client(path):
    with open(path) as f:
        found = json.load(f)
    client = found.get("installed")
    if not client:
        sys.exit("That file isn't a Desktop app client (no \"installed\" section). In Google Cloud: "
                 "APIs & Services > Credentials > Create credentials > OAuth client ID > Desktop app.")
    return client["client_id"], client["client_secret"]


def wait_for_answer(state):
    """A one-off local web server; returns (redirect_uri, code) once Google sends the browser back."""
    answer = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if query.get("state") != [state]:
                self.send_response(400)
                self.end_headers()
                return
            answer["code"] = (query.get("code") or [None])[0]
            answer["error"] = (query.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Done: you can close this tab and go back to the terminal.\n".encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = "http://127.0.0.1:%d" % server.server_port
    return server, redirect_uri, answer


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    client_id, client_secret = read_client(sys.argv[1])
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    server, redirect_uri, answer = wait_for_answer(state)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening Google's sign-in in your browser. If it doesn't open, go to:\n" + url + "\n")
    webbrowser.open(url)
    while "code" not in answer:
        server.handle_request()
    server.server_close()
    if not answer.get("code"):
        sys.exit("Google said no: %s" % (answer.get("error") or "no code"))
    body = urllib.parse.urlencode({"code": answer["code"], "client_id": client_id, "client_secret": client_secret,
                                   "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                                   "code_verifier": verifier}).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body)) as response:
        tokens = json.load(response)
    if not tokens.get("refresh_token"):
        sys.exit("Google gave no refresh token; run this again (it asks for consent each time).")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd = os.open(OUT + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"client_id": client_id, "client_secret": client_secret, "refresh_token": tokens["refresh_token"],
                   "scope": tokens.get("scope"), "made_at": time.strftime("%Y-%m-%d %H:%M")}, f, indent=1)
    os.replace(OUT + ".tmp", OUT)
    print("Signed in. Saved to %s.\nNow copy it to the hub:\n"
          "    cd deploy && .venv/bin/ansible-playbook site.yml --limit hub" % OUT)


if __name__ == "__main__":
    main()
