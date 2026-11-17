// Numbers and times in words for the hub page. Pure functions, no DOM.

/** A value with its unit, or "–" when unknown. */
export function fmt(value, unit, digits = 1) {
  return value == null ? "–" : (+value).toFixed(digits) + unit;
}

/** "01:02:03" */
export function hms(seconds) {
  const s = Math.floor(seconds);
  return [s / 3600 | 0, (s / 60 | 0) % 60, s % 60].map(x => String(x).padStart(2, "0")).join(":");
}

/** "1 h 5 min", "12 min" */
export function hoursMinutes(seconds) {
  const minutes = Math.max(0, Math.round(seconds / 60));
  return minutes >= 60 ? `${minutes / 60 | 0} h ${minutes % 60} min` : `${minutes} min`;
}

/** "under a minute", "about 12 min", "about 1 h 5 min" */
export function fmtEta(seconds) {
  if (seconds == null) return "";
  if (seconds < 60) return "under a minute";
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `about ${minutes} min` : `about ${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

export function fmtBytes(bytes) {
  return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(bytes / 1e6)) + " MB";
}

/** "1:02:03" or "2:03" */
export function fmtDur(seconds) {
  const s = Math.round(seconds || 0);
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, rest = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${rest}` : `${m}:${rest}`;
}

/** When a take started, from its id (local time YYYYMMDD-HHMMSS): "14:05", or "16 Nov 14:05". */
export function takeStart(id) {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(id);
  if (!m) return "";
  const day = new Date(+m[1], m[2] - 1, +m[3]);
  const sameDay = day.toDateString() === new Date().toDateString();
  return `${sameDay ? "" : day.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " "}${m[4]}:${m[5]}`;
}

/** "16 Nov, 14:05" from a Unix time in seconds. */
export function when(unixSeconds) {
  return new Date(unixSeconds * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** A camera's mode as the page shows it. "snapshot" is a still being taken (the calibration
 * watcher takes one every 15 s while a hub page is open): the camera is idle, and the page
 * shows a small camera icon by its name instead of changing the text (user, 2026-11-17:
 * the text flickering in and out looked odd). */
export function shownMode(mode) {
  return mode === "snapshot" ? "idle" : mode;
}

/**
 * A Pi camera's lens position as a focus distance, split for the LCD readout (DSEG7 has
 * digits only): { value: "1.20", unit: "m" }, { value: "45", unit: "cm" }, { value: "", unit: "∞" }.
 * libcamera's LensPosition is in dioptres (1 / distance in metres), so 0 is infinity.
 * The module's calibration is approximate: read it as "about".
 */
export function focusParts(lens) {
  if (lens == null) return { value: "", unit: "auto" };
  if (lens <= 0.01) return { value: "", unit: "∞" };
  const metres = 1 / lens;
  if (metres >= 10) return { value: String(Math.round(metres)), unit: "m" };
  if (metres >= 2) return { value: metres.toFixed(1), unit: "m" };
  if (metres >= 1) return { value: metres.toFixed(2), unit: "m" };
  return { value: String(Math.round(metres * 100)), unit: "cm" };
}

/** The same as text: "∞", "4.0 m", "1.20 m", "45 cm", "auto". */
export function focusDistance(lens) {
  const { value, unit } = focusParts(lens);
  return value ? value + " " + unit : unit;
}
