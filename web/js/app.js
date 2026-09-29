import { api, ApiError } from "./api.js";
import { createAvatar } from "./avatar.js";
import {
  citationChip,
  createPassageDialog,
  el,
  renderAnswerText,
  renderCitationList,
  renderPassages,
  unsupportedChip,
} from "./citations.js";

const $ = (selector) => document.querySelector(selector);

const state = {
  user: null,
  llmConfigured: true,
  matter: null,
  mode: "ask",
  pollTimer: null,
};

const views = {
  login: $("#view-login"),
  matters: $("#view-matters"),
  matter: $("#view-matter"),
};
const advisor = createAvatar($("#advisor"));
const openPassage = createPassageDialog($("#passage-dialog"));

function showView(name) {
  for (const [key, node] of Object.entries(views)) node.hidden = key !== name;
}

function setError(node, error) {
  node.textContent = error ? error.message || String(error) : "";
  node.hidden = !error;
}

// ---------- Session ----------

async function bootstrap() {
  try {
    const health = await api.health();
    state.llmConfigured = Boolean(health.llm_configured);
    $("#llm-banner").hidden = state.llmConfigured;
  } catch {
    state.llmConfigured = false;
  }
  try {
    state.user = await api.me();
  } catch {
    state.user = null;
  }
  renderUserBar();
  route();
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
    location.hash = "#/";
    route();
  } catch (error) {
    setError($("#login-error"), error);
  }
});

$("#logout-btn").addEventListener("click", async () => {
  try {
    await api.logout();
  } finally {
    state.user = null;
    state.matter = null;
    renderUserBar();
    location.hash = "#/";
    route();
  }
});

// ---------- Routing ----------

window.addEventListener("hashchange", route);

function route() {
  stopPolling();
  if (!state.user) {
    showView("login");
    return;
  }
  const match = location.hash.match(/^#\/matters\/([a-z0-9][a-z0-9-]{1,63})$/);
  if (match) {
    openMatter(match[1]);
  } else {
    showView("matters");
    loadMatters();
  }
}

// ---------- Matter list ----------

async function loadMatters() {
  const list = $("#matter-list");
  list.replaceChildren(el("li", { class: "muted", text: "Loading…" }));
  try {
    const { matters } = await api.matters();
    list.replaceChildren(
      ...matters.map((m) =>
        el("li", {}, [
          el("div", {}, [
            el("a", { class: "link", href: `#/matters/${m.id}`, text: m.id }),
            el("div", { class: "meta", text: `${m.document_count} document${m.document_count === 1 ? "" : "s"}${m.read_only ? " · shared, read-only" : ""}` }),
          ]),
          el("a", { class: "btn btn--tiny", href: `#/matters/${m.id}`, text: "Open" }),
        ]),
      ),
    );
  } catch (error) {
    handleAuthError(error);
    list.replaceChildren(el("li", { class: "form-error", text: error.message }));
  }
}

$("#create-matter-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = new FormData(event.target).get("id").trim();
  setError($("#create-matter-error"), null);
  try {
    const matter = await api.createMatter(id);
    event.target.reset();
    location.hash = `#/matters/${matter.id}`;
  } catch (error) {
    handleAuthError(error);
    setError($("#create-matter-error"), error);
  }
});

function handleAuthError(error) {
  if (error instanceof ApiError && error.status === 401) {
    state.user = null;
    renderUserBar();
    showView("login");
  }
}

// ---------- Matter view ----------

async function openMatter(id) {
  try {
    const matter = await api.matter(id);
    if (!state.matter || state.matter.id !== matter.id) {
      $("#transcript").replaceChildren();
      advisor.setState("idle");
    }
    state.matter = matter;
  } catch (error) {
    handleAuthError(error);
    if (error.status === 404) location.hash = "#/";
    return;
  }
  showView("matter");
  $("#matter-title").textContent = state.matter.id;
  $("#matter-readonly-note").hidden = !state.matter.read_only;
  $("#upload-form").hidden = state.matter.read_only;
  updateComposerAvailability();
  await loadDocuments();
}

function updateComposerAvailability() {
  const disabled = !state.llmConfigured;
  $("#composer-submit").disabled = disabled;
  $("#composer-input").disabled = disabled;
  $("#composer-hint").textContent = disabled
    ? "Asking is disabled until the server has a model key."
    : state.mode === "ask"
      ? "Answers cite only passages retrieved from this matter."
      : "Several checked reasoning steps per issue: this can take a few minutes.";
}

