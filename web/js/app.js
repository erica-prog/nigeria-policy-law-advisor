// Chat-first client (revision 2). One screen: the advisor character in the
// middle with a speech bubble, the conversation around it, and a composer at
// the bottom with a + button for documents. Cases (matters) are created
// behind the scenes; the user only ever sees titles. All trust decisions
// (evidence vs authority vs web, avatar state, research-mode labelling) come
// from the server and are rendered as received.

import { api, ApiError } from "./api.js";
import { createAvatar } from "./avatar.js";
import { createBubble } from "./bubble.js";
import { createPassageDialog, el } from "./citations.js";
import { renderErrorMessage, renderReply, renderUserMessage } from "./messages.js";

const $ = (selector) => document.querySelector(selector);

const GREETING = "Hi, I'm your advisor. Tell me about your case, or add your documents with +";
const MISSING_KEY =
  "I can't think yet: the server has no Claude API key. Add ANTHROPIC_API_KEY to the .env file " +
  "next to the app and restart.";
const CURRENT_CASE_KEY = "pa.currentCase";

const state = {
  user: null,
  llmConfigured: true,
  matter: null,
  messages: [],
  documents: [],
  uploads: new Map(), // name -> { status: "uploading" | "failed", error }
  readyNames: new Set(),
  pollTimer: null,
  busy: false,
  pendingIntent: null,
  lastQuestion: null,
};

const views = { login: $("#view-login"), chat: $("#view-chat") };
const transcript = $("#transcript");
const composer = $("#composer");
const composerInput = $("#composer-input");
const advisor = createAvatar($("#advisor"));
const bubble = createBubble($("#bubble"));
const openPassage = createPassageDialog($("#passage-dialog"));

function showView(name) {
  for (const [key, node] of Object.entries(views)) node.hidden = key !== name;
  composer.hidden = name !== "chat";
  $("#drawer-toggle").hidden = name !== "chat";
}

function setError(node, error) {
  node.textContent = error ? error.message || String(error) : "";
  node.hidden = !error;
}

function handleAuthError(error) {
  if (error instanceof ApiError && error.status === 401) {
    state.user = null;
    renderUserBar();
    showView("login");
    return true;
  }
  return false;
}

// ---------- Session ----------

async function bootstrap() {
  try {
    const health = await api.health();
    state.llmConfigured = Boolean(health.llm_configured);
  } catch {
    state.llmConfigured = false;
  }
  try {
    state.user = await api.me();
  } catch {
    state.user = null;
  }
  renderUserBar();
  if (!state.user) {
    showView("login");
    return;
  }
  await enterChat();
}

function renderUserBar() {
  $("#topbar-user").hidden = !state.user;
  $("#user-name").textContent = state.user ? state.user.display_name : "";
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  setError($("#login-error"), null);
  try {
    state.user = await api.login(form.get("username"), form.get("password"));
    renderUserBar();
    event.target.reset();
    await enterChat();
  } catch (error) {
    const friendly =
      error.code === "invalid_credentials"
        ? { message: "That username or password is not right. Try again." }
        : error;
    setError($("#login-error"), friendly);
  }
});

$("#logout-btn").addEventListener("click", async () => {
  try {
    await api.logout();
  } finally {
    state.user = null;
    sessionStorage.removeItem(CURRENT_CASE_KEY);
    resetConversation();
    renderUserBar();
    showView("login");
  }
});

async function enterChat() {
  showView("chat");
  updateComposerAvailability();
  const remembered = sessionStorage.getItem(CURRENT_CASE_KEY);
  if (remembered) {
    const opened = await openCase(remembered, { silent: true });
    if (opened) return;
  }
  startNewCase();
}

// ---------- Conversation lifecycle ----------

function resetConversation() {
  stopPolling();
  state.matter = null;
  state.messages = [];
  state.documents = [];
  state.uploads.clear();
  state.readyNames.clear();
  state.pendingIntent = null;
  state.lastQuestion = null;
  transcript.replaceChildren();
  renderFileChips();
  updateEmptyState();
}

function startNewCase() {
  resetConversation();
  sessionStorage.removeItem(CURRENT_CASE_KEY);
  advisor.setState("idle");
  greet();
  closeDrawer();
  if (!window.matchMedia("(max-width: 700px)").matches) composerInput.focus();
}

function greet() {
  if (!state.llmConfigured) {
    bubble.say(MISSING_KEY, { tone: "error" });
    advisor.setState("error", { detail: "Model key missing" });
    return;
  }
  bubble.say(GREETING, { chips: [{ label: "Add your documents with +", onClick: pickFiles }] });
}

