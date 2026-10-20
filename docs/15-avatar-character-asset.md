# 15 — The Advisor Character: Asset and Usage

The pixel-style advisor character that represents the agent in the web client,
as a set of interchangeable state frames. This document covers what the assets
actually are, which interaction states they cover, and how the frontend
consumes them.

> **Note on links:** this file references docs 12–14 (the voice/avatar
> architecture set), which exist in the local working copy but are **not yet
> committed to this repository** — `docs/` is untracked on `main`. The links
> below resolve once those files are pushed.
>
> - [12-voice-web-app-architecture.md](12-voice-web-app-architecture.md) — API boundary
> - [13-voice-pipeline.md](13-voice-pipeline.md) — speech in/out
> - [14-cartoon-avatar-frontend.md](14-cartoon-avatar-frontend.md) — the avatar state machine this asset plugs into

## The assets

Four frames, all 1254 × 1254 RGBA with fully transparent backgrounds. The
character is a full-body chibi advocate in a black suit, holding brass scales
of justice in one hand and a leather case file under the other.

| File | Frame | Added decoration | Content bounds |
|---|---|---|---|
| `advisor-idle.png` | Neutral pose | none | 827 × 1163 |
| `advisor-listening.png` | Listening | "LISTENING" banner, cyan sound waves | 770 × 1199 |
| `advisor-no-results.png` | Nothing found | "NO RESULTS" banner, document glyph | 886 × 1171 |
| `advisor-verified-source.png` | Answer from your documents | "VERIFIED SOURCE" banner, document with green tick | 1005 × 1175 |

The palette — black suit, gold/amber accents, violet highlights, white-and-gold
streaked hair — is worth treating as the app's accent palette too, so the UI
and the character don't look like they came from different products.

### It is not true pixel art, and that has consequences

Worth measuring before you write any CSS. The file contains **114,465 unique
colours**, and scanning it column by column shows changes at almost every
single pixel (885 of 906 detected boundaries are one pixel apart). Real pixel
art at a 64- or 128-pixel grid would show changes only at grid boundaries and
use a palette of a few dozen colours. Downscaling to candidate grid sizes and
measuring reconstruction error produces a smooth curve with no knee, which is
the signature of a high-resolution illustration drawn in a pixel-art *style*
rather than a sprite snapped to a grid.

Three practical consequences:

- **`image-rendering: pixelated` will not make this crisp.** That CSS property
  tells the browser to scale with nearest-neighbour sampling, which only looks
  right when the source is on a clean pixel grid. Applied here it will
  emphasise the anti-aliasing that's already baked in. Use the default smooth
  scaling.
- **Scale down, never up.** At 1254² you have plenty of headroom for a display
  size of 200–400 px. Rendering above native resolution will look soft and
  give away that it isn't really pixel art.
- **If you want genuine pixel crispness**, the asset has to be re-authored at
  its native resolution (say 96 × 96 or 128 × 128) with a quantised palette and
  hand cleanup. That's an art task, not something a downscale filter can
  recover — but it is also what makes per-frame animation practical, so it's
  worth considering alongside the frame inventory below.

### Provenance and licensing

Record where this came from before the product goes anywhere near a client.
For a tool whose entire value proposition is that its outputs are traceable
and verifiable, shipping artwork of unrecorded origin is an odd gap — and if
the character becomes the product's face, its licence terms matter. Add the
author, tool, or licence to this section once known.

## What the frames can and can't do

Two structural limits hold across all four frames. They are accepted
constraints, not defects to engineer around:

- **No mouth frames.** At the intended display size the mouth is a few pixels
  wide. There is nothing to animate and nothing to swap.
- **Both hands are occupied** — scales in one, case file under the other. Poses
  that want a free hand (a raised finger for "listening", an open palm for
  "I found nothing") need the character redrawn, not just reposed.

The three state frames respect both: the pose is identical in all four, and
they differ only in what's added *around* the character — a banner above, a
glyph to the side. That's a sound way to build the set. The character can't
drift out of character between states, and a new state costs a banner and a
glyph rather than a redraw.

### Coverage against the state machine

[14-cartoon-avatar-frontend.md](14-cartoon-avatar-frontend.md) binds the avatar
to eleven interaction states. Four now have artwork; the rest are the idle
frame plus CSS on the wrapper.

