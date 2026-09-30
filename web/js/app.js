// Chat-first client (revision 2, live bubble + bring-your-own key in
// revision 3). One screen: the advisor character in the middle with a speech
// bubble, the conversation around it, and a composer at the bottom with a +
// button for documents. Cases (matters) are created behind the scenes; the
// user only ever sees titles. All trust decisions (evidence vs authority vs
// web, avatar state, research-mode labelling) come from the server and are
// rendered as received. The advisor thinks with the user's own Claude key;
// until one is on file (or the server shares its key) the composer is off
// and the bubble explains how to activate it.

import { api, ApiError } from "./api.js";
import { createActivateDialog } from "./activate.js";
import { createAvatar } from "./avatar.js";
import { createBubble } from "./bubble.js";
import { createPassageDialog, el } from "./citations.js";
import { renderErrorMessage, renderReply, renderUserMessage } from "./messages.js";

const $ = (selector) => document.querySelector(selector);

const GREETING = "Hi, I'm your advisor. Tell me about your case, or add your documents with +";
const GREETING_SHARED =
  "Hi, I'm your advisor, thinking with this server's shared Claude key for now. Tell me about your " +
  "case, or add your documents with +";
const FRESH_CASE = "A fresh case. Tell me what happened, or add documents with +";
const KEY_REQUIRED = "I need your Claude key before I can start thinking.";
const ACTIVATED = "Thank you, I'm ready. Tell me about your case, or add your documents with +";
const STAGE_TEXT = {
  reading_documents: "Reading your documents…",
  searching_web: "Checking official websites…",
  checking_citations: "Checking my citations against the sources…",
  writing: "Writing it up…",
};
const JOB_POLL_MS = 700;
const CURRENT_CASE_KEY = "pa.currentCase";

const state = {
  user: null,
  advisorReady: false,
  keySource: null, // "user" | "shared" | null
  keyInfo: null, // last GET /api/me/claude-key
  matter: null,
  messages: [],
  documents: [],
  uploads: new Map(), // name -> { status: "uploading" | "failed", error }
  readyNames: new Set(),
  announcedFailures: new Set(),
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
const activate = createActivateDialog($("#activate-dialog"), { onActivated: onKeyActivated });

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

function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

// ---------- Session ----------

function applyUser(user) {
  state.user = user;
  state.advisorReady = Boolean(user && user.advisor_ready);
  state.keySource = user ? user.key_source || null : null;
}

async function bootstrap() {
  try {
    const session = await api.session();
    applyUser(session.user);
  } catch {
    applyUser(null);
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
    applyUser(await api.login(form.get("username"), form.get("password")));
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
    applyUser(null);
    state.keyInfo = null;
    sessionStorage.removeItem(CURRENT_CASE_KEY);
    resetConversation();
    renderUserBar();
    showView("login");
  }
});

async function enterChat() {
  showView("chat");
  updateComposerAvailability();
  loadKeyInfo();
  const remembered = sessionStorage.getItem(CURRENT_CASE_KEY);
  if (remembered) {
    const opened = await openCase(remembered, { silent: true });
    if (opened) return;
  }
  startNewCase({ fresh: false });
}

// ---------- Claude key (bring your own) ----------

async function loadKeyInfo() {
  if (!state.user) return;
  try {
    state.keyInfo = await api.claudeKey();
    state.advisorReady = Boolean(state.keyInfo.advisor_ready);
    state.keySource = state.keyInfo.source || null;
  } catch (error) {
    if (handleAuthError(error)) return;
    state.keyInfo = null;
  }
  renderKeySection();
  updateComposerAvailability();
}

function openActivate(options) {
  activate.open(options);
}

function onKeyActivated(info) {
  state.keyInfo = info;
  state.advisorReady = Boolean(info.advisor_ready);
  state.keySource = info.source || null;
  renderKeySection();
  updateComposerAvailability();
  advisor.setState("idle");
  advisor.bounce();
  bubble.say(ACTIVATED, { chips: [{ label: "Add your documents with +", onClick: pickFiles }] });
  if (!window.matchMedia("(max-width: 700px)").matches) composerInput.focus();
}

async function removeKey() {
  const ok = window.confirm(
    "Remove your Claude key from this server? The advisor will stop thinking for you until you add a key again. Your cases and documents stay.",
  );
  if (!ok) return;
  try {
    await api.removeClaudeKey();
  } catch (error) {
    if (!handleAuthError(error)) renderKeySection(error);
    return;
  }
  await loadKeyInfo();
  if (!state.advisorReady) {
    closeDrawer();
    sayKeyRequired();
  } else {
    bubble.say("Your key is removed. I'm using this server's shared key again.", {
      chips: [{ label: "Use my own key", onClick: () => openActivate() }],
    });
  }
}

