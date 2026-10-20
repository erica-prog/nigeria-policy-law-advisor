# 14 — The Cartoon Avatar Frontend

Last of three documents on the spoken interface.
[12-voice-web-app-architecture.md](12-voice-web-app-architecture.md) defined
the API, [13-voice-pipeline.md](13-voice-pipeline.md) covered speech in and
out. This one covers the browser client: the cartoon character, how its mouth
moves, and the TypeScript you'll meet building it.

TypeScript concepts are explained as they come up, per
[CLAUDE.md](../CLAUDE.md)'s audience note. The deeper background —
compile-time-only types, Zod as the `pydantic` equivalent, `catch (err)`
giving you `unknown` — is in
[05-typescript-guide.md](05-typescript-guide.md); this document assumes you've
skimmed it and focuses on the browser-specific parts that guide doesn't
cover.

## 1. The design risk, stated first

A cartoon character giving legal advice is a genuine product risk, not just a
matter of taste. Two specific mechanisms:

**It can read as a toy.** A practicing lawyer deciding whether to rely on this
tool for a filing is making a professional-risk judgement. A bouncing cartoon
is evidence, to them, that the thing was built for a demo. That's not an
unfair inference either — it's the impression most talking-avatar products
earn.

**Worse, it can miscalibrate trust upward.** This is the subtle one.
Anthropomorphised interfaces reliably make people more willing to accept what
they're told. This entire project is built around the opposite goal: answers
that are *checkable*, with a faithfulness check, a lower-trust label on web
results, and an explicit not-found path, precisely because the dangerous
failure isn't a wrong answer, it's a wrong answer the lawyer believed. A
likeable character that delivers an unverified citation in the same warm voice
as a well-grounded one is working directly against the pipeline behind it.

The resolution this document recommends throughout:

- **The avatar carries state, never content.** It shows what the system is
  doing and how much to trust the result. The answer and its citations render
  in sober, readable typography next to it — not in a speech bubble.
- **The avatar's expression is derived from `source` and `faithful`**, not
  chosen for charm. It must be visibly *not* confident when the backend isn't.
- **The avatar is dismissible.** A "hide the character" control that leaves a
  fully functional text-and-voice interface behind. If the lawyer finds it
  undignified, the product should survive that.

Build it if the lawyer wants it — warmth and approachability are real reasons
a non-technical user engages with a tool at all. Just don't let it become the
layer that decides how trustworthy an answer feels.

## 2. What the avatar is actually for

Doc 12's latency budget puts three to eleven seconds between the question and
the first audio. A spinner for eleven seconds reads as "broken." The avatar's
real job is making that interval legible — showing that retrieval, generation,
and citation-checking are separate pieces of work that are actually happening.

Bind each state to a fact the backend reported:

| Avatar state | Triggered by | What it must communicate |
|---|---|---|
| `idle` | No request in flight | Ready, not listening |
| `listening` | `MediaRecorder` is recording | Mic is live — unmistakably |
| `transcribing` | `/api/transcribe` in flight | Working on what you said |
| `confirming` | Transcript shown, awaiting confirmation | Your turn — check this (doc 13 §2) |
| `retrieving` | `/api/ask` in flight | Reading your documents |
| `checking` | Optional sub-state late in `/api/ask` | Verifying citations |
| `speaking` | Audio playing | Lip-sync active |
| `web-source` | `source === "web"` | Lower trust — not from your documents |
| `unverified` | `faithful === false` | A cited locator wasn't retrieved |
| `not-found` | `source === "none"` | Found nothing; not a failure, an honest answer |
| `error` | Non-2xx, timeout, or LLM failure | Something broke; try again |

Two rules make this honest rather than theatrical:

**No state without a backing fact.** Don't show `checking` for a fixed 800 ms
because the animation looks good. If `/api/ask` is a single round trip, you
know when it started and when it returned, and you can honestly show
`retrieving` for that whole window — but inventing sub-stages you can't
observe is a progress bar that lies. If you want genuine `retrieving` →
`checking` granularity, that's an argument for server-sent events from
`/api/ask` reporting real stage transitions, which is a deliberate addition,
not something to fake client-side.

