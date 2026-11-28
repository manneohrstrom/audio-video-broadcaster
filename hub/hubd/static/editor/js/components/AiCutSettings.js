// The AI cut's settings (docs/ai-cut-plan.md): the model and how it thinks, the two prompts
// (the creative direction is the user's; the hub adds a fixed part with the song's data and
// the answer's format), and what each camera shows in this take. The model and prompts are
// shared by every song; the cameras' tags belong to this take.
import { useEffect, useRef, useState } from "preact/hooks";
import { html } from "./html.js";
import { Icon } from "shared/icons.js";
import { RoleEditor } from "shared/camera-role.js";
import { addInstrument, aiSongAudioUrl, loadAiModels, loadAiSettings, previewAiCut, saveAiSettings, saveTakeCameraTags } from "../api.js";
import { CopyText } from "./CopyText.js";
import { money } from "../money.js";

const TABS = [["model", "Model"], ["prompts", "Prompts"], ["cameras", "Cameras"]];
const THINKING_WORDS = { default: "the model's own", minimal: "minimal", low: "low", medium: "medium", high: "high", xhigh: "extra high" };

function priceLine(price) {
  if (!price) return "price not known (not in the hub's price table)";
  const audio = price.audio != null && price.audio !== price.input ? `, audio $${price.audio}` : "";
  return `$${price.input} in${audio}, $${price.output} out, per million tokens`;
}

export function AiCutSettings({ store, onClose }) {
  const [page, setPage] = useState(null);                // the hub's settings page (settings, defaults, fixed parts...)
  const [draft, setDraft] = useState(null);
  const [models, setModels] = useState(null);
  const [tab, setTab] = useState("model");
  const [tags, setTags] = useState(() => Object.fromEntries(store.cameras.map(camera => [camera.id, camera.tags])));
  const [changedCameras, setChangedCameras] = useState(new Set());
  const [problem, setProblem] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadAiSettings().then(found => { setPage(found); setDraft(found.settings); }).catch(error => setProblem(error.message));
    loadAiModels().then(found => setModels(found.models)).catch(error => setProblem(error.message));
  }, []);

  const change = changes => setDraft(current => ({ ...current, ...changes }));
  const changeTags = (cameraId, next) => {
    setTags(current => ({ ...current, [cameraId]: next }));
    setChangedCameras(current => new Set(current).add(cameraId));
  };

  async function save() {
    setSaving(true);
    setProblem(null);
    try {
      await saveAiSettings(draft);
      for (const cameraId of changedCameras) {
        const saved = await saveTakeCameraTags(store.project.take_id, cameraId, tags[cameraId]);
        store.setCameraTags(cameraId, saved);
      }
      onClose();
    } catch (error) {
      setProblem(error.message);
      setSaving(false);
    }
  }

  return html`
    <div class="editor-backdrop" onClick=${event => { if (event.target === event.currentTarget) onClose(); }}>
      <section class="editor-dialog ai-settings" role="dialog" aria-modal="true" aria-label="Smart cut settings">
        <header>
          <h2>Smart cut settings</h2>
          <nav class="steps" aria-label="Settings">
            ${TABS.map(([id, label]) => html`<button aria-current=${tab === id ? "step" : null} onClick=${() => setTab(id)}>${label}</button>`)}
          </nav>
          <button class="round-key" onClick=${onClose} aria-label="Close"><${Icon} name="close" size=${16} /></button>
        </header>
        <div class="ai-settings-body">
          ${!draft ? html`<p class="ai-note">${problem || "Loading…"}</p>`
            : tab === "model" ? html`<${ModelTab} page=${page} draft=${draft} models=${models} change=${change} />`
            : tab === "prompts" ? html`<${PromptsTab} page=${page} draft=${draft} change=${change} store=${store} />`
            : html`<${CamerasTab} store=${store} tags=${tags} changeTags=${changeTags} onProblem=${setProblem} />`}
        </div>
        <footer>
          <span class="ai-note problem">${problem || ""}</span>
          <button class="button quiet" onClick=${onClose}>cancel</button>
          <button class="button signal" disabled=${!draft || saving} onClick=${save}><${Icon} name="check" size=${16} />save</button>
        </footer>
      </section>
    </div>`;
}

