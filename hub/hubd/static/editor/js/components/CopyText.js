// A block of text to look at and copy (prompts, answers, schemas), for trying the smart cut
// by hand elsewhere. The hub page isn't a secure page, so the clipboard API may be missing:
// then the older "select and copy" way.
import { useState } from "preact/hooks";
import { html } from "./html.js";

function copy(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const field = document.createElement("textarea");
  field.value = text;
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.append(field);
  field.select();
  const done = document.execCommand("copy");
  field.remove();
  return done ? Promise.resolve() : Promise.reject(new Error("the browser refused to copy"));
}

/** title: what it is; text: a string, or anything (shown as indented JSON). */
export function CopyText({ title, text, open = false }) {
  const [state, setState] = useState("");
  const shown = typeof text === "string" ? text : JSON.stringify(text, null, 2);
  const onCopy = event => {
    event.preventDefault();
    copy(shown).then(() => setState("copied"), error => setState(error.message));
    setTimeout(() => setState(""), 2000);
  };
  return html`
    <details class="copy-text" open=${open}>
      <summary><span>${title}</span><small>${shown.length.toLocaleString()} characters</small>
        <button class="button quiet" onClick=${onCopy}>${state || "copy"}</button></summary>
      <pre class="ai-preview">${shown}</pre>
    </details>`;
}
