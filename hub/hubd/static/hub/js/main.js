// Hub page entry point (served at /): the rig's deck, monitor and archive.
import { render } from "preact";
import { html } from "shared/html.js";
import { App } from "./components/App.js";
import { applyTheme, savedChoice, themeFor } from "./themes.js";

applyTheme(themeFor(savedChoice()));    // index.html set data-theme already; this also sets theme-color
render(html`<${App} />`, document.getElementById("app"));
