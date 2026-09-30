// The advisor's speech bubble: one short sentence at a time plus optional
// follow-up chips. Since revision 3 it is live throughout a conversation
// (attaching files, stage narration while the server works, toggles), so the
// accessible announcement is throttled: the visible text updates at once, but
// the polite live region repeats it at most once every ~2 s, with the final
// `say()` always flushed immediately. The character's caption (avatar.js)
// separately announces the trust state, as docs/15 requires. Animations are
// CSS and respect prefers-reduced-motion.

import { el } from "./citations.js";

export const ANNOUNCE_GAP_MS = 2000;

export function createBubble(root) {
  const text = root.querySelector(".bubble__text");
  const live = root.querySelector(".bubble__live");
  const typing = root.querySelector(".bubble__typing");
  const chips = root.querySelector(".bubble__chips");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let lastAnnounced = 0;
  let pending = null;

  function announce(message, { immediate = false } = {}) {
    if (pending) {
      window.clearTimeout(pending);
      pending = null;
    }
    const elapsed = performance.now() - lastAnnounced;
    const flush = () => {
      pending = null;
      lastAnnounced = performance.now();
      // Re-set even when identical so the live region re-announces.
      live.textContent = "";
      live.textContent = message;
    };
    if (immediate || elapsed >= ANNOUNCE_GAP_MS) flush();
    else pending = window.setTimeout(flush, ANNOUNCE_GAP_MS - elapsed);
  }

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
    announce(message, { immediate: true });
    pop();
  }

  // A transient sentence while something is happening: typing indicator on,
  // no chips, announced at most once per ANNOUNCE_GAP_MS.
  function think(message, { pop: shouldPop = true } = {}) {
    text.textContent = message;
    typing.hidden = false;
    root.classList.remove("bubble--warn", "bubble--error");
    chips.replaceChildren();
    chips.hidden = true;
    announce(message);
    if (shouldPop) pop();
  }

  return {
    say,
    think,
    get text() {
      return text.textContent;
    },
  };
}