async function loadDocuments() {
  if (!state.matter) return;
  const list = $("#doc-list");
  try {
    const { documents } = await api.documents(state.matter.id);
    list.replaceChildren(
      ...(documents.length
        ? documents.map(renderDocumentRow)
        : [el("li", { class: "muted", text: "No documents yet." })]),
    );
    const busy = documents.some((d) => d.status === "queued" || d.status === "processing");
    if (busy) schedulePoll();
    else stopPolling();
  } catch (error) {
    handleAuthError(error);
    list.replaceChildren(el("li", { class: "form-error", text: error.message }));
  }
}

function renderDocumentRow(doc) {
  const removable = !state.matter.read_only && doc.status !== "processing" && doc.status !== "queued";
  return el("li", {}, [
    el("div", { class: "name" }, [
      doc.name,
      doc.error ? el("div", { class: "error", text: doc.error }) : null,
    ]),
    el("span", { class: `status status--${doc.status}`, text: doc.status }),
    removable
      ? el("button", {
          type: "button",
          class: "btn btn--danger btn--tiny",
          text: "Remove",
          onclick: async () => {
            try {
              await api.removeDocument(state.matter.id, doc.name);
              await loadDocuments();
            } catch (error) {
              setError($("#upload-error"), error);
            }
          },
        })
      : null,
  ]);
}

function schedulePoll() {
  stopPolling();
  state.pollTimer = window.setTimeout(loadDocuments, 2000);
}

function stopPolling() {
  if (state.pollTimer) window.clearTimeout(state.pollTimer);
  state.pollTimer = null;
}

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const file = form.get("file");
  setError($("#upload-error"), null);
  if (!file || !file.size) return;
  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    await api.upload(state.matter.id, file, form.get("jurisdiction") || null);
    event.target.reset();
    await loadDocuments();
  } catch (error) {
    handleAuthError(error);
    setError($("#upload-error"), error);
  } finally {
    button.disabled = false;
  }
});

// ---------- Mode tabs ----------

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => setMode(tab.dataset.mode));
}

function setMode(mode) {
  state.mode = mode;
  for (const tab of document.querySelectorAll(".tab")) {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  $("#composer-label").textContent =
    mode === "ask"
      ? "Ask a question about the documents in this matter"
      : "Describe the facts of the case to analyze";
  $("#composer-submit").textContent = mode === "ask" ? "Ask" : "Analyze case";
  $("#allow-web-wrap").hidden = mode !== "ask";
  updateComposerAvailability();
}

// ---------- Ask / analyze ----------

const composerInput = $("#composer-input");
composerInput.addEventListener("focus", () => {
  if (["idle", "no_results"].includes(advisor.state)) advisor.setState("listening");
});
composerInput.addEventListener("blur", () => {
  if (advisor.state === "listening" && !composerInput.value.trim()) advisor.setState("idle");
});

$("#ask-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = composerInput.value.trim();
  if (!text || !state.matter) return;
  const transcript = $("#transcript");
  transcript.append(el("article", { class: "msg msg--user", text }));
  composerInput.value = "";
  const submit = $("#composer-submit");
  submit.disabled = true;
  advisor.setState("listening", {
    busy: true,
    detail: state.mode === "ask" ? "Reading your documents" : "Analyzing each issue against the documents",
  });
  const common = { jurisdiction: $("#jurisdiction").value || null, language: $("#language").value };
  try {
    if (state.mode === "ask") {
      const result = await api.ask(state.matter.id, {
        question: text,
        allow_web: $("#allow-web").checked,
        ...common,
      });
      transcript.append(renderAnswer(result));
      advisor.setState(result.avatar_state, avatarDetailForAnswer(result));
    } else {
      const result = await api.analyze(state.matter.id, { case_facts: text, ...common });
      transcript.append(renderAnalysis(result));
      advisor.setState(result.avatar_state, avatarDetailForAnalysis(result));
    }
  } catch (error) {
    handleAuthError(error);
    transcript.append(el("article", { class: "msg msg--assistant msg--error", text: error.message }));
    advisor.setState("error", { detail: error.code === "llm_unavailable" ? "Model not configured or unreachable" : "" });
  } finally {
    submit.disabled = !state.llmConfigured;
    transcript.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "end" });
  }
});

function avatarDetailForAnswer(result) {
  if (result.avatar_state === "unverified") {
    return { detail: `Not retrieved: ${result.unsupported_citations.join(", ")}` };
  }
  if (result.avatar_state === "verified_source") {
    return { detail: `${result.citations.length} citation${result.citations.length === 1 ? "" : "s"} resolved` };
  }
  if (result.avatar_state === "web_source") {
    return { detail: `${result.citations.length} web source${result.citations.length === 1 ? "" : "s"}` };
  }
  return {};
}

