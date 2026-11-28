// One smart cut run in full, for looking into it and trying it by hand (user, 2026-11-28):
// both prompts exactly as sent, with the settings and answer format (JSON schema) that went
// with them, Gemini's answers, the tokens and cost of each call, what the hub changed or left
// out, and downloads of the bounce as sent and of the whole run.
import { useEffect, useState } from "preact/hooks";
import { html } from "./html.js";
import { Icon } from "shared/icons.js";
import { CopyText } from "./CopyText.js";
import { aiRunAudioUrl, aiRunFileUrl, loadAiRun } from "../api.js";
import { money } from "../money.js";
import { formatSeconds } from "../time-format.js";

const NS_PER_SECOND = 1e9;

/** The answer as indented JSON when it is JSON, else the raw text. */
function answerOf(call) {
  return call.answer != null ? call.answer : call.text;
}

function CallFacts({ call }) {
  if (!call) return null;
  const tokens = call.tokens || {};
  return html`<p class="ai-note">${(tokens.text || 0).toLocaleString()} text + ${(tokens.audio || 0).toLocaleString()} audio tokens in,
    ${(tokens.output || 0).toLocaleString()} out (thinking ${((call.usage || {}).thoughtsTokenCount || 0).toLocaleString()}) ·
    ${call.seconds} s · ${money(call.cost_usd)} · ${call.finish || ""}${call.reused_from ? ` · reused from run ${call.reused_from}` : ""}</p>`;
}

export function AiRunDetails({ store, runId, onClose }) {
  const { take_id: takeId, song } = store.project;
  const [run, setRun] = useState(null);
  const [problem, setProblem] = useState(null);
  useEffect(() => { loadAiRun(takeId, song.id, runId).then(setRun).catch(error => setProblem(error.message)); }, [runId]);

  const changed = run && run.cuts ? run.cuts.filter(cut => cut.note) : [];
  return html`
    <div class="editor-backdrop" onClick=${event => { if (event.target === event.currentTarget) onClose(); }}>
      <section class="editor-dialog ai-details" role="dialog" aria-modal="true" aria-label="Smart cut run">
        <header>
          <h2>Run ${runId}${run ? ` · ${run.settings.model} · ${run.status}` : ""}</h2>
          <button class="round-key" onClick=${onClose} aria-label="Close"><${Icon} name="close" size=${16} /></button>
        </header>
        <div class="ai-settings-body">
          ${!run ? html`<p class="ai-note">${problem || "Loading…"}</p>` : html`
            <p class="ai-note">${money(run.cost_usd)} in all (estimated ${money((run.estimate || {}).cost_usd)}) ·
              thinking ${run.settings.thinking} · temperature ${run.settings.temperature ?? "the model's own"} ·
              longest answer ${run.settings.max_output_tokens} tokens</p>
            <div class="ai-keys ai-downloads">
              <a class="button quiet" href=${aiRunAudioUrl(takeId, song.id, runId)} download>bounce as sent (mp3)</a>
              <a class="button quiet" href=${aiRunFileUrl(takeId, song.id, runId)} download>whole run (json)</a>
            </div>
            ${run.error && html`<p class="ai-note problem">${run.error.message}</p>`}
            ${run.failed_call && html`<${CopyText} title="The answer that failed" text=${run.failed_call.text} open />`}

            <h3>1. Listen</h3>
            <${CopyText} title="Prompt, as sent (with the bounce)" text=${run.prompts.listen} />
            ${run.listening && html`
              <${CopyText} title="Settings and answer format sent with it" text=${run.listening.config || "(not recorded for this run)"} />
              <${CallFacts} call=${run.listening} />
              <${CopyText} title="Answer" text=${answerOf(run.listening)} />`}

            <h3>2. Edit</h3>
            <${CopyText} title="Prompt, as sent (same conversation)" text=${run.prompts.edit} />
            ${run.editing && html`
              <${CopyText} title="Settings and answer format sent with it" text=${run.editing.config || "(not recorded for this run)"} />
              <${CallFacts} call=${run.editing} />
              <${CopyText} title="Answer" text=${answerOf(run.editing)} />`}

            ${run.cuts && html`
              <h3>3. What the hub did</h3>
              <p class="ai-note">${run.cuts.length} cuts placed; ${changed.length} changed by the hub, ${(run.left_out || []).length} left out.</p>
              ${changed.length > 0 && html`<${CopyText} title="Cuts the hub changed" text=${changed.map(cut =>
                `${formatSeconds(cut.at_ns / NS_PER_SECOND)}  ${cut.camera}  ${cut.note}`).join("\n")} />`}
              ${(run.left_out || []).length > 0 && html`<${CopyText} title="Cuts left out" text=${run.left_out.map(cut =>
                `${cut.time_s == null ? "" : cut.time_s.toFixed(1) + " s"}  ${cut.camera}  ${cut.left_out}  (${cut.why || ""})`).join("\n")} />`}
              <${CopyText} title="The cuts as placed" text=${run.cuts} />`}`}
        </div>
      </section>
    </div>`;
}
