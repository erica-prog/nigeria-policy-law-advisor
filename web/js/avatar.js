// Advisor character state machine (docs/15-avatar-character-asset.md).
//
// Only three states have their own artwork; the rest use the idle frame plus
// CSS on the wrapper. The frames are cropped with ONE shared bounding box so
// the character stays the same size and position when the state changes.
// The caption is the accessible state (aria-live) and always matches the
// banner baked into the frame.

// Union of the four frames' alpha bounding boxes on the 1254x1254 canvas.
// Recompute (docs/15, "Frame registration") if a frame is added or redrawn.
const CANVAS = 1254;
const CROP = { left: 165, top: 17, right: 1170, bottom: 1219 };
const CROP_W = CROP.right - CROP.left; // 1005
const CROP_H = CROP.bottom - CROP.top; // 1202

const FRAME = {
  idle: "idle",
  listening: "listening",
  verified_source: "verified-source",
  no_results: "no-results",
  web_source: "idle", // no artwork yet: amber outline + caption (docs/15 "the gap")
  unverified: "idle", // no artwork yet: red outline + caption
  error: "idle",
};

const CAPTION = {
  idle: "Ready",
  listening: "Listening",
  verified_source: "Verified source: citations checked against your documents",
  no_results: "No results in your documents",
  web_source: "From an official website, not checked against your documents",
  unverified: "Unverified: a cited passage was not among the retrieved text",
  error: "Advisor unavailable",
};

const frameUrl = (name) => `/avatar/advisor-${name}.png`;

export function createAvatar(root) {
  const img = root.querySelector("img");
  const caption = root.querySelector(".advisor__caption");
  const detail = root.querySelector(".advisor__detail");
  const frameBox = root.querySelector(".advisor__frame");
  let current = "idle";

  // Shared crop box, expressed as percentages of the frame box.
  frameBox.style.setProperty("--crop-aspect", `${CROP_W} / ${CROP_H}`);
  frameBox.style.setProperty("--img-width", `${(CANVAS / CROP_W) * 100}%`);
  frameBox.style.setProperty("--img-left", `${(-CROP.left / CROP_W) * 100}%`);
  frameBox.style.setProperty("--img-top", `${(-CROP.top / CROP_H) * 100}%`);

  // Swapping src to an unfetched image shows a blank box at the exact moment
  // the user is looking; fetch all four once.
  for (const name of new Set(Object.values(FRAME))) {
    const pre = new Image();
    pre.src = frameUrl(name);
  }

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function setState(state, options = {}) {
    if (!(state in FRAME)) state = "idle";
    const nextSrc = frameUrl(FRAME[state]);
    const text = options.caption || CAPTION[state];
    const swap = () => {
      img.src = nextSrc;
      root.classList.remove("advisor--swapping");
    };
    root.classList.forEach((cls) => {
      if (cls.startsWith("advisor--") && cls !== "advisor--swapping") root.classList.remove(cls);
    });
    root.classList.add(`advisor--${state}`);
    if (options.busy) root.classList.add("advisor--busy");
    caption.textContent = text;
    detail.textContent = options.detail || "";
    root.dataset.state = state;
    // Gentle bounce on a real state change; CSS disables it under
    // prefers-reduced-motion, so the class is harmless there.
    if (state !== current && !reducedMotion.matches) {
      root.classList.remove("advisor--pop");
      void root.offsetWidth; // restart the animation
      root.classList.add("advisor--pop");
    }
    if (img.getAttribute("src") !== nextSrc) {
      if (reducedMotion.matches) {
        swap();
      } else {
        root.classList.add("advisor--swapping");
        window.setTimeout(swap, 150);
      }
    }
    current = state;
  }

  return {
    setState,
    get state() {
      return current;
    },
    toggleHidden() {
      root.classList.toggle("is-hidden");
      return root.classList.contains("is-hidden");
    },
  };
}
