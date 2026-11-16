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

/** Everything the editor needs for one song version (hub/hubd/editor_projects.py). */
export function loadProject(takeId, songId, version) {
  return request("GET", `/api/editor/${takeId}/${songId}/v${version}`);
}

/** Ask the hub to render the finished video of this edit (runs when the rig is idle). */
export function requestRender(takeId, songId, version) {
  return request("POST", `/api/editor/${takeId}/${songId}/v${version}/render`, {});
}

/** The render's progress: { status, progress, eta_s, file, error }. */
export function renderStatus(takeId, songId, version) {
  return request("GET", `/api/editor/${takeId}/${songId}/v${version}/render`);
}

/** Save the working copy of the edit on the hub. */
export function saveEdit(takeId, songId, version, edit) {
  return request("PUT", `/api/editor/${takeId}/${songId}/v${version}`, edit);
}
