// Turns model markdown into elements. The model writes headings, bold, lists
// and links; showing that source as text is not readable. HTML in the model
// text is escaped, and links are kept only when they are http(s).

import { el } from "./citations.js";

const BLOCK = /^(#{1,4})\s+(.*)$/;
const RULE = /^(?:---|\*\*\*+)$/;
const BULLET = /^[-*]\s+(.*)$/;
const NUMBERED = /^\d+[.)]\s+(.*)$/;

function appendInline(parent, text) {
  const pattern =
    /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*\n]+)\*\*|__([^_\n]+)__|`([^`\n]+)`|(https?:\/\/[^\s)<]+)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) parent.append(document.createTextNode(text.slice(last, match.index)));
    if (match[1] !== undefined) parent.append(link(match[1], match[2]));
    else if (match[3] !== undefined) parent.append(el("strong", { text: match[3] }));
    else if (match[4] !== undefined) parent.append(el("strong", { text: match[4] }));
    else if (match[5] !== undefined) parent.append(el("code", { text: match[5] }));
    else parent.append(link(match[6], match[6]));
    last = match.index + match[0].length;
  }
  if (last < text.length) parent.append(document.createTextNode(text.slice(last)));
}

function link(label, href) {
  return el("a", { href, target: "_blank", rel: "noopener noreferrer" }, [label]);
}

// Appends block elements for `text` onto `parent`. Returns the parent.
export function renderMarkdown(parent, text) {
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  let paragraph = [];
  let list = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const node = el("p");
    appendInline(node, paragraph.join(" "));
    parent.append(node);
    paragraph = [];
  };
  const endList = () => {
    list = null;
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flushParagraph();
      endList();
      continue;
    }
    const heading = BLOCK.exec(line);
    if (heading) {
      flushParagraph();
      endList();
      const node = el(`h${Math.min(heading[1].length + 1, 4)}`);
      appendInline(node, heading[2]);
      parent.append(node);
      continue;
    }
    if (RULE.test(line)) {
      flushParagraph();
      endList();
      parent.append(el("hr"));
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = NUMBERED.exec(line);
    if (bullet || numbered) {
      flushParagraph();
      const type = bullet ? "ul" : "ol";
      if (!list || list.type !== type) {
        list = { type, node: el(type) };
        parent.append(list.node);
      }
      const item = el("li");
      appendInline(item, (bullet || numbered)[1]);
      list.node.append(item);
      continue;
    }
    endList();
    paragraph.push(line);
  }
  flushParagraph();
  return parent;
}