| State | Frame | Treatment |
|---|---|---|
| `idle` | `advisor-idle` | As-is, caption "Ready" |
| `listening` | `advisor-listening` | As-is — the sound waves carry it |
| `transcribing` | `advisor-idle` | Travelling gradient ring; caption "Working out what you said" |
| `confirming` | `advisor-idle` | Dimmed to push focus to the editable transcript |
| `retrieving` | `advisor-idle` | Slow bob (2–3 px, ~1.5 s); caption "Reading your documents" |
| `checking` | `advisor-idle` | Bob continues; caption "Checking citations" — only if the backend reports the stage (doc 14 §2) |
| `speaking` | `advisor-idle` | Amplitude-driven audio meter beside the character |
| Grounded answer (`source="corpus"`, `faithful=true`) | `advisor-verified-source` | As-is, but see the wording problem below |
| `not-found` (`source="none"`) | `advisor-no-results` | As-is |
| `web-source` (`source="web"`) | **none** | Amber border + warning icon + caption |
| `unverified` (`faithful=false`) | **none** | Red border + caption naming the unsupported locator |

### The gap: the cautionary states are the ones without artwork

Of the three trust states the backend actually distinguishes, the reassuring
one got bespoke art and the two warnings didn't. `source="web"` (an answer from
an official website that was never checked against the lawyer's documents) and
`faithful=false` (the answer cited a locator that wasn't retrieved) are exactly
the situations where the interface most needs to change character, and they're
currently a CSS border.

That asymmetry pushes in the wrong direction. A confident, illustrated
"VERIFIED SOURCE" frame competing with a thin amber outline means the
reassuring state is the most visually salient thing in the product, which is
the trust miscalibration doc 14 §1 warns about. **Commission those two frames
before `speaking` or `retrieving`** — the animation states are cosmetic, these
two carry the professional risk.

The scales make both easy to draw without touching the pose: tipped and
off-balance for `unverified`, one pan holding a web page instead of a document
for `web-source`, with the banner and glyph following the established pattern.

Two rules carried over from doc 14 that these assets make concrete:

- **Don't fake the mouth.** With no mouth frames, the amplitude signal from the
  `AnalyserNode` should drive something that *is* honest about being an
  indicator — equalizer bars, a pulsing ring, a waveform strip — rather than a
  crude mouth rectangle pasted over the character's face. A bad lip-sync reads
  as broken; a clean audio meter reads as deliberate.
- **The caption is not optional**, even now that three frames have their state
  written on them. It's the accessible state, announced via `aria-live` while
  the image itself stays `aria-hidden` — and as the next section explains, text
  baked into a PNG doesn't reach a screen reader, a translator, or a user who
  zooms.

## The banners are baked-in text, and that has three costs

The "LISTENING", "NO RESULTS", and "VERIFIED SOURCE" banners are pixels, not
text. They read well at display size — legibility isn't the issue — but they
carry three problems, in increasing order of seriousness.

**They can't be translated.** The app asks the lawyer to pick a conversation
language explicitly and threads it through generation (`LANGUAGE_NAMES =
{"en": ..., "fr": ...}` in
[prompt.py](../src/policy_advisor/generation/prompt.py)). A French session with
an English banner above the character's head is the one inconsistency this
product has otherwise avoided: language is chosen, never assumed. Every new
language means re-exporting every framed PNG.

**They can't be read by assistive technology.** The image is `aria-hidden` and
decorative by design, so a screen reader announces the caption and never the
banner. Sighted and non-sighted users get different information from the same
state, and the banner can't be zoomed, restyled, or selected.

**"VERIFIED SOURCE" claims more than the system does — fix this one.** In the
question-answering path the only check that runs is `check_faithfulness`
([faithfulness.py](../src/policy_advisor/generation/faithfulness.py)), which
extracts the `[Source: ...]` locators from the answer and confirms each one
appeared among the retrieved chunks, allowing a prefix match so a sub-rule
pinpoint counts. That is a citation-*existence* check. It does not confirm the
cited passage supports the claim, and it does not confirm the answer is
legally correct — the Claude-as-judge pass that would go further
([judge_check.py](../src/policy_advisor/generation/judge_check.py)) isn't wired
into `RAGChain.answer()`.

So a gold banner reading "VERIFIED SOURCE", next to a green tick, tells a
lawyer their answer has been verified when what actually happened is that its
citations point at passages that were retrieved. For a tool whose entire
purpose is calibrating how much to trust an answer, that's the most consequential
word in the interface to get wrong. Better wording, in order of preference:

- **"CITATIONS CHECKED"** — accurate, and says what was checked.
- **"FROM YOUR DOCUMENTS"** — accurate and states provenance, which is the
  distinction from the `web` case.
- Avoid "VERIFIED", "CONFIRMED", "ACCURATE", and anything with a tick that
  implies the substance was reviewed.

### Recommendation

Commission the frames **without banners** and render the label as HTML text
next to the character, styled to look like the banner. The glyphs (sound
waves, document, tick) can stay baked in — they're language-neutral and
decorative. This fixes translation and accessibility in one move, makes the
wording above changeable without touching artwork, and removes the banner from
the alignment problem in the next section.

Until then, use the framed PNGs as they are and keep the caption text in sync
with the banner so the two channels never disagree.

## Frame registration: crop all frames with one box

This is the practical bug you'll hit first when swapping frames, and it's a
correction to the generation recipe originally published in this document.

Each frame's own alpha bounding box is a different size, because the banners
and side glyphs differ in width and height:

```
advisor-idle.png             827 x 1163
advisor-listening.png        770 x 1199
advisor-no-results.png       886 x 1171
advisor-verified-source.png  1005 x 1175
```

Crop each frame to its *own* bounding box and scale to a common height, and
the character comes out a different size in every frame — the widest banner
("VERIFIED SOURCE") forces the whole image down hardest, so the character
shrinks most in the frame you most want to look authoritative. Swapping states
then makes the character visibly grow, shrink, and slide.

The fix is one line of intent: **compute the union of all four bounding boxes
and crop every frame with that same box.** The character is already registered
consistently within the source canvas — the feet sit on a common baseline
within 5 px of 1254 — so a shared crop preserves that registration. Measured
on the current four frames:

| | Per-frame crop | Shared crop |
|---|---|---|
| Tile size at 200 px tall | 142, 128, 151, 171 px wide | 167 × 200 for all four |
| Foot-centre spread | visible size and position jump | 78.6–81.5 px (≈ 2.8 px) |

Identical tile dimensions also mean the `<img>` box never changes size, so
swapping frames can't reflow the layout around it.

Because the frames were drawn independently rather than exported from one
master, small differences in the character's proportions survive the shared
crop. They're minor next to the gross jump, but it's worth eyeballing a swap
before shipping — and it's an argument for the eventual frames coming out of a
single source file.

## The frame inventory, when you commission the animation set

Three state frames exist. This is the rest of the list, in priority order:
`web-source` and `unverified` first (the gap above), then mouth shapes, then
the cosmetic states. Keep the same palette, pose, and canvas so the shared crop
box keeps working.

**Mouth shapes — 4 to 6 frames**, mapped to amplitude buckets: closed, slightly
open, mid, wide, plus optionally a rounded "o" and a teeth-showing frame.
Doc 13's amplitude-driven approach can't distinguish phonemes, so more than six
is wasted unless you move to a TTS that emits real visemes.

**State poses**, and here there's a design idea worth taking seriously: **the
scales are already a trust indicator.** The character is holding the exact
metaphor the pipeline needs, so encode the epistemic state in the prop rather
than in the face:

| State | Pose |
|---|---|
| Grounded answer (`source="corpus"`, `faithful=true`) | Scales level and steady, gold glow |
| Unverified citation (`faithful=false`) | Scales visibly tipped, amber glow |
| Web source (`source="web"`) | One pan holding a small globe or web page instead of a document, amber tint |
| Nothing found (`source="none"`) | Both pans empty, character glancing at them |
| Retrieving | Character looking down at the case file, scales swaying |
| Listening | Scales lowered, head turned slightly toward the viewer |

That mapping is legible at a glance, survives being small, doesn't rely on
facial detail that a chibi style can't carry, and — the part that matters —
ties the visual directly to the `source` and `faithful` fields the backend
actually returns, rather than to a mood the designer picked.

## How the web app consumes it

### File layout

The frontend (doc 12: a TypeScript client against the FastAPI backend) copies
runtime-sized derivatives out of this folder at build time. Keep the source
here; don't commit a dozen resized variants.

```
assets/avatar/                      # committed masters, 1254² each
  advisor-idle.png
  advisor-listening.png
  advisor-no-results.png
  advisor-verified-source.png
web/public/avatar/                  # generated at build time, gitignored
  advisor-idle-200.png              # 167 × 200 — 1x display
  advisor-idle-400.png              # 334 × 400 — 2x for retina
  advisor-listening-200.png         # …and so on for each frame
```

The number in the filename is the **height**. With the shared crop box of
1005 × 1202 the aspect ratio is 0.836, so every frame comes out 167 × 200 at 1x
and 334 × 400 at 2x — identical dimensions, which is the point.

Generating them (Pillow is already a transitive dependency via `pdf2image`):

```bash
python3 - <<'PY'
from pathlib import Path
from PIL import Image

src = Path("assets/avatar")
out = Path("web/public/avatar"); out.mkdir(parents=True, exist_ok=True)
frames = {p.stem.removeprefix("advisor-"): Image.open(p).convert("RGBA")
          for p in sorted(src.glob("advisor-*.png"))}

# One crop box for every frame, so the character stays registered between
# states. Cropping each frame to its own bbox is what makes it jump.
boxes = [im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
         for im in frames.values()]
box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
       max(b[2] for b in boxes), max(b[3] for b in boxes))

for name, im in frames.items():
    cropped = im.crop(box)
    for height in (200, 400):
        width = round(cropped.width * height / cropped.height)
        cropped.resize((width, height), Image.LANCZOS).save(   # LANCZOS, not NEAREST
            out / f"advisor-{name}-{height}.png", optimize=True)
PY
```

Re-run this whenever a frame is added or redrawn: a new frame can widen the
union box, which changes every output and keeps them consistent. If you ever
need the box fixed across builds, hard-code it rather than deriving it.

### The component

```tsx
// Only these states have their own artwork; everything else uses the idle
// frame plus CSS. Record that here rather than scattering it through the JSX.
const FRAME: Record<AvatarState["kind"], string> = {
  idle: "idle", transcribing: "idle", confirming: "idle", retrieving: "idle",
  checking: "idle", speaking: "idle", error: "idle",
  webSource: "idle", unverified: "idle",   // TODO: own frames — see "the gap" above
  listening: "listening",
  notFound: "no-results",
  grounded: "verified-source",
};

// Swapping src on a frame the browser hasn't fetched yet shows a blank box for
// a beat. Four small PNGs are cheap; fetch them all once on mount.
function usePreloadedFrames() {
  useEffect(() => {
    for (const name of new Set(Object.values(FRAME))) {
      const img = new Image();
      img.src = `/avatar/advisor-${name}-200.png`;
    }
  }, []);
}

export function Advisor({ state, level }: { state: AvatarState; level: number }) {
  usePreloadedFrames();
  const frame = FRAME[state.kind];
  return (
    <div className={`advisor advisor--${state.kind}`}>
      {/* Empty alt + aria-hidden: decorative, the caption carries the state. */}
      <img
        src={`/avatar/advisor-${frame}-200.png`}
        srcSet={`/avatar/advisor-${frame}-200.png 1x, /avatar/advisor-${frame}-400.png 2x`}
        width={167}
        height={200}
        alt=""
        aria-hidden="true"
      />
      {state.kind === "speaking" && <AudioMeter level={level} />}
      <p className="advisor__caption" aria-live="polite">
        {state.caption}
      </p>
    </div>
  );
}
```

Six details that matter more than they look:

- **`width` and `height` are set explicitly** (167 × 200, from the shared crop
  box) so the browser reserves the space before the image loads and the layout
  doesn't jump when it arrives. Because every frame is the same size, this
  holds for all of them.
- **`Record<AvatarState["kind"], string>`** makes the compiler require an entry
  for every state — `AvatarState["kind"]` is an indexed access type, reading the
  union of `kind` values out of the state type. Add a twelfth state and this
  map fails to compile until you say which frame it uses, which is exactly
  where you want to be reminded.
- **Preload the frames.** Swapping `src` to an unfetched image leaves an empty
  box for a beat, and it will happen precisely at the moment the state changes
  — the moment the user is looking.
- **`alt=""` with `aria-hidden="true"`** is deliberate, and it's the correct
  pairing for decorative imagery: the character conveys no information that
  isn't in the caption, and a screen reader describing a cartoon at the user is
  noise. The `aria-live` caption is what gets announced.
- **The state class drives the ring, border, tint, and animation** — all the
  treatments in the table above are CSS on the wrapper, so adding a state never
  means touching the image.
- **Crossfade the frame swap** with a short opacity transition (~150 ms) rather
  than cutting. It covers the residual proportion differences between
  independently drawn frames, and a hard cut between two near-identical poses
  reads as a glitch.
- **Wrap every animation in `@media (prefers-reduced-motion: reduce)`** —
  including that crossfade — and fall back to an instant swap with the caption
  still updating.

### Where it sits on screen

Per doc 14 §6: left rail, with the answer, citations, and `[Source: ...]` links
in the main column. The character does not get a speech bubble, and the answer
text is never rendered inside or over it — a lawyer needs to read and copy that
text, and typography beats characterisation for content every time. Keep the
"hide avatar" control; the interface must remain complete without it.

## Open questions to settle with the lawyer

1. **What should the grounded-answer banner say?** "VERIFIED SOURCE" overstates
   what the pipeline checks, and the right replacement is a wording judgement a
   lawyer should make, not an engineer. "CITATIONS CHECKED" and "FROM YOUR
   DOCUMENTS" are the candidates.
2. Does the character make the tool feel more approachable, or less
   professional? Doc 14 §1 argues this is a real risk for a legal product and
   that the answer decides whether more artwork is worth commissioning. Show
   the four frames in a working client before spending further.
3. Is a chibi figure the right register for court-facing work, or would a
   smaller emblem — just the scales, animated — carry the same state
   information with less risk?
4. Provenance and licence of the artwork, per the section above.
