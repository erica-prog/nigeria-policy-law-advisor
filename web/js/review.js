// Review queue and artefact viewer (docs/36). Two dialogs: the queue lists what
// still needs a decision on the current case (draft or reviewed artefacts,
// open or reviewed findings) with the actions the server will accept for this
// user's role; the viewer shows an artefact's outline, its provenance
// manifest entry by entry, the judge's verdicts, and the download links. The
// server decides every transition; the client only shows buttons and relays
// the answer, including a refusal.

import { el } from "./citations.js";

const ARTEFACT_ACTIONS = {
  draft: ["review", "reject"],
  reviewed: ["approve", "reject"],
  rejected: ["reopen"],
  approved: [],
};
const FINDING_ACTIONS = {
  open: ["review", "dismiss"],
  reviewed: ["dismiss"],
  dismissed: ["reopen"],
};
const ACTION_LABEL = {
  review: "Mark reviewed",
  approve: "Approve",
  reject: "Reject",
  dismiss: "Dismiss",
  reopen: "Reopen",
};
// Which roles the server accepts for each action (review.py); used only to
// hide buttons that would certainly be refused.
const ROLE_FOR = {
  review: () => true,
  approve: (user) => user.role === "reviewer" || user.role === "admin",
  reject: (user) => user.role === "reviewer" || user.role === "admin",
  dismiss: (user) => user.role === "reviewer" || user.role === "admin",
  reopen: (user, matter) => !matter.read_only,
};

function statusBadge(status) {
  return el("span", { class: `badge badge--${status}`, text: status });
}

export function createReviewPanel({ api, dialog, viewer, getMatter, getUser, onError }) {
  const list = dialog.querySelector("#review-list");
  const title = dialog.querySelector("#review-title");
  const closeButton = dialog.querySelector("#review-close");
  closeButton.addEventListener("click", () => dialog.close());

  async function act(kind, id, action) {
    const matter = getMatter();
    const note = window.prompt(`${ACTION_LABEL[action]}: add a note for the trail (optional)`) || null;
    try {
      if (kind === "artefact") await api.reviewArtefact(matter.id, id, action, note);
      else await api.reviewFinding(matter.id, id, action, note);
      await render();
    } catch (error) {
      onError(error);
    }
  }

  function actionButtons(kind, item, actions) {
    const user = getUser();
    const matter = getMatter();
    return actions
      .filter((action) => ROLE_FOR[action](user, matter))
      .map((action) =>
        el("button", { class: "btn btn--tiny", type: "button", onClick: () => act(kind, kind === "artefact" ? item.artefact_id : item.id, action) }, [ACTION_LABEL[action]]),
      );
  }

  async function render() {
    const matter = getMatter();
    if (!matter) return;
    title.textContent = `Review queue: ${matter.title || matter.id}`;
    list.replaceChildren(el("p", { class: "muted", text: "Loading…" }));
    let queue;
    try {
      queue = await api.reviewQueue(matter.id);
    } catch (error) {
      onError(error);
      list.replaceChildren(el("p", { class: "form-error", text: "Could not load the queue." }));
      return;
    }
    const rows = [];
    for (const artefact of queue.artefacts) {
      const counts = (artefact.manifest && artefact.manifest.counts) || {};
      rows.push(
        el("li", { class: "review-row" }, [
          el("div", { class: "review-row__head" }, [statusBadge(artefact.status), el("strong", { text: artefact.title }), el("span", { class: "muted small", text: `${artefact.kind} by ${artefact.created_by || "?"}` })]),
          el("div", { class: "muted small", text: `Provenance: ${Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(", ") || "none"}` }),
          el("div", { class: "review-row__actions" }, [
            el("button", { class: "btn btn--tiny btn--ghost", type: "button", onClick: () => viewer.open(artefact) }, ["Open"]),
            ...actionButtons("artefact", artefact, ARTEFACT_ACTIONS[artefact.status] || []),
          ]),
        ]),
      );
    }
    for (const finding of queue.findings) {
      rows.push(
        el("li", { class: "review-row" }, [
          el("div", { class: "review-row__head" }, [statusBadge(finding.status), el("span", { class: `badge badge--${finding.severity}`, text: finding.severity }), el("strong", { text: finding.kind.replace(/_/g, " ") })]),
          el("div", { text: finding.summary }),
          el("div", { class: "muted small", text: `Documents: ${finding.documents.join(", ")}` }),
          el("div", { class: "review-row__actions" }, actionButtons("finding", finding, FINDING_ACTIONS[finding.status] || [])),
        ]),
      );
    }
    list.replaceChildren(...(rows.length ? rows : [el("p", { class: "muted", text: "Nothing waits for a decision on this case." })]));
  }

  return {
    async open() {
      if (!getMatter()) {
        onError({ message: "Open a case first." });
        return;
      }
      dialog.showModal();
      await render();
    },
    refresh: render,
  };
}

