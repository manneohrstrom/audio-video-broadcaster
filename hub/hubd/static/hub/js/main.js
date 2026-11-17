// Hub page entry point (served at /): the rig's deck, monitor and archive.
import { render } from "preact";
import { html } from "shared/html.js";
import { App } from "./components/App.js";

render(html`<${App} />`, document.getElementById("app"));