**The three low-trust states must look different from each other and from
`idle`.** `web-source`, `unverified`, and `not-found` are distinct situations
with distinct remedies: check the website yourself, distrust that specific
citation, or rephrase the question. Collapsing them into one "uncertain"
expression loses the information.

## 3. Choosing the animation technology

| Approach | Fit here | Notes |
|---|---|---|
| **Rive** | Recommended | State machines are the core primitive. You set a named input from React (`state = "speaking"`, `mouthOpen = 0.6`) and the runtime handles transitions. Small `.riv` files, canvas-rendered |
| Lottie | Workable, awkward | After Effects export. Plays timelines; no state machine, so you orchestrate transitions yourself and lip-sync means seeking frames by hand |
| Sprite sheet + CSS | Fine for a first pass | A row of mouth shapes, swap by index. Zero dependencies, genuinely enough for a cartoon mouth. Ugly to extend |
| 3D (Three.js, Ready Player Me) | Overkill | Morph-target visemes and real blendshapes, at the cost of a 3D pipeline, bigger bundle, and a look that isn't "cartoon" |

**Use Rive.** The table in section 2 is a state machine; Rive's central
abstraction is a state machine with named inputs. Everything else makes you
build that mapping by hand. `@rive-app/react-canvas` exposes it as a
component plus hooks for setting inputs.

The practical cost to plan for is artwork: someone has to draw the character
with separated mouth shapes and author the state machine. That's a design
task, not an engineering one, and it's the long pole. A sprite-sheet
placeholder built in an afternoon is the right way to validate the whole
interaction before commissioning art.

## 4. Lip-sync

Two approaches, and which one you get is decided by the TTS choice in
[13-voice-pipeline.md](13-voice-pipeline.md) §3.

**Viseme events (if your provider supplies them).** Azure emits viseme IDs
with timestamps; ElevenLabs gives character-level timings you can map to
mouth shapes. You get a schedule of (time, shape) pairs, set a timer per
entry against playback position, and the mouth genuinely matches the
phonemes. Better fidelity, and it ties you to that provider.

**Amplitude-driven (works with anything, including `speechSynthesis`).** Read
the playing audio's loudness each animation frame and open the mouth
proportionally. It's not phonetically accurate — "ee" and "oh" look the same —
but for a cartoon at conversational speed it reads as talking, and it costs
nothing and works with every provider.

Start with amplitude. Here's the shape, with the TypeScript worth explaining:

```typescript
// AudioContext is the Web Audio entry point; AnalyserNode taps the signal
// as it passes through to the speakers without altering it.
const ctx = new AudioContext();
const source = ctx.createMediaElementSource(audioEl);
const analyser = ctx.createAnalyser();
analyser.fftSize = 256;
source.connect(analyser);
analyser.connect(ctx.destination); // without this, playback is silent

// Uint8Array is a typed array: a fixed-length, fixed-type buffer, closer to
// numpy's ndarray than to a Python list. Allocate it once, outside the loop —
// getByteTimeDomainData writes into it in place rather than returning a new one.
const samples = new Uint8Array(analyser.fftSize);

function frame(): void {
  analyser.getByteTimeDomainData(samples);
  // Samples centre on 128; distance from centre is amplitude.
  let peak = 0;
  for (const s of samples) peak = Math.max(peak, Math.abs(s - 128));
  setMouthOpen(Math.min(1, peak / 64)); // feeds the Rive input
  requestAnimationFrame(frame);         // ~60 fps, pauses in background tabs
}
requestAnimationFrame(frame);
```

Four things a Python developer should know about that snippet:

- **`requestAnimationFrame` is the browser's frame loop**, not a `while True`
  with a `sleep`. You hand it a callback, it calls you once before the next
  repaint, and you re-register each time. It stops when the tab is
  backgrounded, which is a feature.
- **`: void`** is the return-type annotation for "returns nothing" — TS's
  `-> None`.
- **Browsers require a user gesture before audio plays.** `AudioContext`
  starts suspended until a click or keypress. Since the interaction here
  begins with the lawyer pressing a mic button, that's satisfied naturally —
  but an avatar that greets the user with speech on page load will silently
  fail to make a sound.
