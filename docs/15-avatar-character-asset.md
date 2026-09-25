# 15 — The Advisor Character: Asset and Usage

The pixel-style advisor character that represents the agent in the web
client. This document covers what the asset actually is, what it can and
can't do today, and how the frontend consumes it.

> **Note on links:** this file references docs 12–14 (the voice/avatar
> architecture set), which exist in the local working copy but are **not yet
> committed to this repository** — `docs/` is untracked on `main`. The links
> below resolve once those files are pushed.
>
> - [12-voice-web-app-architecture.md](12-voice-web-app-architecture.md) — API boundary
> - [13-voice-pipeline.md](13-voice-pipeline.md) — speech in/out
> - [14-cartoon-avatar-frontend.md](14-cartoon-avatar-frontend.md) — the avatar state machine this asset plugs into

## The asset

| | |
|---|---|
| Path | `assets/avatar/advisor-source.png` |
| Dimensions | 1254 × 1254, RGBA |
| Background | Fully transparent (alpha 0 at all corners) |
| Content bounds | 827 × 1163 within the canvas — the remainder is transparent margin, unevenly distributed |
| Subject | Full-body chibi advocate in a black suit, holding brass scales of justice in the right hand and a leather case file under the left arm |

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

## What one static pose can and can't do

[14-cartoon-avatar-frontend.md](14-cartoon-avatar-frontend.md) specifies an
avatar bound to eleven interaction states and a mouth driven by audio
amplitude. This asset is **one pose**, and it has two structural limits:

- **No mouth frames.** At the intended display size the mouth is a few pixels
  wide. There is nothing to animate and nothing to swap.
- **Both hands are occupied** — scales in one, case file under the other. Poses
  that want a free hand (a raised finger for "listening", an open palm for
  "I found nothing") need the character redrawn, not just reposed.

So the honest position is: **use it now as the idle/portrait state, and drive
every other state through layers around the character rather than changes to
it.** Which turns out to be fine, because the states that matter most are
trust states, and trust states read better as explicit labels than as facial
expressions anyway.

### Driving state with one pose

Everything in this table is CSS and DOM around a static `<img>`. No second
artwork file required.

| State | Treatment with the single pose |
|---|---|
| `idle` | Character at rest, full opacity, caption "Ready" |
| `listening` | Amber pulsing ring around the avatar + a clearly separate mic indicator; caption "Listening" |
| `transcribing` | Ring switches to a travelling gradient; caption "Working out what you said" |
| `confirming` | Ring stops, character dims slightly to push focus to the editable transcript; caption "Check this before I answer" |
| `retrieving` | Slow bob (2–3 px, ~1.5 s); caption "Reading your documents" |
| `checking` | Bob continues, caption changes to "Checking citations" — only if the backend actually reports the stage (doc 14 §2) |
| `speaking` | Amplitude-driven equalizer bars beside the character (see below); caption "Speaking" |
| `web-source` | Amber border + warning icon + caption "From an official website — not your documents" |
| `unverified` | Red border + caption naming the unsupported locator |
| `not-found` | Character at reduced opacity, neutral caption "I didn't find this in your documents" |
| `error` | Greyscale filter, caption "Something went wrong — try again" |

Two rules carried over from doc 14 that this asset makes concrete:

- **Don't fake the mouth.** With no mouth frames, the amplitude signal from the
  `AnalyserNode` should drive something that *is* honest about being an
  indicator — equalizer bars, a pulsing ring, a waveform strip — rather than a
  crude mouth rectangle pasted over the character's face. A bad lip-sync reads
  as broken; a clean audio meter reads as deliberate.
- **The caption is not optional.** It's the accessible state (announced via
  `aria-live` while the image itself is `aria-hidden`), and with one static
  pose it's carrying most of the information anyway.

## The frame inventory, when you commission the animation set

If the character earns its place (doc 14 §8 recommends validating that with the
lawyer before spending an art budget), this is the list to commission. Keep the
same palette and silhouette.

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
assets/avatar/
  advisor-source.png        # 1254², committed, the master
web/public/avatar/          # generated at build time, gitignored
  advisor-200.png           # 142 × 200 — 1x display
  advisor-400.png           # 284 × 400 — 2x for retina
```

The number in the filename is the **height**. The cropped figure is 827 × 1163,
an aspect ratio of 0.711, so the width follows from it rather than the other way
round.

Generating them (Pillow is already a transitive dependency via `pdf2image`):

```bash
python3 - <<'PY'
from PIL import Image
im = Image.open("assets/avatar/advisor-source.png").convert("RGBA")
im = im.crop(im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox())
for height in (200, 400):
    width = round(im.width * height / im.height)
    out = im.resize((width, height), Image.LANCZOS)   # LANCZOS, not NEAREST
    out.save(f"web/public/avatar/advisor-{height}.png", optimize=True)
PY
```

The crop trims the transparent margin so the character fills its box
predictably, and the width is derived from the height so the aspect ratio is
preserved — the figure is noticeably taller than it is wide, so sizing it as a
square would letterbox it.

### The component

```tsx
// AvatarState is the discriminated union from doc 14 §5 — the compiler
// enforces that every state has a caption and a visual treatment.
export function Advisor({ state, mouthOpen }: { state: AvatarState; mouthOpen: number }) {
  return (
    <div className={`advisor advisor--${state.kind}`}>
      {/* Empty alt + aria-hidden: decorative, the caption carries the state. */}
      <img
        src="/avatar/advisor-200.png"
        srcSet="/avatar/advisor-200.png 1x, /avatar/advisor-400.png 2x"
        width={142}
        height={200}
        alt=""
        aria-hidden="true"
      />
      {state.kind === "speaking" && <AudioMeter level={mouthOpen} />}
      <p className="advisor__caption" aria-live="polite">
        {state.caption}
      </p>
    </div>
  );
}
```

Four details that matter more than they look:

- **`width` and `height` are set explicitly** (142 × 200, from the cropped
  aspect ratio) so the browser reserves the space before the image loads and
  the layout doesn't jump when it arrives.
- **`alt=""` with `aria-hidden="true"`** is deliberate, and it's the correct
  pairing for decorative imagery: the character conveys no information that
  isn't in the caption, and a screen reader describing a cartoon at the user is
  noise. The `aria-live` caption is what gets announced.
- **The state class drives the ring, border, tint, and animation** — all the
  treatments in the table above are CSS on the wrapper, so adding a state never
  means touching the image.
- **Wrap every animation in `@media (prefers-reduced-motion: reduce)`** and
  fall back to the static pose with the caption still updating.

### Where it sits on screen

Per doc 14 §6: left rail, with the answer, citations, and `[Source: ...]` links
in the main column. The character does not get a speech bubble, and the answer
text is never rendered inside or over it — a lawyer needs to read and copy that
text, and typography beats characterisation for content every time. Keep the
"hide avatar" control; the interface must remain complete without it.

## Open questions to settle with the lawyer

1. Does the character make the tool feel more approachable, or less
   professional? Doc 14 §1 argues this is a real risk for a legal product and
   that the answer decides whether the animation budget is spent at all. Show
   the static pose in a working client before commissioning frames.
2. Is a chibi figure the right register for court-facing work, or would a
   smaller emblem — just the scales, animated — carry the same state
   information with less risk?
3. Provenance and licence of the artwork, per the section above.
