// Talking to hubd. One function per call; errors become exceptions carrying the hub's message.

async function request(method, url, body) {
  const response = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error((payload.reasons || [payload.error]).filter(Boolean).join("\n") || `${response.status} ${response.statusText}`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

/** The rig: nodes, phones, hub health, state, budget. watch=1 lets hubd poll the cameras often. */
export const loadStatus = watch => request("GET", "/api/status" + (watch ? "?watch=1" : ""));
/** hub-ingest: takes, their steps, songs and videos, the drive. */
export const loadIngest = () => request("GET", "/api/ingest");
/** The last 30 minutes of health values, one point per 10 s. */
export const loadHistory = () => request("GET", "/api/history");

/** REC, STOP, LIVE on/off, ingest now, eject: a rig action. Resolves with hubd's answer. */
export const rigAction = (path, body = {}) => request("POST", path, body);

/** Name a camera or take ("" goes back to the default). Resolves with every name. */
export const saveName = (kind, id, name) => request("POST", "/api/names", { kind, id, name });
export const setPhoneMount = (phoneId, mount) => request("POST", `/api/phone/${encodeURIComponent(phoneId)}/mount`, { mount });
export const requestSync = (take, song, version) => request("POST", "/api/sync", { take, song, version });

// Bounce uploads (hub/hubd/audio.py)
export const loadSongs = take => request("GET", `/api/audio/${take}`);
export const newSong = (take, name) => request("POST", `/api/audio/${take}/songs`, { name });
export const newVersion = (take, song, filename, bytes) => request("POST", `/api/audio/${take}/${song}/versions`, { filename, bytes });
export const finishUpload = (take, song, version) => request("POST", `/api/audio/${take}/${song}/v${version}/finish`, {});
export const chooseVersion = (take, song, version) => request("POST", `/api/audio/${take}/${song}/choose`, { version });

/** One piece of an upload. Returns the raw response: the uploader handles 409 (rig busy) itself. */
export const putPiece = (take, song, version, offset, blob) =>
  fetch(`/api/audio/${take}/${song}/v${version}?offset=${offset}`, { method: "PUT", body: blob });