- **Smooth the value before using it.** Raw per-frame peaks jitter and the
  mouth flutters. A simple exponential smoothing
  (`next = prev * 0.7 + target * 0.3`) is enough.

## 5. The TypeScript you'll actually meet

Beyond [05-typescript-guide.md](05-typescript-guide.md), four patterns come up
in this client specifically.

### Discriminated unions for the answer

The `source` field takes three values and each implies a different render
path. TypeScript can enforce that you handled all three:

```typescript
type Answer =
  | { source: "corpus"; answer: string; faithful: boolean;
      unsupportedCitations: string[]; retrieved: Chunk[] }
  | { source: "web"; answer: string; webCitations: WebCitation[] }
  | { source: "none"; answer: string };
```

A **discriminated union** is a union of object types sharing one literal
field (`source`) that identifies which member you have. Inside
`if (a.source === "web")`, the compiler knows `a.webCitations` exists and that
`a.retrieved` does not. Python's closest equivalent is a tagged
`Union[...]` checked with `match`, except here the checking is real and
happens at compile time.

The payoff is specific: you cannot forget to handle the web case, because
accessing `a.retrieved` without narrowing is a compile error. Given that the
trust distinction *is* the product (doc 13 §5), having the type system refuse
to let you render all three identically is worth the modelling effort.

### Zod at the HTTP boundary