function renderKeySection(error) {
  const status = $("#key-status");
  const actions = $("#key-actions");
  const info = state.keyInfo;
  status.classList.remove("key-section__status--none");
  if (!info) {
    status.textContent = "Checking…";
    actions.replaceChildren();
    return;
  }
  const button = (label, onclick, cls = "btn btn--tiny") => el("button", { type: "button", class: cls, onclick }, label);
  if (info.configured) {
    status.textContent = `Key on file, ends in …${info.last4}`;
    actions.replaceChildren(
      button("Replace", () => openActivate({ replacing: true })),
      button("Remove", removeKey, "btn btn--ghost btn--tiny"),
    );
  } else if (info.source === "shared") {
    status.textContent = "No key of your own. Using this server's shared key.";
    actions.replaceChildren(button("Add my own key", () => openActivate()));
  } else {
    status.textContent = "No key. The advisor cannot think for you yet.";
    status.classList.add("key-section__status--none");
    actions.replaceChildren(button("Activate", () => openActivate(), "btn btn--primary btn--tiny"));
  }
  const existing = $("#key-section .key-section__error");
  if (existing) existing.remove();
  if (error) {
    $("#key-section").append(el("p", { class: "form-error key-section__error", role: "alert", text: error.message }));
  }
}

function sayKeyRequired() {
  advisor.setState("idle", { caption: "Waiting for your key" });
  bubble.say(KEY_REQUIRED, {
    tone: "warn",
    chips: [{ label: "Activate the advisor", onClick: () => openActivate() }],
  });
}

// ---------- Conversation lifecycle ----------

function resetConversation() {
  stopPolling();
  state.matter = null;
  state.messages = [];
  state.documents = [];
  state.uploads.clear();
  state.readyNames.clear();
  state.announcedFailures.clear();
  state.pendingIntent = null;
  state.lastQuestion = null;
  transcript.replaceChildren();
  renderFileChips();
  updateEmptyState();
}

function startNewCase({ fresh = true } = {}) {
  resetConversation();
  sessionStorage.removeItem(CURRENT_CASE_KEY);
  advisor.setState("idle");
  greet({ fresh });
  closeDrawer();
  if (state.advisorReady && !window.matchMedia("(max-width: 700px)").matches) composerInput.focus();
}

function greet({ fresh = false } = {}) {
  if (!state.advisorReady) {
    sayKeyRequired();
    return;
  }
  const chips = [{ label: "Add your documents with +", onClick: pickFiles }];
  if (state.keySource === "shared") chips.push({ label: "Use my own key", onClick: () => openActivate() });
  if (fresh) {
    bubble.say(FRESH_CASE, { chips });
  } else {
    bubble.say(state.keySource === "shared" ? GREETING_SHARED : GREETING, { chips });
  }
}

function markRestored(node) {
  node.classList.add(node.classList.contains("reply") ? "reply--restored" : "msg--restored");
  return node;
}

async function openCase(id, { silent = false } = {}) {
  try {
    const matter = await api.matter(id);
    const [history, docs] = await Promise.all([api.chatHistory(id), api.documents(id)]);
    resetConversation();
    state.matter = matter;
    state.messages = history.messages;
    sessionStorage.setItem(CURRENT_CASE_KEY, matter.id);
    // Restored history appears at once: the class stays on each node, so
    // the entrance animation never starts for them.
    for (const message of history.messages) {
      transcript.append(
        markRestored(message.role === "user" ? renderUserMessage(message.text) : renderReply(message, openPassage)),
      );
    }
    applyDocuments(docs.documents, { announce: false });
    updateEmptyState();
    const lastReply = [...history.messages].reverse().find((m) => m.role === "assistant");
    if (!state.advisorReady) {
      sayKeyRequired();
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
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  transcript.lastElementChild?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "end" });
}

// ---------- Sending ----------

const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

// Start the job, then poll it; the bubble follows the stage the server
// reports. Resolves with the reply, rejects with an ApiError-like object.
async function chatWithProgress(matterId, body) {
  const job = await api.startChatJob(matterId, body);
  let lastStage = null;
  for (;;) {
    await sleep(JOB_POLL_MS);
    const status = await api.chatJob(matterId, job.job_id);
    if (status.stage && status.stage !== lastStage && status.status === "running") {
      lastStage = status.stage;
      bubble.think(STAGE_TEXT[status.stage] || "Working on it…", { pop: false });
    }
    if (status.status === "done") return status.reply;
    if (status.status === "failed") {
      const error = status.error || {};
      throw new ApiError(0, error.code || "error", error.message || "Something went wrong.");
    }
  }
}

