"""A small client for Google's Gemini API (AI Studio key), standard library only, for the AI
cut (ai_cut.py, docs/ai-cut-plan.md): the models, a file upload, and one generateContent call.

Checked against the live API 2026-11-28: generateContent with generationConfig.thinkingConfig
(thinkingLevel for Gemini 3 and later, thinkingBudget for 2.5), responseMimeType
"application/json" with responseJsonSchema, usageMetadata with promptTokenCount,
candidatesTokenCount, thoughtsTokenCount and promptTokensDetails per modality. Files go
through the resumable upload (upload/v1beta/files) and are kept by Google for 48 hours.

Every failure is a GeminiError with a kind the editor can explain (see EXPLAINED).
"""
import json
import os
import time
import urllib.error
import urllib.request

API = "https://generativelanguage.googleapis.com"
UPLOAD_CHUNK = 1 << 20

# What the user is told for each kind of failure (the editor shows it next to the Smart cut button).
EXPLAINED = {
    "no_key": "There's no Gemini key on the hub: put the AI Studio key in ai_studio_key in the repo folder and deploy.",
    "key": "Google refused the key: check it in AI Studio (aistudio.google.com → API keys) and deploy it again.",
    "credit": "The AI Studio credit is used up: top it up in AI Studio (aistudio.google.com → Billing), then try again.",
    "rate": "Google's rate limit for this key was reached: wait a minute and try again.",
    "model": "That model isn't available for this key (any more): choose another in the smart cut settings.",
    "too_big": "The request was too big for the model: try a shorter answer or less thinking.",
    "busy": "Gemini is overloaded or had an error: try again in a few minutes.",
    "network": "The hub couldn't reach Google: check the hub's internet connection.",
    "blocked": "Gemini didn't answer: it stopped for a safety or policy reason.",
    "answer": "Gemini's answer wasn't what was asked for (cut short, or not valid JSON).",
    "other": "Gemini returned an error.",
}


class GeminiError(Exception):
    def __init__(self, kind, detail=""):
        self.kind = kind
        self.detail = detail
        super().__init__(EXPLAINED.get(kind, EXPLAINED["other"]) + (" (%s)" % detail if detail else ""))


def classify(status, message):
    """The kind of an HTTP error from Google, from its status and message. Prepaid credit
    running out comes back as 429 RESOURCE_EXHAUSTED like a rate limit ("Your prepayment
    credits are depleted"); a rate limit says which limit and when to retry, and its text
    also mentions "billing details", so the credit words are looked for first."""
    text = (message or "").lower()
    if status in (401, 403) or "api key" in text or "api_key" in text:
        return "key"
    if status == 402 or any(word in text for word in ("depleted", "prepay", "credit", "balance")):
        return "credit"
    if status == 429 and any(words in text for words in ("retry in", "per minute", "per day", "limit:", "rate limit")):
        return "rate"
    if status == 429 or "billing" in text:
        return "credit"
    if status == 404 or "not found" in text or "is not supported" in text:
        return "model"
    if status == 413 or "too large" in text or "exceeds the maximum" in text:
        return "too_big"
    if status in (500, 502, 503, 504):
        return "busy"
    return "other"


class Gemini:
    def __init__(self, key_path, timeout_s=600, opener=urllib.request.urlopen):
        self.key_path = key_path
        self.timeout_s = timeout_s
        self.opener = opener

    def key(self):
        try:
            with open(self.key_path) as f:
                key = f.read().strip()
        except OSError:
            key = ""
        if not key:
            raise GeminiError("no_key")
        return key

    def has_key(self):
        try:
            self.key()
            return True
        except GeminiError:
            return False

    # -- requests

    def _request(self, method, url, body=None, headers=None, raw=None):
        """(response JSON, response headers). body: JSON; raw: bytes or a file object."""
        all_headers = {"x-goog-api-key": self.key()}
        all_headers.update(headers or {})
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            all_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=all_headers)
        try:
            with self.opener(request, timeout=self.timeout_s) as response:
                text = response.read().decode(errors="replace")
                return (json.loads(text) if text.strip() else {}), dict(response.headers)
        except urllib.error.HTTPError as error:
            text = error.read().decode(errors="replace")
            try:
                message = json.loads(text)["error"].get("message", "")
            except (ValueError, KeyError, TypeError, AttributeError):
                message = text[:300]
            raise GeminiError(classify(error.code, message), "%d: %s" % (error.code, message[:300])) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise GeminiError("network", str(getattr(error, "reason", error))[:200]) from None

    def models(self):
        """[{id, name, thinking, input_tokens, output_tokens}] of the Gemini models this key can
        use for text answers (no speech, image, live, embedding or robotics models)."""
        found, token = [], None
        while True:
            url = API + "/v1beta/models?pageSize=200" + ("&pageToken=" + token if token else "")
            page, _ = self._request("GET", url)
            for model in page.get("models") or []:
                model_id = model.get("name", "").split("/", 1)[-1]
                if ("generateContent" not in (model.get("supportedGenerationMethods") or [])
                        or not model_id.startswith("gemini")
                        or any(word in model_id for word in ("tts", "image", "live", "embedding", "robotics",
                                                             "computer-use", "transcribe", "banana", "customtools"))):
                    continue
                found.append({"id": model_id, "name": model.get("displayName") or model_id,
                              "thinking": bool(model.get("thinking")),
                              "input_tokens": model.get("inputTokenLimit"), "output_tokens": model.get("outputTokenLimit")})
            token = page.get("nextPageToken")
            if not token:
                return found

    def upload(self, path, mime_type, display_name):
        """Upload a file (resumable protocol) and wait until Google can use it. Returns its uri."""
        size = os.path.getsize(path)
        _, headers = self._request("POST", API + "/upload/v1beta/files", body={"file": {"display_name": display_name}},
                                   headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                                            "X-Goog-Upload-Header-Content-Length": str(size),
                                            "X-Goog-Upload-Header-Content-Type": mime_type})
        upload_url = next((value for name, value in headers.items() if name.lower() == "x-goog-upload-url"), None)
        if not upload_url:
            raise GeminiError("other", "no upload address in Google's answer")
        with open(path, "rb") as f:
            answer, _ = self._request("POST", upload_url, raw=f,
                                      headers={"Content-Length": str(size), "X-Goog-Upload-Offset": "0",
                                               "X-Goog-Upload-Command": "upload, finalize"})
        file = answer.get("file") or {}
        deadline = time.monotonic() + 120
        while file.get("state") == "PROCESSING" and time.monotonic() < deadline:
            time.sleep(2)
            file, _ = self._request("GET", API + "/v1beta/" + file["name"])
        if file.get("state") not in (None, "ACTIVE") or not file.get("uri"):
            raise GeminiError("other", "the uploaded file is %s" % file.get("state"))
        return file["uri"]

    def generate(self, model, contents, config):
        """One generateContent call. Returns (answer text, usage, finish reason, model version).
        contents: the conversation so far (Gemini's format); config: its generationConfig."""
        answer, _ = self._request("POST", "%s/v1beta/models/%s:generateContent" % (API, model),
                                  body={"contents": contents, "generationConfig": config})
        candidates = answer.get("candidates") or []
        if not candidates:
            reason = (answer.get("promptFeedback") or {}).get("blockReason") or "no answer"
            raise GeminiError("blocked", reason)
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        return text, answer.get("usageMetadata") or {}, candidate.get("finishReason"), answer.get("modelVersion")
