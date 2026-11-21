// The phone pages' addresses as QR codes: the fixed hub name, so it matches the Chrome flag
// set on each phone (docs/runbook.md). Two pages (user, 2026-11-21): handheld, the full page
// as before; tripod, a dark screen for a phone left in a bracket, set up from this page.
// qrcode.js (vendored, pinned) sets the global qrcode.
import { html } from "shared/html.js";
import { Dialog } from "./Dialog.js";

const PAGES = [
  { url: "http://studio-hub.local/phone", path: "/phone", title: "Handheld",
    note: "The full page: name, focus, manual values. For a phone in your hand." },
  { url: "http://studio-hub.local/phone/tripod", path: "/phone/tripod", title: "Tripod",
    note: "A dark screen and nothing to press. Name it, identify it and reload it from the Monitor here." },
];

function qrSvg(url) {
  try {
    const code = qrcode(0, "M");
    code.addData(url);
    code.make();
    return code.createSvgTag({ cellSize: 6, margin: 4, scalable: true });
  } catch (error) {
    return null;
  }
}

export function QrDialog({ onClose }) {
  return html`
    <${Dialog} title="Use a phone as a camera" onClose=${onClose}>
      <div class="qr-pair">
        ${PAGES.map(page => {
          const svg = qrSvg(page.url);
          return html`<figure class="qr">
            ${svg ? html`<div class="qr-code" dangerouslySetInnerHTML=${{ __html: svg }}></div>` : html`<p class="note bad">QR code unavailable.</p>`}
            <figcaption><b>${page.title}</b> <a href=${page.path}><code>${page.path}</code></a><br /><span class="note">${page.note}</span></figcaption>
          </figure>`;
        })}
      </div>
      <p><b>Scan one with the phone's camera.</b> Each phone shows up here by its id (phone-…) until you give it a name.</p>
      <p class="note">Android and Chrome only. First time on a phone: the setup is at the bottom of the handheld page, and in the runbook for tripod phones.</p>
    <//>`;
}