async function send(text, intent = "auto") {
  if (state.busy || !text) return;
  if (!state.advisorReady) {
    sayKeyRequired();
    return;
  }
  setError($("#composer-error"), null);
  state.busy = true;
  updateComposerAvailability();
  transcript.append(renderUserMessage(text));
  updateEmptyState();
  scrollToEnd();
  bubble.think("Let me think about that…");
  advisor.setState("listening", { busy: true, detail: "Working" });
  try {
    const matter = await ensureMatter();
    const reply = await chatWithProgress(matter.id, {
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
      if (error.code === "claude_key_required") {
        state.advisorReady = false;
        loadKeyInfo();
        sayKeyRequired();
      } else if (error.code === "llm_unavailable") {
        bubble.say("Claude did not answer just now. Please try again in a moment.", { tone: "error" });
        advisor.setState("error", { detail: "Model not reachable" });
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
    if (reply.avatar_state === "unverified") return { detail: `${plural(unverified, "issue")} flagged` };
    if (reply.avatar_state === "verified_source") {
      return { detail: `${plural(reply.analysis.issues.length, "issue")} analysed` };
    }
    return {};
  }
  const answer = reply.answer || {};
  const n = (reply.citations || []).length;
  if (reply.avatar_state === "unverified") return { detail: `Not retrieved: ${(answer.unsupported_citations || []).join(", ")}` };
  if (reply.avatar_state === "verified_source") return { detail: `${plural(n, "citation")} resolved` };
  if (reply.avatar_state === "web_source") return { detail: `${plural(n, "web source")}` };
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

const DEFAULT_PLACEHOLDER = composerInput.placeholder;

function updateComposerAvailability() {
  const ready = state.advisorReady;
  $("#composer-send").disabled = !ready || state.busy;
  composerInput.disabled = !ready;
  composerInput.placeholder = ready
    ? window.matchMedia("(max-width: 480px)").matches
      ? "Your case or a question"
      : DEFAULT_PLACEHOLDER
    : "Activate the advisor to start";
  // The + button stays enabled: uploads work without a key and while thinking.
  $("#composer-hint").textContent = ready
    ? "Enter to send, Shift+Enter for a new line."
    : "Add your Claude key to start. You can already add documents.";
}

function autosize() {
  composerInput.style.height = "auto";
  composerInput.style.height = `${Math.min(composerInput.scrollHeight, 180)}px`;
}

composerInput.addEventListener("input", autosize);
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

$("#allow-web").addEventListener("change", (event) => {
  if (state.busy) return; // do not talk over the stage narration
  if (event.target.checked) {
    bubble.say("I'll also check official websites, and label anything from them.");
  } else {
    bubble.say("I'll stick to your documents.");
  }
});

// ---------- Documents ----------

function pickFiles() {
  if (state.matter?.read_only) {
    bubble.say("This shared library is read-only. Start a new case to add your own documents.", {
      chips: [{ label: "Start a new case", onClick: () => startNewCase() }],
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
  bubble.think(`Got it, I'll read ${files.length === 1 ? files[0].name : plural(files.length, "document")}`);
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
    if (!state.announcedFailures.has(name)) {
      state.announcedFailures.add(name);
      bubble.say(`I couldn't take ${name}: ${error.message}`, { tone: "warn" });
    }
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

// One sentence per event: a document read, a document that failed, and, when
// nothing is left processing, the offer to analyse.
function applyDocuments(documents, { announce }) {
  const before = new Set(state.readyNames);
  state.documents = documents;
  for (const doc of documents) if (doc.status === "ready") state.readyNames.add(doc.name);
  renderFileChips();
  const busy = documents.some((d) => d.status === "queued" || d.status === "processing");
  const newlyReady = documents.filter((d) => d.status === "ready" && !before.has(d.name));
  const newlyFailed = documents.filter((d) => d.status === "failed" && !state.announcedFailures.has(d.name));
  for (const doc of newlyFailed) state.announcedFailures.add(doc.name);

  if (announce && !state.busy) {
    if (newlyFailed.length) {
      advisor.setState("no_results", { detail: "Could not read a document" });
      const reason = newlyFailed[0].error || "I could not read it. Try a PDF or DOCX with selectable text.";
      bubble.say(
        newlyFailed.length === 1
          ? `I couldn't read ${newlyFailed[0].name}. ${reason}`
          : `I couldn't read ${plural(newlyFailed.length, "document")}. ${reason}`,
        { tone: "warn" },
      );
    } else if (newlyReady.length) {
      const read = newlyReady.length === 1 ? `I've read ${newlyReady[0].name}` : `I've read ${plural(newlyReady.length, "document")}`;
      if (busy) {
        bubble.think(`${read}. Still reading the rest…`, { pop: false });
      } else if (state.advisorReady) {
        advisor.setState("idle");
        bubble.say(`${read}. Shall I analyse your case now?`, {
          chips: [{ label: "Analyse my case now", onClick: analyseNow }],
        });
      } else {
        advisor.setState("idle", { caption: "Waiting for your key" });
        bubble.say(`${read}. ${KEY_REQUIRED}`, {
          tone: "warn",
          chips: [{ label: "Activate the advisor", onClick: () => openActivate() }],
        });
      }
    }
  }
  if (busy) {
    schedulePoll();
    return;
  }
  stopPolling();
  if (announce && (newlyReady.length || newlyFailed.length)) loadCases();
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
    state.announcedFailures.delete(name);
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
  loadKeyInfo();
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
$("#new-case").addEventListener("click", () => startNewCase());

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
                text: `${plural(m.document_count, "document")}${m.read_only ? " · shared, read-only" : ""}`,
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
