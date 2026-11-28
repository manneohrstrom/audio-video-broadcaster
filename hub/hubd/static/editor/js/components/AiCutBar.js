// The smart cut (docs/ai-cut-plan.md, hub/hubd/ai_cut.py; "AI cut" in the code, "Smart cut"
// on screen: user, 2026-11-28), above the timeline in the Cut step: "Smart cut" runs Gemini on this song (after showing what it will cost), the settings key opens
// the model, prompts and cameras, and "runs" lists the song's runs to use any one's cut again.
// A run's cut replaces the song's cut through the edit history: Undo brings back the one before.
import { useEffect, useState } from "preact/hooks";
import { html } from "./html.js";
import { Icon } from "shared/icons.js";
import { AiCutSettings } from "./AiCutSettings.js";
import { AiRunDetails } from "./AiRunDetails.js";
import { loadAiRun, loadAiRuns, previewAiCut, startAiCut } from "../api.js";
import { money } from "../money.js";

const POLL_MS = 2000;

function when(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

export function AiCutBar({ store }) {
  const { take_id: takeId, song, version } = store.project;
  const [runs, setRuns] = useState([]);
  const [running, setRunning] = useState(null);          // the run in progress (summary)
  const [problem, setProblem] = useState(null);
  const [asking, setAsking] = useState(null);             // the preview while "run it?" is open
  const [panel, setPanel] = useState(null);               // "settings" | "runs" | null
  const [details, setDetails] = useState(null);           // the run shown in full, or null

  const refreshRuns = () => loadAiRuns(takeId, song.id).then(found => {
    setRuns(found.runs);
    const inProgress = found.runs.find(run => run.status === "running");
    if (inProgress) setRunning(inProgress);
    return found.runs;
  }).catch(() => []);

  useEffect(() => {
    refreshRuns();
    if (store.aiRunId) {                                  // the run the saved cut came from: Music lane, reasons
      loadAiRun(takeId, song.id, store.aiRunId).then(run => { store.aiRun = run; store.notify(); }).catch(() => {});
    }
  }, []);

  useEffect(() => {                                       // follow a run until it's done
    if (!running) return undefined;
    const timer = setInterval(async () => {
      try {
        const run = await loadAiRun(takeId, song.id, running.id);
        if (run.status === "running") {
          setRunning(run);
          return;
        }
        clearInterval(timer);
        setRunning(null);
        refreshRuns();
        if (run.status === "done") {
          store.applyAiRun(run);
          store.showToast(`Smart cut: ${run.cuts.length} cuts · ${money(run.cost_usd)} · Undo brings back your cut`);
        } else {
          setProblem(run.error ? run.error.message : "the smart cut failed");
        }
      } catch (error) {
        setProblem(error.message);
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [running && running.id]);

  async function ask() {
    setProblem(null);
    try {
      setAsking(await previewAiCut(takeId, song.id, version));
    } catch (error) {
      setProblem(error.message);
    }
  }

  async function run(listenAgain) {
    setAsking(null);
    if (store.saveState !== "saved") await store.saveNow();
    try {
      setRunning(await startAiCut(takeId, song.id, version, listenAgain));
    } catch (error) {
      setProblem(error.message);
    }
  }

  async function useRun(summary) {
    setPanel(null);
    try {
      store.applyAiRun(await loadAiRun(takeId, song.id, summary.id));
    } catch (error) {
      setProblem(error.message);
    }
  }

  const finished = runs.filter(entry => entry.status !== "running");
  return html`
    <div class="ai-cut">
      <button class="button signal-soft" disabled=${!!running} onClick=${ask} title="Gemini listens to the bounce and cuts the song">
        ${running ? "Smart cut…" : "Smart cut"}</button>
      <button class="round-key" onClick=${() => setPanel("settings")} aria-label="Smart cut settings" title="Model, prompts and cameras">
        <${Icon} name="sliders" size=${16} /></button>
      ${finished.length > 0 && html`<button class="button quiet" onClick=${() => setPanel(panel === "runs" ? null : "runs")}
                                           title="This song's smart cuts: use any one's cut again">${finished.length} run${finished.length === 1 ? "" : "s"}</button>`}
      <span class=${"ai-cut-state" + (problem ? " problem" : "")} title=${problem || ""}>
        ${running ? `${running.step_text || "starting"}…` : problem || ""}</span>
      ${asking && html`<${RunConfirm} preview=${asking} onRun=${run} onCancel=${() => setAsking(null)}
                                      onSettings=${() => { setAsking(null); setPanel("settings"); }} />`}
      ${panel === "runs" && html`<${RunList} runs=${finished} current=${store.aiRunId} onUse=${useRun}
                                             onDetails=${run => { setPanel(null); setDetails(run.id); }} onClose=${() => setPanel(null)} />`}
      ${details && html`<${AiRunDetails} store=${store} runId=${details} onClose=${() => setDetails(null)} />`}
      ${panel === "settings" && html`<${AiCutSettings} store=${store} onClose=${() => setPanel(null)} />`}
    </div>`;
}

function RunConfirm({ preview, onRun, onCancel, onSettings }) {
  const estimate = preview.estimate;
  const [reuse, setReuse] = useState(true);            // the earlier run's listening, when it fits
  return html`
    <div class="ai-popover" role="dialog" aria-label="Run the smart cut">
      <p><strong>Cut this song with ${preview.model || "Gemini"}?</strong></p>
      <p>About ${money(estimate.cost_usd)}: ${Math.round(estimate.input_tokens / 1000)}k tokens in, up to
         ~${Math.round(estimate.output_tokens / 1000)}k out${preview.reuses_listening && reuse ? ", less with the listening reused" : ""}.
         It takes a minute or two. The new cut replaces this one; Undo brings it back.</p>
      ${preview.reuses_listening && html`
        <label class="ai-inline"><input type="checkbox" checked=${reuse} onChange=${event => setReuse(event.target.checked)} />
          use the listening from run ${preview.reusable_from} (same bounce, model, settings and listen prompt);
          untick to listen again: it can hear the song differently each time</label>`}
      <div class="ai-keys">
        <button class="button quiet" onClick=${onSettings}>settings</button>
        <button class="button quiet" onClick=${onCancel}>cancel</button>
        <button class="button signal" onClick=${() => onRun(preview.reuses_listening && !reuse)}>Run</button>
      </div>
    </div>`;
}

function RunList({ runs, current, onUse, onDetails, onClose }) {
  const total = runs.reduce((sum, run) => sum + (run.cost_usd || 0), 0);
  return html`
    <div class="ai-popover ai-runs" role="dialog" aria-label="Smart cut runs">
      <header><strong>Smart cuts of this song</strong><span>${money(total)} in all</span>
        <button class="round-key" onClick=${onClose} aria-label="Close"><${Icon} name="close" size=${14} /></button></header>
      <ol>
        ${runs.map(run => html`
          <li class=${run.id === current ? "current" : ""}>
            <div class="ai-run-line">
              <span>${when(run.started_at)}</span><span>${run.model}${run.thinking && run.thinking !== "default" ? ` · ${run.thinking}` : ""}</span>
              <span>${run.status === "done" ? `${run.cuts} cuts` : "failed"}</span><span>${money(run.cost_usd)}</span>
              <span class="ai-run-keys">
                <button class="button quiet" onClick=${() => onDetails(run)} title="The prompts as sent, the answers, tokens and what the hub changed">details</button>
                ${run.status === "done" && html`<button class="button quiet" onClick=${() => onUse(run)}>${run.id === current ? "use again" : "use"}</button>`}
              </span>
            </div>
            <small>${run.status === "done" ? (run.summary || "") : (run.error && run.error.message) || ""}${run.reused_listening ? " (listening reused)" : ""}</small>
          </li>`)}
      </ol>
    </div>`;
}
