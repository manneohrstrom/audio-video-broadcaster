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