function avatarDetailForAnalysis(result) {
  const unverified = result.issues.filter((i) => i.unverified).length;
  if (result.avatar_state === "unverified") return { detail: `${unverified} issue${unverified === 1 ? "" : "s"} flagged` };
  if (result.avatar_state === "verified_source") return { detail: `${result.issues.length} issue${result.issues.length === 1 ? "" : "s"} analyzed` };
  return {};
}

function renderAnswer(result) {
  const card = el("article", { class: `msg msg--assistant msg--${result.source}${result.faithful ? "" : " msg--unverified"}` });
  const label =
    result.source === "web"
      ? "⚠ From an official website, not verified against this matter's documents"
      : result.source === "none"
        ? "Nothing relevant found"
        : "From your documents";
  card.append(el("div", { class: "msg__label", text: label }));
  card.append(renderAnswerText(result.answer, result.citations, result.unsupported_citations, openPassage));
  if (!result.faithful && result.source === "corpus") {
    card.append(
      el("div", { class: "msg__warning" }, [
        "Citation check failed. These locators were not among the retrieved passages: ",
        ...result.unsupported_citations.flatMap((loc, i) => [i ? " " : null, unsupportedChip(loc)]),
        ". Treat this answer with caution.",
      ]),
    );
  }
  if (result.source === "web") {
    card.append(el("div", { class: "msg__info", text: "Web material is labelled as legal authority. It cannot replace missing case evidence." }));
  }
  const cited = renderCitationList(result.source === "web" ? "Web sources" : "Sources cited", result.citations, openPassage);
  if (cited) card.append(cited);
  const passages = renderPassages(result.retrieved, openPassage);
  if (passages) card.append(passages);
  const usage = result.usage || {};
  if (usage.input_tokens || usage.output_tokens) {
    card.append(el("div", { class: "msg__meta", text: `Tokens: ${usage.input_tokens || 0} in / ${usage.output_tokens || 0} out` }));
  }
  return card;
}

function confidenceBadge(issue) {
  const cls = issue.unverified
    ? "badge--conf-unverified"
    : issue.confidence.startsWith("strongly")
      ? "badge--conf"
      : issue.confidence.startsWith("no authority")
        ? "badge--conf-none"
        : "badge--conf-weak";
  return el("span", { class: `badge ${cls}`, text: issue.unverified ? "unverified" : issue.confidence });
}

function renderAnalysis(result) {
  const card = el("article", { class: `msg msg--assistant${result.avatar_state === "unverified" ? " msg--unverified" : ""}` });
  card.append(el("div", { class: "msg__label", text: "Case analysis" }));
  for (const issue of result.issues) {
    const block = el("section", { class: "issue" }, [
      el("div", { class: "issue__title" }, [issue.issue, confidenceBadge(issue)]),
    ]);
    for (const argument of issue.arguments) {
      const cites = el("div", { class: "argument__cites" });
      for (const authority of argument.authorities) {
        cites.append(
          authority.source
            ? citationChip(authority.source, openPassage, authority.locator)
            : unsupportedChip(authority.locator),
        );
      }
      if (!argument.authorities.length) cites.append(el("span", { class: "muted small", text: "no authority cited" }));
      block.append(
        el("div", { class: "argument" }, [
          el("span", { class: "argument__side", text: `${argument.side}: ` }),
          argument.summary,
          cites,
        ]),
      );
    }
    block.append(el("p", { class: "msg__body" }, [el("em", { text: "Assessment: " }), issue.assessment]));
    card.append(block);
  }
  card.append(el("section", { class: "issue" }, [el("h3", { text: "Overall position" }), el("p", { class: "msg__body", text: result.overall_position })]));
  if (result.missing_evidence.length) {
    card.append(
      el("section", { class: "issue" }, [
        el("h3", { text: "Missing support" }),
        el("ul", { class: "gap-list" }, result.missing_evidence.map((gap) => el("li", {}, [el("strong", { text: gap.issue }), ` — ${gap.note}`]))),
      ]),
    );
  }
  const cited = renderCitationList("Authorities resolved", result.citations, openPassage);
  if (cited) card.append(cited);
  card.append(el("div", { class: "disclaimer", text: result.disclaimer }));
  return card;
}

// ---------- Avatar controls ----------

$("#toggle-avatar").addEventListener("click", (event) => {
  const hidden = advisor.toggleHidden();
  event.currentTarget.textContent = hidden ? "Show character" : "Hide character";
  event.currentTarget.setAttribute("aria-pressed", String(hidden));
});

setMode("ask");
bootstrap();
