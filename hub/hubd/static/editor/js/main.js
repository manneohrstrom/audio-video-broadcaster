// Editor entry point: /editor/?take=<take id>&song=<song id>&v=<version>[&mode=sync]
// mode=sync opens Sync mode (place the bounce by hand), for synced and unsynced bounces.
import { h, render } from "preact";
import { App } from "./components/App.js";

const query = new URLSearchParams(location.search);
const takeId = query.get("take");
const songId = query.get("song");
const version = Number(query.get("v") || 1);
const mode = query.get("mode") === "sync" ? "sync" : "edit";

const root = document.getElementById("app");
if (!takeId || !songId) {
  root.textContent = "Open a song from the hub page: each synced song has an Edit button.";
} else {
  render(h(App, { takeId, songId, version, mode }), root);
}
