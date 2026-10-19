// Rendering of source references (docs/contracts/web-api.md). Citations are
// shown exactly as the backend returned them: the quote is verbatim, the
// kind label (evidence / authority / web) is never re-derived client-side,
// and an unsupported locator is shown struck through, never as a source.

import { renderMarkdown } from "./markdown.js";

const KIND_LABEL = { evidence: "Case evidence", authority: "Legal authority", web: "Web source" };
const SOURCE_TAG = /\[Source:\s*([^\]]+)\]/g;

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function kindLabel(kind) {
  return KIND_LABEL[kind] || kind;
}

function findCitation(citations, citedAs) {
  return (
    citations.find((c) => c.cited_as === citedAs) ||
    citations.find((c) => c.locator === citedAs) ||
    citations.find((c) => c.locator && citedAs.startsWith(c.locator)) ||
    null
  );
}

export function citationChip(ref, onOpen, label) {
  return el("button", {
    type: "button",
    class: `cite cite--${ref.kind}`,
    title: `${kindLabel(ref.kind)}: ${ref.document}`,
    onclick: () => onOpen(ref),
  }, label || ref.cited_as || ref.locator || ref.document);
}

export function unsupportedChip(locator) {
  return el("span", {
    class: "cite cite--unsupported",
    title: "This locator was not among the retrieved passages",
  }, locator);
}

// Replaces [Source: X] tags in the answer with clickable chips.
function renderTextPart(body, text, citations, unsupported, onOpen) {
  if (!text) return;
  // A [Source: …] tag sits between formatted blocks, never inside one, so the
  // chip stays a button and the surrounding markdown still formats.
  let last = 0;
  let sawTag = false;
  for (const match of text.matchAll(SOURCE_TAG)) {
    sawTag = true;
    renderMarkdown(body, text.slice(last, match.index));
    const citedAs = match[1].trim();
    const ref = findCitation(citations, citedAs);
    if (ref) body.append(citationChip(ref, onOpen, citedAs));
    else if (unsupported.includes(citedAs)) body.append(unsupportedChip(citedAs));
    else renderMarkdown(body, match[0]);
    last = match.index + match[0].length;
  }
  renderMarkdown(body, sawTag ? text.slice(last) : text);
}

export function renderAnswerText(answer, citations, unsupported, onOpen) {
  const body = el("div", { class: "msg__body prose" });
  renderTextPart(body, answer, citations, unsupported, onOpen);
  return body;
}

export function renderCitationList(title, citations, onOpen) {
  if (!citations.length) return null;
  const list = el("div", { class: "cite-list" }, [el("span", { class: "cite-list__title", text: title })]);
  for (const ref of citations) {
    list.append(citationChip(ref, onOpen, `${ref.locator || ref.document}`));
  }
  return list;
}

export function renderPassages(retrieved, onOpen) {
  if (!retrieved.length) return null;
  const details = el("details", { class: "passages" }, [
    el("summary", { text: `Passages consulted (${retrieved.length})` }),
  ]);
  for (const ref of retrieved) {
    details.append(
      el("div", { class: "passage-row" }, [
        el("div", {}, [
          citationChip(ref, onOpen, ref.locator || ref.document),
          " ",
          el("span", { class: "muted small", text: `${ref.document}${ref.page ? `, p. ${ref.page}` : ""}` }),
        ]),
        el("blockquote", { text: ref.quote || "" }),
      ]),
    );
  }
  return details;
}

export function createPassageDialog(dialog) {
  const kind = dialog.querySelector("#passage-kind");
  const doc = dialog.querySelector("#passage-doc");
  const locator = dialog.querySelector("#passage-locator");
  const quote = dialog.querySelector("#passage-quote");
  const url = dialog.querySelector("#passage-url");
  const note = dialog.querySelector("#passage-note");
  return function open(ref) {
    kind.textContent = kindLabel(ref.kind);
    kind.className = `badge badge--${ref.kind}`;
    doc.textContent = ref.document;
    const parts = [];
    if (ref.locator) parts.push(ref.locator);
    if (ref.cited_as && ref.cited_as !== ref.locator) parts.push(`cited as ${ref.cited_as}`);
    if (ref.page) parts.push(`page ${ref.page}`);
    if (ref.jurisdiction) parts.push(ref.jurisdiction);
    locator.textContent = parts.join(" · ");
    quote.textContent = ref.quote || "";
    quote.hidden = !ref.quote;
    url.replaceChildren();
    if (ref.url) {
      url.append(el("a", { class: "link", href: ref.url, target: "_blank", rel: "noopener noreferrer" }, ref.url));
    }
    note.textContent =
      ref.kind === "web"
        ? "Web material is legal authority at best. It was not checked against this matter's documents and cannot stand in for missing case evidence."
        : ref.kind === "authority"
          ? "Legal authority supports a legal proposition; it does not prove a fact of this case."
          : "Case evidence: text from a document uploaded to this matter, shown verbatim.";
    dialog.showModal();
  };
}