async function openCase(id, { silent = false } = {}) {
  try {
    const matter = await api.matter(id);
    const [history, docs] = await Promise.all([api.chatHistory(id), api.documents(id)]);
    resetConversation();
    state.matter = matter;
    state.messages = history.messages;
    sessionStorage.setItem(CURRENT_CASE_KEY, matter.id);
    transcript.classList.add("transcript--restoring"); // restored history appears at once, no entrance animation
    for (const message of history.messages) {
      transcript.append(
        message.role === "user" ? renderUserMessage(message.text) : renderReply(message, openPassage),
      );
    }
    window.setTimeout(() => transcript.classList.remove("transcript--restoring"), 50);
    applyDocuments(docs.documents, { announce: false });
    updateEmptyState();
    const lastReply = [...history.messages].reverse().find((m) => m.role === "assistant");
    if (!state.llmConfigured) {
      greet();
    } else if (lastReply) {
      advisor.setState(lastReply.avatar_state, avatarDetail(lastReply));
      bubble.say(lastReply.bubble, { chips: chipsFor(lastReply.chips), tone: toneFor(lastReply) });
    } else if (matter.read_only) {
      advisor.setState("idle");
      bubble.say("This is the shared reference library. Ask me a question about what it contains.");
    } else if (state.documents.some((d) => d.status === "ready")) {
      advisor.setState("idle");
      bubble.say("Your documents are here. Shall I analyse your case?", {
        chips: [{ label: "Analyse my case now", onClick: analyseNow }],
      });
    } else {
      advisor.setState("idle");
      greet();
    }
    closeDrawer();
    scrollToEnd();
    return true;
  } catch (error) {
    if (handleAuthError(error)) return false;
    if (!silent) setError($("#composer-error"), error);
    sessionStorage.removeItem(CURRENT_CASE_KEY);
    return false;
  }
}

async function ensureMatter() {
  if (state.matter) return state.matter;
  state.matter = await api.createMatter({});
  sessionStorage.setItem(CURRENT_CASE_KEY, state.matter.id);
  return state.matter;
}

function updateEmptyState() {
  views.chat.classList.toggle("chat--empty", transcript.childElementCount === 0);
}

function scrollToEnd() {
  transcript.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "end" });
}

// ---------- Sending ----------

async function send(text, intent = "auto") {
  if (state.busy || !text) return;
  setError($("#composer-error"), null);
  state.busy = true;
  updateComposerAvailability();
  transcript.append(renderUserMessage(text));
  updateEmptyState();
  scrollToEnd();
  const hasDocs = state.documents.some((d) => d.status === "ready");
  bubble.think(
    intent === "analyze" || (!state.messages.length && hasDocs)
      ? "Analysing your case against your documents. This can take a few minutes."
      : hasDocs
        ? "Reading your documents."
        : "Looking this up.",
  );
  advisor.setState("listening", { busy: true, detail: "Working" });
  try {
    const matter = await ensureMatter();
    const reply = await api.chat(matter.id, {
      message: text,
      allow_web: $("#allow-web").checked,
      intent,
      language: $("#language").value,
    });
    state.messages.push({ role: "user", text }, reply);
    state.lastQuestion = reply.mode === "question" ? text : state.lastQuestion;
    transcript.append(renderReply(reply, openPassage));
    advisor.setState(reply.avatar_state, avatarDetail(reply));
    bubble.say(reply.bubble, { chips: chipsFor(reply.chips), tone: toneFor(reply) });
    loadCases();
  } catch (error) {
    if (!handleAuthError(error)) {
      transcript.append(renderErrorMessage(error.message));
      if (error.code === "llm_unavailable") {
        bubble.say(MISSING_KEY, { tone: "error" });
        advisor.setState("error", { detail: "Model not configured or unreachable" });
      } else {
        bubble.say("Something went wrong. Please try again.", { tone: "error" });
        advisor.setState("error");
      }
    }
  } finally {
    state.busy = false;
    updateComposerAvailability();
    updateEmptyState();
    scrollToEnd();
  }
}

function toneFor(reply) {
  if (reply.mode === "research" || reply.avatar_state === "web_source") return "warn";
  if (reply.avatar_state === "unverified") return "warn";
  return undefined;
}

function avatarDetail(reply) {
  if (reply.analysis) {
    const unverified = reply.analysis.issues.filter((i) => i.unverified).length;
    if (reply.avatar_state === "unverified") return { detail: `${unverified} issue${unverified === 1 ? "" : "s"} flagged` };
    if (reply.avatar_state === "verified_source") {
      return { detail: `${reply.analysis.issues.length} issue${reply.analysis.issues.length === 1 ? "" : "s"} analysed` };
    }
    return {};
  }
  const answer = reply.answer || {};
  const n = (reply.citations || []).length;
  if (reply.avatar_state === "unverified") return { detail: `Not retrieved: ${(answer.unsupported_citations || []).join(", ")}` };
  if (reply.avatar_state === "verified_source") return { detail: `${n} citation${n === 1 ? "" : "s"} resolved` };
  if (reply.avatar_state === "web_source") return { detail: `${n} web source${n === 1 ? "" : "s"}` };
  return {};
}

