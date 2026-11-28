// Talking to the hub (hubd). One function per call; errors become exceptions with the
// hub's own message, so the page can show what went wrong.

async function request(method, url, body) {
  const response = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `${response.status} ${response.statusText}`);
  return payload;
}

/** Everything the editor needs for one song version (hub/hubd/editor_projects.py).
 *  mode "sync": Sync mode, for placing the bounce by hand (any uploaded version). */
export function loadProject(takeId, songId, version, mode = "edit") {
  return request("GET", `/api/editor/${takeId}/${songId}/v${version}${mode === "sync" ? "?mode=sync" : ""}`);
}

/** Sync by hand: the bounce's t=0 is at bounceStartNs on the hub clock (writes sync.json). */
export function saveSync(takeId, songId, version, bounceStartNs) {
  return request("PUT", `/api/editor/${takeId}/${songId}/v${version}/sync`, { bounce_start_ns: Math.round(bounceStartNs) });
}

/** Ask the hub to render the finished video of this edit (runs when the rig is idle).
 *  kind "story": its Instagram story instead (9:16, never uploaded). */
export function requestRender(takeId, songId, version, youtube = false, kind = "edit") {
  return request("POST", `/api/editor/${takeId}/${songId}/v${version}/render`, { youtube, kind });
}

/** Is the hub signed in to YouTube? { signed_in } (mac/youtube_login.py, then the deploy). */
export function youtubeStatus() {
  return request("GET", "/api/youtube");
}

/** The render's progress: { status, progress, eta_s, file, error, upload: { status, progress, url, studio_url, note, error } }. */
export function renderStatus(takeId, songId, version, kind = "edit") {
  return request("GET", `/api/editor/${takeId}/${songId}/v${version}/render${kind === "story" ? "?kind=story" : ""}`);
}

/** Save the working copy of the edit on the hub. */
export function saveEdit(takeId, songId, version, edit) {
  return request("PUT", `/api/editor/${takeId}/${songId}/v${version}`, edit);
}

// -- the AI cut (hub/hubd/ai_cut.py, docs/ai-cut-plan.md)

/** Its settings, defaults, the prompts' fixed parts, the placeholders and whether there is a key. */
export function loadAiSettings() {
  return request("GET", "/api/ai-cut/settings");
}

/** Save its settings (shared by every song). Returns them cleaned. */
export function saveAiSettings(settings) {
  return request("POST", "/api/ai-cut/settings", settings);
}

/** The Gemini models the key can use, each with its price if known: { models }. */
export function loadAiModels() {
  return request("GET", "/api/ai-cut/models");
}

/** The prompts as they would be sent for this song, and the estimated cost. */
export function previewAiCut(takeId, songId, version) {
  return request("GET", `/api/ai-cut/${takeId}/${songId}/v${version}/preview`);
}

/** Start a smart cut of this song (in the background on the hub). Returns the run's summary.
 *  listenAgain: listen even when an earlier run's listening could be reused. */
export function startAiCut(takeId, songId, version, listenAgain = false, extra = "") {
  return request("POST", `/api/ai-cut/${takeId}/${songId}/v${version}/run`, { listen_again: listenAgain, extra });
}

/** The song's runs, newest first: { runs }. */
export function loadAiRuns(takeId, songId) {
  return request("GET", `/api/ai-cut/${takeId}/${songId}/runs`);
}

/** One run in full: settings, prompts, answers, tokens, cost, cuts. */
export function loadAiRun(takeId, songId, runId) {
  return request("GET", `/api/ai-cut/${takeId}/${songId}/runs/${runId}`);
}

/** A new instrument to pick from in every camera's role (camera_tags.py). Returns the vocabulary. */
export function addInstrument(name) {
  return request("POST", "/api/camera-tags/instrument", { name });
}

/** One take's tags for a camera (what it shows in this take; camera_tags.py). */
export function saveTakeCameraTags(takeId, cameraId, tags) {
  return request("POST", "/api/camera-tags/take", { take: takeId, id: cameraId, tags });
}

/** Links for looking into a smart cut by hand: the bounce exactly as sent to Gemini (this
 *  song's now, or a run's), and a run's whole record as a JSON file. */
export const aiSongAudioUrl = (takeId, songId, version) => `/api/ai-cut/${takeId}/${songId}/v${version}/audio`;
export const aiRunAudioUrl = (takeId, songId, runId) => `/api/ai-cut/${takeId}/${songId}/runs/${runId}/audio`;
export const aiRunFileUrl = (takeId, songId, runId) => `/api/ai-cut/${takeId}/${songId}/runs/${runId}?download=1`;
