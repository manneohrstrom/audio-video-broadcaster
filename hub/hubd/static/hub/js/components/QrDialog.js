// The phone page's address as a QR code: the fixed hub name, so it matches the Chrome flag
// set on each phone (docs/runbook.md). qrcode.js (vendored, pinned) sets the global qrcode.
import { html } from "shared/html.js";
import { Dialog } from "./Dialog.js";

const PHONE_URL = "http://studio-hub.local/phone";

function qrSvg() {
  try {
    const code = qrcode(0, "M");
    code.addData(PHONE_URL);
    code.make();
    return code.createSvgTag({ cellSize: 6, margin: 4, scalable: true });
  } catch (error) {
    return null;
  }
}

export function QrDialog({ onClose }) {
  const svg = qrSvg();
  return html`
    <${Dialog} title="Use a phone as a camera" onClose=${onClose}>
      <div class="qr">${svg ? html`<div class="qr-code" dangerouslySetInnerHTML=${{ __html: svg }}></div>` : html`<p class="note bad">QR code unavailable.</p>`}</div>
      <p><b>Scan it with the phone's camera</b> to open the phone page: <code>${PHONE_URL}</code></p>
      <p><a href="/phone">Already on the phone? Open it here.</a></p>
      <p class="note">Android and Chrome only. First time on a phone: the setup is at the bottom of the phone page.</p>
    <//>`;
}
