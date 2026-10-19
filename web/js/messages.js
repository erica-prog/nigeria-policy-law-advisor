// Renders conversation cards. The answer/analysis renderers are unchanged
// from revision 1: citations are shown exactly as the backend labelled them
// (case evidence / legal authority / web source) and open the passage dialog.
// `renderReply` wraps them for the unified chat reply.

import {
  citationChip,
  el,
  renderAnswerText,
  renderCitationList,
  renderPassages,
  unsupportedChip,
} from "./citations.js";

export function renderUserMessage(text) {
  return el("article", { class: "msg msg--user", text });
}

export function renderErrorMessage(text) {
  return el("article", { class: "msg msg--assistant msg--error", role: "alert", text });
}

export function renderAnswer(result, openPassage, { label } = {}) {
  const card = el("article", {
    class: `msg msg--assistant msg--${result.source}${result.faithful ? "" : " msg--unverified"}`,
  });
  const heading =
    label ||
    (result.source === "web"
      ? "⚠ From an official website, not verified against your documents"
      : result.source === "none"
        ? "Nothing relevant found"
        : "From your documents");
  card.append(el("div", { class: "msg__label", text: heading }));
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
    card.append(
      el("div", {
        class: "msg__info",
        text: "Web material is labelled as legal authority. It cannot replace missing case evidence.",
      }),
    );
  }
  const cited = renderCitationList(result.source === "web" ? "Web sources" : "Sources cited", result.citations, openPassage);
  if (cited) card.append(cited);
  const passages = renderPassages(result.retrieved, openPassage);
  if (passages) card.append(passages);
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

export function renderAnalysis(result, openPassage) {
  const card = el("article", {
    class: `msg msg--assistant msg--analysis${result.avatar_state === "unverified" ? " msg--unverified" : ""}`,
  });
  card.append(el("div", { class: "msg__label", text: "Case analysis from your documents" }));
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
  card.append(
    el("section", { class: "issue" }, [
      el("h3", { text: "Overall position" }),
      el("p", { class: "msg__body", text: result.overall_position }),
    ]),
  );
  if (result.missing_evidence.length) {
    card.append(
      el("section", { class: "issue" }, [
        el("h3", { text: "Missing support" }),
        el(
          "ul",
          { class: "gap-list" },
          result.missing_evidence.map((gap) => el("li", {}, [el("strong", { text: gap.issue }), ` — ${gap.note}`])),
        ),
      ]),
    );
  }
  const cited = renderCitationList("Authorities resolved", result.citations, openPassage);
  if (cited) card.append(cited);
  card.append(el("div", { class: "disclaimer", text: result.disclaimer }));
  return card;
}

// One chat reply: a notice (when the server sent one) plus the full answer or
// analysis card. The notice text comes from the server so the wording about
// research versus evidence is the same everywhere.
export function renderReply(reply, openPassage) {
  const wrap = el("div", { class: `reply reply--${reply.mode}` });
  if (reply.notice) {
    wrap.append(el("div", { class: "notice", role: "note" }, ["⚠ ", reply.notice]));
  }
  if (reply.analysis) {
    wrap.append(renderAnalysis(reply.analysis, openPassage));
  } else if (reply.answer) {
    const label =
      reply.mode === "research"
        ? reply.answer.source === "web"
          ? "General legal research from official websites, not evidence from your case"
          : "General legal research: nothing relevant found"
        : undefined;
    wrap.append(renderAnswer(reply.answer, openPassage, { label }));
  }
  return wrap;
}