// Server chips name an action; the client decides what the action does.
function chipsFor(chips) {
  return (chips || []).map((chip) => ({
    label: chip.label,
    onClick: () => {
      if (chip.action === "analyze") analyseNow();
      else if (chip.action === "add_documents") pickFiles();
      else if (chip.action === "ask_web") {
        $("#allow-web").checked = true;
        if (state.lastQuestion) send(state.lastQuestion);
        else composerInput.focus();
      } else composerInput.focus();
    },
  }));
}

function firstUserText() {
  return state.messages.find((m) => m.role === "user")?.text || null;
}

function analyseNow() {
  const typed = composerInput.value.trim();
  const facts = typed || firstUserText();
  if (!facts) {
    state.pendingIntent = "analyze";
    bubble.say("First tell me about your case below, then I'll analyse it against your documents.");
    composerInput.focus();
    return;
  }
  composerInput.value = "";
  autosize();
  send(facts, "analyze");
}

// ---------- Composer ----------

function updateComposerAvailability() {
  const canSend = state.llmConfigured && !state.busy;
  $("#composer-send").disabled = !canSend;
  composerInput.disabled = !state.llmConfigured;
  // The + button stays enabled: uploads work without a model key and while thinking.
  $("#composer-hint").textContent = state.llmConfigured
    ? "Enter to send, Shift+Enter for a new line."
    : "Answers are off until the server has a Claude API key. You can still add documents.";
}

function autosize() {
  composerInput.style.height = "auto";
  composerInput.style.height = `${Math.min(composerInput.scrollHeight, 180)}px`;
}

composerInput.addEventListener("input", autosize);
// A one-line placeholder on narrow screens; the aria-label keeps the full wording.
if (window.matchMedia("(max-width: 480px)").matches) composerInput.placeholder = "Your case or a question";
composerInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    composer.requestSubmit();
  }
});
composerInput.addEventListener("focus", () => {
  if (["idle", "no_results"].includes(advisor.state) && !state.busy) {
    advisor.setState("listening");
  }
});
composerInput.addEventListener("blur", () => {
  if (advisor.state === "listening" && !state.busy && !composerInput.value.trim()) advisor.setState("idle");
});

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = composerInput.value.trim();
  if (!text) return;
  const intent = state.pendingIntent || "auto";
  state.pendingIntent = null;
  composerInput.value = "";
  autosize();
  send(text, intent);
});

// ---------- Documents ----------

function pickFiles() {
  if (state.matter?.read_only) {
    bubble.say("This shared library is read-only. Start a new case to add your own documents.", {
      chips: [{ label: "Start a new case", onClick: startNewCase }],
    });
    return;
  }
  $("#file-input").click();
}

$("#add-files").addEventListener("click", pickFiles);
$("#file-input").addEventListener("change", async (event) => {
  const files = [...event.target.files];
  event.target.value = "";
  if (!files.length) return;
  setError($("#composer-error"), null);
  try {
    await ensureMatter();
  } catch (error) {
    handleAuthError(error) || setError($("#composer-error"), error);
    return;
  }
  bubble.think(`Adding ${files.length === 1 ? "your document" : `${files.length} documents`}.`);
  advisor.setState("listening", { busy: true, detail: "Receiving documents" });
  await Promise.all(files.map(uploadOne));
  await loadDocuments();
});

async function uploadOne(file) {
  const name = file.name;
  state.uploads.set(name, { status: "uploading" });
  renderFileChips();
  try {
    await api.upload(state.matter.id, file, null);
    state.uploads.delete(name);
  } catch (error) {
    if (handleAuthError(error)) return;
    state.uploads.set(name, { status: "failed", error: error.message });
  }
  renderFileChips();
}

async function loadDocuments() {
  if (!state.matter) return;
  try {
    const { documents } = await api.documents(state.matter.id);
    applyDocuments(documents, { announce: true });
  } catch (error) {
    handleAuthError(error);
  }
}

function applyDocuments(documents, { announce }) {
  const before = new Set(state.readyNames);
  state.documents = documents;
  for (const doc of documents) if (doc.status === "ready") state.readyNames.add(doc.name);
  renderFileChips();
  const busy = documents.some((d) => d.status === "queued" || d.status === "processing");
  if (busy) {
    schedulePoll();
    return;
  }
  stopPolling();
  if (!announce) return;
  const newlyReady = documents.filter((d) => d.status === "ready" && !before.has(d.name));
  const failed = documents.filter((d) => d.status === "failed");
  if (newlyReady.length && state.llmConfigured) {
    advisor.setState("idle");
    bubble.say(
      `${newlyReady.length === 1 ? "Your document is" : `${newlyReady.length} documents are`} ready.` +
        (failed.length ? ` ${failed.length} could not be read.` : "") +
        " Shall I analyse your case now?",
      { chips: [{ label: "Analyse my case now", onClick: analyseNow }] },
    );
  } else if (newlyReady.length) {
    advisor.setState("idle");
    bubble.say(`${newlyReady.length === 1 ? "Your document is" : "Your documents are"} ready. ${MISSING_KEY}`, {
      tone: "error",
    });
  } else if (failed.length) {
    advisor.setState("no_results");
    bubble.say("I could not read that document. Try a PDF or DOCX with selectable text.", { tone: "warn" });
  }
  loadCases();
}