function ModelTab({ page, draft, models, change }) {
  const chosen = (models || []).find(model => model.id === draft.model);
  const listed = models && !chosen ? [{ id: draft.model, name: draft.model, price: null }, ...models] : models || [];
  return html`
    ${!page.has_key && html`<p class="ai-note problem">There's no Gemini key on the hub yet: put it in ai_studio_key in the repo folder and deploy.</p>`}
    <label class="ai-field"><span>Model</span>
      <select value=${draft.model} onChange=${event => change({ model: event.target.value })}>
        ${listed.map(model => html`<option value=${model.id}>${model.name} (${model.id})</option>`)}
      </select>
      <small>${models ? priceLine(chosen && chosen.price) : "asking Google which models the key can use…"}</small>
    </label>
    <label class="ai-field"><span>Thinking</span>
      <select value=${draft.thinking} onChange=${event => change({ thinking: event.target.value })}>
        ${page.thinking_levels.map(level => html`<option value=${level}>${THINKING_WORDS[level] || level}</option>`)}
      </select>
      <small>How long it reasons before answering: the biggest lever on quality and cost (thinking is paid as output).
        Not every model takes every level.</small>
    </label>
    <label class="ai-field"><span>Temperature</span>
      <div class="ai-inline">
        <input type="checkbox" checked=${draft.temperature == null}
               onChange=${event => change({ temperature: event.target.checked ? null : 1.0 })} /> the model's own
        ${draft.temperature != null && html`
          <input type="range" min="0" max="2" step="0.1" value=${draft.temperature}
                 onInput=${event => change({ temperature: Number(event.target.value) })} /><output>${draft.temperature.toFixed(1)}</output>`}
      </div>
      <small>How much the answer varies from run to run. Google advises the model's own for Gemini 3 and later.</small>
    </label>
    <label class="ai-field"><span>Longest answer</span>
      <input type="number" min="1024" max="65536" step="1024" value=${draft.max_output_tokens}
             onChange=${event => change({ max_output_tokens: Math.round(Number(event.target.value)) })} />
      <small>Tokens per answer, thinking included. There's no context window to choose: a song uses a few percent of it.</small>
    </label>
    <p class="ai-note">Prices: the hub's table of ${page.prices_as_of}${page.prices_coming ? `. ${page.prices_coming}` : ""}</p>`;
}

