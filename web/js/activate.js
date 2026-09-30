// "Activate the advisor": the bring-your-own Claude key dialog. A native
// <dialog> opened with showModal() gives the focus trap, Escape-to-close and
// backdrop for free; aria-labelledby points at the heading. The key is sent
// once to PUT /api/me/claude-key and never displayed again; the server
// answers with `last4` only. Server messages (invalid key, unreachable, rate
// limited, server not configured) are shown inline, verbatim: they are
// written by us on the server and never contain the key.

import { api, ApiError } from "./api.js";

export function createActivateDialog(dialog, { onActivated }) {
  const form = dialog.querySelector("form");
  const input = dialog.querySelector("#activate-key");
  const reveal = dialog.querySelector("#activate-reveal");
  const error = dialog.querySelector("#activate-error");
  const submit = dialog.querySelector("#activate-submit");
  const title = dialog.querySelector("#activate-title");
  const lead = dialog.querySelector("#activate-lead");
  let busy = false;

  function setError(message) {
    error.textContent = message || "";
    error.hidden = !message;
  }

  function setBusy(value) {
    busy = value;
    submit.disabled = value;
    input.disabled = value;
    submit.textContent = value ? "Checking your key…" : "Save and activate";
  }

  function reset() {
    form.reset();
    input.type = "password";
    reveal.textContent = "Show";
    reveal.setAttribute("aria-pressed", "false");
    setError(null);
    setBusy(false);
  }

  function open({ replacing = false } = {}) {
    reset();
    title.textContent = replacing ? "Replace your Claude key" : "Activate the advisor";
    lead.textContent = replacing
      ? "Paste a new key below. It replaces the one on file as soon as Anthropic accepts it."
      : "The advisor thinks with Claude, and you bring your own key so nobody else pays for your questions. It takes about two minutes.";
    if (!dialog.open) dialog.showModal();
    input.focus();
  }

  function close() {
    if (dialog.open) dialog.close();
  }

  reveal.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    reveal.textContent = show ? "Hide" : "Show";
    reveal.setAttribute("aria-pressed", String(show));
    input.focus();
  });

  dialog.addEventListener("cancel", (event) => {
    if (busy) event.preventDefault(); // do not lose the in-flight check
  });
  dialog.querySelector("#activate-cancel").addEventListener("click", close);
  dialog.addEventListener("close", reset);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const apiKey = input.value.trim();
    if (!apiKey) {
      setError("Paste your key first. It starts with sk-ant-.");
      input.focus();
      return;
    }
    if (!apiKey.startsWith("sk-ant-")) {
      setError("That does not look like an Anthropic API key: it should start with sk-ant-.");
      input.focus();
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const info = await api.saveClaudeKey(apiKey);
      input.value = ""; // the key is now only on the server
      close();
      onActivated(info);
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError && err.status === 401) {
        setError("Your session has expired. Log in again, then come back here.");
      } else {
        setError(err.message || "Something went wrong. Please try again.");
      }
      input.focus();
    }
  });

  return { open, close };
}