function schedulePoll() {
  stopPolling();
  state.pollTimer = window.setTimeout(loadDocuments, 1500);
}

function stopPolling() {
  if (state.pollTimer) window.clearTimeout(state.pollTimer);
  state.pollTimer = null;
}

const STATUS_LABEL = {
  uploading: "uploading",
  queued: "queued",
  processing: "processing",
  ready: "ready",
  failed: "failed",
};

function renderFileChips() {
  const rows = new Map();
  for (const doc of state.documents) rows.set(doc.name, { name: doc.name, status: doc.status, error: doc.error });
  for (const [name, upload] of state.uploads) if (!rows.has(name)) rows.set(name, { name, ...upload });
  const list = $("#file-chips");
  list.replaceChildren(
    ...[...rows.values()].map((row) =>
      el(
        "span",
        {
          class: `file-chip file-chip--${row.status}`,
          title: row.error || `${row.name}: ${STATUS_LABEL[row.status] || row.status}`,
        },
        [
          el("span", { class: "file-chip__icon", "aria-hidden": "true", text: "📄" }),
          el("span", { class: "file-chip__name", text: row.name }),
          el("span", { class: `file-chip__status`, text: STATUS_LABEL[row.status] || row.status }),
          row.status === "ready" || row.status === "failed"
            ? el("button", {
                type: "button",
                class: "file-chip__remove",
                "aria-label": `Remove ${row.name}`,
                title: "Remove",
                onclick: () => removeDocument(row.name),
              }, "✕")
            : null,
        ],
      ),
    ),
  );
  list.hidden = rows.size === 0;
}

async function removeDocument(name) {
  if (state.uploads.has(name) && !state.documents.some((d) => d.name === name)) {
    state.uploads.delete(name);
    renderFileChips();
    return;
  }
  try {
    await api.removeDocument(state.matter.id, name);
    state.readyNames.delete(name);
    await loadDocuments();
  } catch (error) {
    handleAuthError(error) || setError($("#composer-error"), error);
  }
}

// ---------- "Your cases" drawer ----------

const drawer = $("#drawer");
const scrim = $("#scrim");

function openDrawer() {
  drawer.hidden = false;
  scrim.hidden = false;
  $("#drawer-toggle").setAttribute("aria-expanded", "true");
  loadCases();
  $("#new-case").focus();
}

function closeDrawer() {
  if (drawer.hidden) return;
  drawer.hidden = true;
  scrim.hidden = true;
  $("#drawer-toggle").setAttribute("aria-expanded", "false");
}

$("#drawer-toggle").addEventListener("click", () => (drawer.hidden ? openDrawer() : closeDrawer()));
$("#drawer-close").addEventListener("click", closeDrawer);
scrim.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !drawer.hidden) {
    closeDrawer();
    $("#drawer-toggle").focus();
  }
});
$("#new-case").addEventListener("click", startNewCase);

async function loadCases() {
  if (!state.user) return;
  const list = $("#case-list");
  try {
    const { matters } = await api.matters();
    list.replaceChildren(
      ...matters.map((m) => {
        const current = state.matter && state.matter.id === m.id;
        return el("li", {}, [
          el(
            "button",
            {
              type: "button",
              class: `case${current ? " is-current" : ""}${m.read_only ? " case--shared" : ""}`,
              "aria-current": current ? "true" : null,
              onclick: () => openCase(m.id),
            },
            [
              el("span", { class: "case__title", text: m.title || "Untitled case" }),
              el("span", {
                class: "case__meta",
                text: `${m.document_count} document${m.document_count === 1 ? "" : "s"}${
                  m.read_only ? " · shared, read-only" : ""
                }`,
              }),
            ],
          ),
        ]);
      }),
    );
  } catch (error) {
    if (!handleAuthError(error)) list.replaceChildren(el("li", { class: "form-error", text: error.message }));
  }
}

// ---------- Avatar controls ----------

$("#toggle-avatar").addEventListener("click", (event) => {
  const hidden = advisor.toggleHidden();
  event.currentTarget.textContent = hidden ? "Show character" : "Hide character";
  event.currentTarget.setAttribute("aria-pressed", String(hidden));
});

bootstrap();
