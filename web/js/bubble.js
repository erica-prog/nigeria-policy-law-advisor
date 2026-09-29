// The advisor's speech bubble: one short sentence at a time plus optional
// follow-up chips. It is a live region so the sentence is announced; the
// character's caption (avatar.js) separately announces the trust state, as
// docs/15 requires. Animations are CSS and respect prefers-reduced-motion.

import { el } from "./citations.js";

export function createBubble(root) {
  const text = root.querySelector(".bubble__text");
  const typing = root.querySelector(".bubble__typing");
  const chips = root.querySelector(".bubble__chips");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function pop() {
    if (reducedMotion.matches) return;
    root.classList.remove("bubble--pop");
    void root.offsetWidth;
    root.classList.add("bubble--pop");
  }

  function say(message, options = {}) {
    text.textContent = message;
    typing.hidden = true;
    root.classList.toggle("bubble--warn", options.tone === "warn");
    root.classList.toggle("bubble--error", options.tone === "error");
    chips.replaceChildren(
      ...(options.chips || []).map((chip) =>
        el("button", { type: "button", class: "chip", onclick: chip.onClick }, chip.label),
      ),
    );
    chips.hidden = !chips.childElementCount;
    pop();
  }

  function think(message) {
    text.textContent = message;
    typing.hidden = false;
    root.classList.remove("bubble--warn", "bubble--error");
    chips.replaceChildren();
    chips.hidden = true;
    pop();
  }

  return { say, think };
}