export function createArtefactViewer({ api, dialog, getMatter, onError }) {
  const title = dialog.querySelector("#artefact-title");
  const meta = dialog.querySelector("#artefact-meta");
  const body = dialog.querySelector("#artefact-body");
  const links = dialog.querySelector("#artefact-links");
  dialog.querySelector("#artefact-close").addEventListener("click", () => dialog.close());

  function renderOutline(artefact) {
    const outline = artefact.outline || {};
    const manifest = artefact.manifest || { entries: [], judge: [] };
    const rejected = new Set(manifest.entries.filter((e) => e.status === "rejected_evidence").map((e) => `${e.section}.${e.paragraph}`));
    const unverified = new Set(manifest.entries.filter((e) => e.status === "unverified").map((e) => `${e.section}.${e.paragraph}`));
    const nodes = [];
    if (outline.summary) nodes.push(el("p", { class: "muted", text: outline.summary }));
    (outline.sections || []).forEach((section, s) => {
      nodes.push(el("h3", { text: section.heading }));
      const verdict = (manifest.judge || []).find((j) => j.section === s);
      if (verdict) nodes.push(el("p", { class: `judge judge--${verdict.verdict.toLowerCase()}`, text: `Judge: ${verdict.verdict}${verdict.notes ? ` — ${verdict.notes}` : ""}` }));
      (section.paragraphs || []).forEach((paragraph, p) => {
        const key = `${s}.${p}`;
        if (rejected.has(key)) {
          nodes.push(el("p", { class: "artefact__rejected", text: "[Paragraph removed: it cited case evidence as legal authority.]" }));
          return;
        }
        const node = el("p", { class: paragraph.kind === "instruction" ? "artefact__instruction" : "" }, [paragraph.text]);
        if (paragraph.sources && paragraph.sources.length) node.append(el("span", { class: "muted small", text: ` [${paragraph.sources.join("; ")}]` }));
        if (unverified.has(key)) node.append(el("span", { class: "artefact__unverified", text: " UNVERIFIED" }));
        nodes.push(node);
      });
    });
    if ((outline.limitations || []).length) {
      nodes.push(el("h3", { text: "Limitations" }));
      nodes.push(el("ul", {}, outline.limitations.map((item) => el("li", { text: item }))));
    }
    nodes.push(el("h3", { text: "Provenance" }));
    const entries = manifest.entries.filter((e) => e.status !== "instruction");
    nodes.push(
      el("table", { class: "manifest" }, [
        el("thead", {}, [el("tr", {}, ["§", "Source", "Status", "Instrument", "Version"].map((h) => el("th", { text: h })))]),
        el("tbody", {}, entries.map((e) => el("tr", { class: `manifest__row manifest__row--${e.status}` }, [
          el("td", { text: `${e.section + 1}.${e.paragraph + 1}` }),
          el("td", { text: e.pinpoint || "(none)" }),
          el("td", {}, [statusBadge(e.status)]),
          el("td", { text: e.instrument || "" }),
          el("td", { text: e.version_date || e.access_date || "" }),
        ]))),
      ]),
    );
    return nodes;
  }

  return {
    async open(artefact) {
      const matter = getMatter();
      title.textContent = artefact.title;
      meta.replaceChildren(statusBadge(artefact.status), el("span", { class: "muted small", text: ` ${artefact.kind}, ${artefact.created_by || "?"}, ${(artefact.created_at || "").slice(0, 10)}` }));
      body.replaceChildren(...renderOutline(artefact));
      const anchors = [];
      if (artefact.has_docx) anchors.push(el("a", { class: "btn btn--tiny", href: `/api/matters/${matter.id}/artefacts/${artefact.artefact_id}/file?format=docx` }, ["Download memo (.docx)"]));
      if (artefact.has_pptx) anchors.push(el("a", { class: "btn btn--tiny", href: `/api/matters/${matter.id}/artefacts/${artefact.artefact_id}/file?format=pptx` }, ["Download slides (.pptx)"]));
      try {
        const { events } = await api.artefactEvents(matter.id, artefact.artefact_id);
        if (events.length) {
          anchors.push(el("ul", { class: "events" }, events.map((e) => el("li", { class: "muted small", text: `${(e.created_at || "").slice(0, 16)} ${e.username || "?"} (${e.role || "?"}): ${e.action}, ${e.from_status} → ${e.to_status}${e.note ? ` — ${e.note}` : ""}` }))));
        }
      } catch (error) {
        onError(error);
      }
      links.replaceChildren(...anchors);
      dialog.showModal();
    },
  };
}