function PromptsTab({ page, draft, change, store }) {
  const [preview, setPreview] = useState(null);
  const placeholders = Object.entries(page.placeholders);
  async function showPreview() {
    try {
      await saveAiSettings(draft);                        // the preview is made from the saved settings
      const { take_id: takeId, song, version } = store.project;
      setPreview(await previewAiCut(takeId, song.id, version));
    } catch (error) {
      setPreview({ error: error.message });
    }
  }
  return html`
    <p class="ai-note">Two steps in one conversation: first it listens (with the bounce), then it cuts. Each prompt is your
      direction, then the hub's fixed part added after it (the song's data and the answer's fields in words). The exact answer
      format goes alongside the prompt as a JSON schema that Gemini must follow. The {{…}} buttons put the song's data in
      where the cursor is (hover one to see what).</p>
    <${PromptBox} label="1. Listen" value=${draft.listen_direction} fixed=${page.fixed.listen} schema=${page.schemas.listen} placeholders=${placeholders}
                  onChange=${text => change({ listen_direction: text })} onReset=${() => change({ listen_direction: page.defaults.listen_direction })} />
    <${PromptBox} label="2. Edit" value=${draft.edit_direction} fixed=${page.fixed.edit} schema=${page.schemas.edit} placeholders=${placeholders}
                  onChange=${text => change({ edit_direction: text })} onReset=${() => change({ edit_direction: page.defaults.edit_direction })} />
    <div class="ai-keys">
      <a class="button quiet" href=${aiSongAudioUrl(store.project.take_id, store.project.song.id, store.project.version)} download
         title="This song's bounce exactly as it goes to Gemini: mono MP3">bounce as sent (mp3)</a>
      <button class="button quiet" onClick=${showPreview} title="Saves the settings, then shows both prompts as they'd be sent">preview for this song</button>
    </div>
    ${preview && (preview.error ? html`<p class="ai-note problem">${preview.error}</p>` : html`
      <p class="ai-note">This song: about ${money(preview.estimate.cost_usd)}${preview.reuses_listening ? " (the listening would be reused)" : ""}.
        To try it by hand: the listen prompt with the bounce, then the edit prompt in the same chat, each with its answer format.</p>
      <${CopyText} title="1. Listen prompt, as it would be sent" text=${preview.prompts.listen} open />
      <${CopyText} title="1. Its settings and answer format (generationConfig)" text=${preview.configs.listen} />
      <${CopyText} title="2. Edit prompt, as it would be sent" text=${preview.prompts.edit} open />
      <${CopyText} title="2. Its settings and answer format (generationConfig)" text=${preview.configs.edit} />`)}`;
}

function PromptBox({ label, value, fixed, schema, placeholders, onChange, onReset }) {
  const box = useRef(null);
  const insert = name => {
    const field = box.current;
    const at = field ? field.selectionStart : value.length;
    onChange(value.slice(0, at) + `{{${name}}}` + value.slice(field ? field.selectionEnd : at));
  };
  return html`
    <div class="ai-prompt">
      <div class="ai-prompt-head"><strong>${label}</strong>
        <span>${placeholders.map(([name, what]) => html`<button class="ai-chip" title=${what} onClick=${() => insert(name)}>{{${name}}}</button>`)}</span>
        <button class="button quiet" onClick=${onReset}>default</button></div>
      <textarea ref=${box} rows="9" value=${value} onInput=${event => onChange(event.target.value)}></textarea>
      <${CopyText} title="Added after your text by the hub (fixed)" text=${fixed} />
      <${CopyText} title="Answer format, sent alongside (JSON schema)" text=${schema} />
    </div>`;
}

function CamerasTab({ store, tags, changeTags, onProblem }) {
  const [vocabulary, setVocabulary] = useState(store.project.tag_vocabulary);
  if (!vocabulary) return html`<p class="ai-note">This hub doesn't have camera tags yet.</p>`;
  async function addOne(name) {                          // a new instrument, for every camera
    try {
      const next = { ...vocabulary, ...(await addInstrument(name)) };
      store.project.tag_vocabulary = next;
      setVocabulary(next);
    } catch (error) {
      onProblem(error.message);
    }
  }
  const from = { take: "this take's", recorded: "as at REC", current: "the camera's current role", null: "not said yet" };
  return html`
    <p class="ai-note">What each camera shows in this take: it goes into the edit prompt as the camera table ({{cameras}}).
      Changes here are for this take only; the hub page sets a camera's role for the takes to come.</p>
    ${store.cameras.map(camera => html`
      <div class="ai-camera">
        <div class="ai-camera-name" style=${{ "--camera-colour": `var(--color-camera-${(camera.index % 6) + 1})` }}>
          <span class="camera-key">${camera.number}</span>
          <span><strong>${camera.label}</strong><small>${camera.id} · ${camera.kind === "phone" ? camera.mount + " phone" : "Pi, fixed"} · ${from[camera.tags_from] || ""}</small></span>
        </div>
        <${RoleEditor} vocabulary=${vocabulary} tags=${tags[camera.id]} onChange=${next => changeTags(camera.id, next)}
                       onAddInstrument=${addOne} />
      </div>`)}`;
}