Same rule as [05-typescript-guide.md](05-typescript-guide.md#zod-runtime-validation--your-pydantic-equivalent),
applied in the other direction: TS types are erased at runtime, so
`await res.json()` gives you `any` and a type annotation on it is a promise
you're making to the compiler, not a check. Parse the response through a Zod
schema so a backend contract change surfaces as a validation error at the
boundary instead of `undefined` reaching the avatar's state machine.

### `null` handling and refs

```typescript
const audioRef = useRef<HTMLAudioElement | null>(null);
// ...
audioRef.current?.play();   // optional chaining: no-op if null
```

`HTMLAudioElement | null` is TS making explicit what Python leaves implicit:
the element doesn't exist until React has mounted it. `?.` is optional
chaining — call it if non-null, otherwise evaluate to `undefined`. Resist
`!` (the non-null assertion, "trust me, it's there"); it silences the
compiler without making the value non-null, which is how you get a runtime
`Cannot read properties of null`.

`useRef` is also the answer to a question that trips up Python developers in
React: where do long-lived objects live? A React component function re-runs on
every render, so a `const ctx = new AudioContext()` in the body creates a new
one each time. `useRef` gives you a stable box that survives re-renders —
that's where the `AudioContext`, `MediaRecorder`, and Rive instance belong.

### Timeouts on `fetch`

`fetch` has no timeout by default. It will wait indefinitely, which for a
`/api/ask` call that can legitimately take six seconds means you can't tell
slow from hung:

```typescript
const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), 30_000);
try {
  const res = await fetch("/api/ask", { signal: controller.signal, /* ... */ });
} finally {
  clearTimeout(timer);
}
```

This is the browser-side half of
[06-production.md](06-production.md#error-handling)'s "timeouts everywhere an
external call happens" — the backend already sets
`default_request_timeout=30` on the Claude client
([chain.py](../src/policy_advisor/generation/chain.py) line 55), and the
client needs its own. `30_000` uses numeric separators for readability; it's
just `30000`.

## 6. Layout: the avatar is not the interface

A rough division of the screen that keeps content primary:

```
┌──────────────────────────────────────────────────────────────┐
│  Matter: smith-v-acme-2026    Language: English    [Log out]  │
├───────────────────┬──────────────────────────────────────────┤
│                   │  Transcript (editable before sending)     │
│     ┌───────┐     │  ─────────────────────────────────────    │
│     │ avatar │     │  Answer text, with [Source: ...] markers  │
│     │  🙂    │     │  kept intact and rendered as links        │
│     └───────┘     │                                           │
│   state caption   │  ▸ Sources (3)                            │
│                   │    Order 5 Rule 2 — FHC Rules, p.14        │
│   [ 🎤 hold ]      │    Order 5 Rule 3 — FHC Rules, p.15        │
│   [ ⌨ type ]      │                                           │
│   [ hide avatar ] │  Research aid only — not a substitute for  │
│                   │  your own legal judgment.                  │
└───────────────────┴──────────────────────────────────────────┘
```

Load-bearing details:

- **The state caption next to the avatar is text**, not just an expression.
  "Reading your documents" alongside the animation means the state survives
  for a user who doesn't read cartoon faces the way you intended — and it's
  what a screen reader announces (section 7).
- **`[Source: ...]` markers stay in the on-screen answer.** Only the spoken
  copy is stripped (doc 13 §3). Rendering them as links that expand the
  cited passage from `retrieved` is the single highest-value thing this UI
  does for a lawyer: it turns a claim into something checkable in one click.
- **The persistent disclaimer lives here**, mirroring
  [app.py](../src/policy_advisor/app.py) lines 28–31, because voice can only
  deliver it once per session without becoming noise.
- **The web-source case replaces the answer area with a visually distinct
  block**, the way the bordered container does today
  ([app.py](../src/policy_advisor/app.py) lines 155–160). Don't reduce it to a
  small icon next to normal-looking text.

## 7. Accessibility, and the open-plan office problem

Voice-first is not voice-only, and for this user the reasons are practical
before they're ethical.

**A lawyer often cannot speak the question aloud.** Shared offices, open-plan
floors, chambers, a client in the room, a court corridor — dictating the facts
of a live matter is a confidentiality problem in most of the places this tool
would actually be used. This is the same concern as doc 13's data-handling
argument, arriving through the room instead of the network.

So: **the typed path is a peer, not a fallback.** Full keyboard operation, a
text input that's always present rather than hidden behind a toggle, and no
feature reachable only by speaking. A mute control that stops the avatar
speaking while keeping the interface working is table stakes, and it should be
findable in one click when someone walks in.

The rest of the checklist:

- **Captions always.** Everything spoken appears as text simultaneously. This
  is also what makes the interface usable for a deaf or hard-of-hearing
  lawyer, and what makes answers copy-pasteable into a brief.
- **`prefers-reduced-motion`.** Respect it: hold a static avatar, skip idle
  animation, keep only the mouth movement (or drop that too). It's a CSS
  media query and a matching check in the Rive state machine.
- **Never colour alone for trust state.** The web-source and unverified
  states need an icon and a text label, not just an amber tint — the
  information is too important to lose to colour blindness or a bad monitor.
- **The avatar is decorative to assistive tech.** Mark the canvas
  `aria-hidden="true"` and put the real state in an `aria-live="polite"`
  region containing the caption text. A screen reader user gets "Reading your
  documents" announced; they don't get a canvas described at them.
- **The mic button needs an unmistakable recording indicator** that doesn't
  depend on the avatar animation — a lawyer must never be unsure whether the
  microphone is live.

## 8. What to build first

Order matters here because the expensive parts (artwork, cloud TTS) should
come after you know the interaction is worth having:

1. **A text-only React client** against the doc 12 API: login, matter
   selector, typed question, answer with `[Source: ...]` links and a sources
   panel. No voice, no avatar. This is already a better product surface than
   the Streamlit demo for the question-answering path, and it de-risks
   everything else.
2. **Add push-to-talk and captions** — `MediaRecorder` to `/api/transcribe`,
   transcript confirmation, `/api/speak` via browser `speechSynthesis`.
   Interaction complete, still no character on screen.
3. **Add a placeholder avatar**: a sprite sheet with four mouth shapes and the
   state table from section 2 driving a static expression. Enough to judge
   whether the cartoon helps or undercuts the tool.
4. **Show it to the lawyer at step 3** and ask directly whether the character
   makes the tool feel more approachable or less professional. That answer
   decides whether step 5 happens at all.
5. **Commission real artwork and a Rive state machine**, and only then
   consider viseme-accurate lip-sync with a cloud TTS.

Steps 1–3 are days of work each. Step 5 is the one with a design budget
attached, and section 1's risk is real enough that it should be spent on
evidence rather than on assumption.

Back to [06-production.md](06-production.md) for the reliability checklist all
three of these documents feed into — in particular the per-user cost ceiling,
which doc 12 argues voice turns from a planned improvement into a
prerequisite.
