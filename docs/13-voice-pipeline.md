# 13 — The Voice Pipeline (Speech In, Speech Out)

Second of three documents on the spoken interface. [12-voice-web-app-architecture.md](12-voice-web-app-architecture.md)
covered the client/server boundary; this one covers what happens inside
`POST /api/transcribe` and `POST /api/speak`, and the failure modes voice
introduces that a text chat doesn't have.

The short version of this document: **speech is not a skin over the text
interface.** It changes where client-confidential data travels, it breaks the
citation format the answer is written in, and it removes the visual channel
the product currently uses to say "don't trust this one as much." Each of
those needs a deliberate answer.

## 1. Speech-to-text: the confidentiality decision comes first

The fastest way to get voice input working is four lines of browser
JavaScript using the Web Speech API (`SpeechRecognition`). Do not reach for
it without understanding what it does: in Chrome, that API streams the
microphone audio to Google's speech servers for recognition. It is not
on-device.

For this product, that means a lawyer dictating the facts of a live matter
sends audio describing a client's situation to a third party that is not
covered by whatever engagement terms or professional-privilege obligations
the lawyer is operating under. That is not an engineering trade-off you can
make on their behalf — it's a confidentiality question with a professional
sanction on the wrong end of it. The same logic the project already applies
to web search (restricted, vetted, clearly labelled) applies here.

| Option | Audio goes to | Latency | Notes |
|---|---|---|---|
| Browser `SpeechRecognition` | Google's servers | Fast (~0.5 s) | Chrome-centric, no self-hosting, no control. Dev-only shortcut at best |
| `faster-whisper` on your server | Nowhere (your infra) | 0.5–3 s by model size | Recommended. CTranslate2 backend, CPU-viable for `small` |
| Transformers Whisper via HF | Nowhere (your infra) | Slower than `faster-whisper` | Fits the existing HF dependency; heavier at runtime |
| Cloud STT (Deepgram, AssemblyAI, OpenAI) | That vendor | Fast, accurate | Only with a reviewed data-processing agreement, and a per-request cost |

**Recommendation: server-side Whisper.** Hugging Face is already in the stack
for embeddings ([02-environment-setup.md](02-environment-setup.md)), so
self-hosted speech models are a smaller addition than they look, and the
audio never leaves the machine that already holds the documents. If you want
browser `SpeechRecognition` during early development, label it in the UI as
such and gate it behind `STT_BACKEND` from doc 12 so it cannot be the default
in production by accident.

### Picking a Whisper size

`tiny` and `base` are tempting on CPU and are the wrong choice here. The
whole reason legal transcription is hard is rare proper nouns and numbered
references, which is exactly what small models lose first. `small` is a
reasonable CPU floor; `medium` or `large-v3` if you have a GPU. Measure on
your own audio rather than trusting published word-error rates — the
benchmarks are dominated by read speech in accents that may not match your
user.

Which raises the point worth being explicit about: **Whisper's training data
under-represents Nigerian English**, and this corpus and its users are
Nigerian. Expect measurably worse accuracy than the published numbers, test
with the actual lawyer's voice before concluding a model size is adequate, and
treat the transcript-confirmation step below as load-bearing rather than
optional.

## 2. Misheard legal language is the biggest new failure mode

A text interface fails visibly: the lawyer sees their own typo. A voice
interface fails invisibly — the system confidently answers a question that
wasn't asked, and the answer is well-cited and faithful to the documents, so
every quality check in the pipeline passes. Nothing in
[faithfulness.py](../src/policy_advisor/generation/faithfulness.py) or the
golden-set evaluation in [06-production.md](06-production.md#evaluation)
detects a correct answer to the wrong question.

What specifically gets mangled:

- **Numbered locators.** "Order five rule two sub one" is the spoken form of
  `Order 5 Rule 2(1)`. Whisper may render the numerals as words, drop the
  sub-rule, or hear "order" as ordinary English.
- **Case names.** "Meta v NDPC" is an initialism plus a Latin abbreviation
  read aloud as "versus" — expect "Meta versus NDPC", "Meta v and DPC",
  "meta the NDPC".
- **Court and institution names.** Federal High Court, Lagos State
  Magistrates' Courts, National Industrial Court.
- **Terms of art** that have common-English homophones: "praecipe",
  "ex parte", "interlocutory", "originating summons".

### The concrete consequence in this codebase

`HybridRetriever` has a deliberate short-circuit for exact locator queries —
a lawyer asking for "Order 5 Rule 3" gets that rule by metadata lookup
instead of fuzzy search, because rules rarely restate their own number in
their own body text
([hybrid_retriever.py](../src/policy_advisor/retrieval/hybrid_retriever.py)
lines 74–89). It fires off this pattern:

```python
_LOCATOR_RE = re.compile(r"order\s+(\d+)\D{1,5}?rule\s+(\d+)", re.IGNORECASE)
```

`(\d+)` requires digits. A transcript reading "Order five, Rule three" does
not match, the short-circuit silently doesn't fire, and the query degrades to
whatever BM25 and vector search rank highest for the words "order" and
"rule" — the exact failure the short-circuit exists to prevent. The lawyer
gets a plausible answer about the wrong rule and no indication that anything
was skipped.

So a **spoken-numeral normalisation step** between transcription and
retrieval is not polish, it's required for the retrieval behaviour the
codebase already relies on. Normalise number words to digits and common
spoken forms to written ones ("sub one" → "(1)", "versus" → "v") before the
text reaches `chain.answer()`. Whether you extend `_LOCATOR_RE` to also
accept number words or normalise upstream is a judgement call; normalising
upstream is cleaner because the same transcript is also what you show the
lawyer and what goes into the prompt.

### Always show the transcript, and let it be corrected

This is the mitigation that actually works, and it's why doc 12 recommends
push-to-talk: a discrete recording action gives you a natural moment to
display what was heard before committing to it.

- Render the transcript in the input box, editable, before the question is
  sent.
- If your STT backend exposes confidence or `no_speech_prob`, treat a
  low-confidence transcript as a prompt to re-record rather than a question
  to answer.
- Never auto-send a transcript the lawyer hasn't seen. An extra half-second
  of friction is cheap next to a confidently answered wrong question.
- Log the raw transcript alongside the normalised one, so "why did it answer
  that?" (the debugging goal from [06-production.md](06-production.md#observability))
  stays answerable when the cause was mishearing rather than retrieval.

## 3. Text-to-speech: choose on whether you get timing data

Any TTS will produce audio. The thing that differentiates them for this
project is whether they also hand back **timing metadata** — visemes (mouth
shapes) or word/character timestamps — because that's what drives lip-sync in
[14-cartoon-avatar-frontend.md](14-cartoon-avatar-frontend.md). Without it,
the avatar's mouth has to be driven from raw audio amplitude, which is
workable but cruder.

| Option | Timing data | Answer text goes to | Notes |
|---|---|---|---|
| Browser `speechSynthesis` | `boundary` events (word-level, uneven support) | Nowhere — on-device | Free, private, zero setup. Voice quality varies by OS; French voices may be absent |
| Azure Speech | Viseme events (purpose-built for lip-sync) | Microsoft | Best fit if lip-sync fidelity matters |
| ElevenLabs | Character-level timestamps | ElevenLabs | Most natural-sounding; timestamps convertible to visemes |
| Google Cloud TTS | SSML `<mark>` timepoints | Google | Timing requires marking up the text first |
| Piper / Coqui (self-hosted) | None directly | Nowhere — your infra | Private, fast, robotic; pair with amplitude-driven mouth |

**The privacy question applies on the way out too**, and it's easy to miss.
The answer text isn't generic — it quotes retrieved passages from documents
in the lawyer's matter. Sending it to a cloud TTS vendor sends client
document content to that vendor. If you accepted self-hosted STT for
confidentiality reasons, sending the answer to ElevenLabs undoes the
reasoning.

The consistent positions are: **browser `speechSynthesis` or self-hosted
Piper** (nothing leaves, accept amplitude-driven lip-sync and a plainer
voice), or **a cloud provider under a reviewed data-processing agreement**
(better voice and real visemes, with a documented decision behind it). Start
with `speechSynthesis` — it costs nothing to try, and doc 14's
amplitude-driven mouth works with it.

### The answer text is not speakable as-is

This one will bite immediately. The system prompt requires every claim to
carry a literal citation marker
([prompt.py](../src/policy_advisor/generation/prompt.py) lines 22–23):

```
1. Every substantive claim must cite the exact locator of the passage it came from,
in the form [Source: <locator>] (e.g. [Source: Order 5 Rule 3] or [Source: Paragraph 3]).
```

So `result.answer` contains strings like
`[Source: Order 5 Rule 2(1)]`. Piped into a synthesiser, a lawyer hears
"bracket Source colon Order five Rule two paren one bracket" in the middle of
every sentence. You need a **speech normalisation pass** over the answer
before synthesis:

- Strip or rewrite the `[Source: ...]` markers. `LOCATOR_RE` in
  [faithfulness.py](../src/policy_advisor/generation/faithfulness.py) line 8
  already matches exactly this pattern — reuse it rather than writing a
  second regex that can drift out of sync with the prompt.
- Expand what remains into speakable form: `Order 5 Rule 2(1)` →
  "Order 5, Rule 2, sub-rule 1". This is the inverse of the input
  normalisation in section 2, and the two should be tested against each other
  — spoken form in, spoken form out, same locator.
- Don't read long quoted passages aloud. A synthesiser reciting two
  paragraphs of statutory text is unusable. Summarise audibly, show the full
  passage on screen.

**Critically: normalise only the copy sent to `/api/speak`.** The on-screen
text keeps its `[Source: ...]` markers, because those are the verifiable
record — the thing that makes the answer checkable rather than merely
plausible, per [00-overview.md](00-overview.md). Two representations of one
answer: one for the ear, one for the record. Never let the spoken version
become the only one.

### Match the voice to the conversation language

The lawyer picks a conversation language explicitly and it flows through to
generation as `response_language` (`LANGUAGE_NAMES = {"en": ..., "fr": ...}`
in [prompt.py](../src/policy_advisor/generation/prompt.py) line 12, selected
in [app.py](../src/policy_advisor/app.py) lines 70–73). `/api/speak` must
receive that same value and select a matching voice, or you get an English
voice reading French text — which is both unpleasant and, for a tool whose
selling point is that language handling is explicit and never inferred,
an obvious inconsistency.

Two details: a locator is never translated (the prompt says so explicitly),
so a French answer contains English locator text — your speech normalisation
has to handle a mixed-language string. And browser `speechSynthesis` voice
availability is OS-dependent; if no French voice is installed, fail visibly
with an offer to show text rather than reading French through an English
voice.

## 4. Streaming vs. the faithfulness check

This is the central design tension of the whole voice interface, and it has
a correct answer.

Conversational voice assistants feel fast because they stream: tokens go
straight into a synthesiser and speech starts before generation finishes.
That option is not available here, because the citation check runs on the
**complete** answer ([chain.py](../src/policy_advisor/generation/chain.py)
line 123):

```python
faithful, unsupported = check_faithfulness(response.content, retrieved)
```

`check_faithfulness` extracts every cited locator and verifies it appeared
among the retrieved chunks. It cannot run on a partial answer — a locator
that looks unsupported mid-stream may be a chunk that hasn't been cited yet,
and more importantly the fabricated citation you're trying to catch could
appear in the last sentence. If you start speaking at token one, you have
committed to reading out an answer you have not yet checked.

The options, honestly stated:

1. **Speak only after the check passes.** Costs the full generation latency
   (2–6 s per doc 12's budget) before any audio. Never speaks an
   unverified citation.
2. **Stream and interrupt on failure.** Saves the wait, at the cost of
   sometimes cutting the avatar off mid-sentence to say "actually, disregard
   that." For a lawyer who may have already written the citation down, a
   retraction is worse than a pause — and in the failure mode this is meant
   to catch, the fabricated citation has already been spoken aloud.
3. **Stream only the non-citation prose.** Requires knowing which clauses
   carry citations before generation completes. Not reliably possible.

**Take option 1.** Cover the wait with avatar state that shows real work —
"listening", "reading the rules", "checking citations" — rather than a
spinner (doc 14). The wait is the product being careful, and showing it
honestly is better than hiding it behind speech you might have to retract.

### What to do when the check fails

Note that a failed check doesn't suppress the answer today: `RAGChain` returns
it with `faithful=False` and `unsupported_citations` populated, and the
Streamlit UI shows a warning above it
([app.py](../src/policy_advisor/app.py) lines 164–168). Voice needs its own
decision here, and "read it out with a warning first" is the defensible one:

> "One caution before I answer: this response cites Order 9 Rule 5, which
> wasn't in the passages I found. Treat that citation as unverified."

Spoken **before** the answer, not after — a warning that arrives once the
lawyer has already heard and noted the citation has done nothing. Pair it
with the visual warning and a distinct avatar state.

## 5. Trust signalling has to survive in audio

The Streamlit app carries trust information visually: a bordered container, a
⚠️ caption, a separate link list for web sources
([app.py](../src/policy_advisor/app.py) lines 150–168). Audio has no border
and no colour. If the voice client speaks every `AnswerResult` in the same
neutral tone, it has flattened three materially different epistemic states
into one, which is precisely the failure
[11-web-search-and-usage-limits.md](11-web-search-and-usage-limits.md) warns
about when it explains why web results are deliberately kept out of the
faithfulness-checked path.

So every response state needs an audible signature, driven off `source` and
`faithful`:

| State | Spoken form | Also required |
|---|---|---|
| `source="corpus"`, `faithful=True` | Answer directly, no preamble | Citation panel on screen |
| `source="corpus"`, `faithful=False` | Unverified-citation caution **first**, then the answer | Visual warning, distinct avatar state |
| `source="web"` | "I couldn't find this in your documents. From the National Assembly website, which I haven't checked against your matter…" | Visually distinct block + clickable source links, per today's rendering |
| `source="none"` | Speak the not-found message plainly; offer to rephrase | No avatar "confident" state |

Three rules that make this hold up:

- **The preamble is mandatory, not stylistic.** A web-sourced answer that
  starts speaking without it is indistinguishable from a grounded one.
  Generate it from `source` server-side if you want it guaranteed, rather
  than trusting the client to prepend it.
- **Speak the not-found message.** `NOT_FOUND_MESSAGE` and
  `NOT_FOUND_EVEN_ON_WEB_MESSAGE` ([chain.py](../src/policy_advisor/generation/chain.py)
  lines 18–26) are the product's honesty mechanism. Silence, a shrug
  animation, or a chirpy "let me think about that differently" all undercut
  it. Read it out.
- **Never let the cartoon's tone override the content.** A friendly voice
  delivering an unverified citation in the same cheerful cadence as a
  well-grounded answer is actively misleading. Doc 14 handles the visual half
  of this; the audio half is prosody and preamble.

### The disclaimer

The Streamlit app shows a persistent caption — "Research aid only, not a
substitute for your own legal judgment"
([app.py](../src/policy_advisor/app.py) lines 28–31) — and
[06-production.md](06-production.md#reliability-extras-worth-planning-for)
treats that as a liability requirement, not decoration. Voice makes it
awkward: spoken before every answer it becomes noise the user learns to
ignore, which is worse than not saying it.

Workable split: speak it once, on the first answer of a session, and keep it
permanently visible on screen next to the avatar. The audio channel carries it
when it will actually be heard; the visual channel carries it continuously.

## 6. Audio retention: default to keeping nothing

Recordings of a lawyer describing a live matter are more sensitive than the
query logs [06-production.md](06-production.md#observability) already flags
for redaction and retention limits. They're voice-identifiable, they may
capture names and facts the lawyer wouldn't have typed, and they may capture
background conversation the lawyer didn't intend to record.

- **Don't persist audio.** Accept the upload, transcribe it in memory or in a
  `tempfile` you delete in a `finally` block, return the transcript. If you
  need sample audio to tune the STT model, collect it deliberately and with
  consent, not as a side effect of normal use.
- **Don't write synthesised audio to a durable cache** keyed by answer text
  without thinking about it — that cache is a store of answers about client
  matters.
- **Transcripts are query text** and inherit the existing rule: they may
  contain client-sensitive detail, so decide retention deliberately rather
  than logging them forever by default.
- **Say what you do.** A lawyer needs to be able to answer "is this
  recording me?" for their own client. "Audio is transcribed and discarded,
  never stored" is a sentence worth being able to say truthfully and put in
  the UI.

## What to build first

1. `POST /api/transcribe` with server-side Whisper `small`, plus the
   transcript-confirmation step in the client. Nothing else works reliably
   without these two together.
2. Spoken-numeral normalisation, tested against `_LOCATOR_RE` so the exact
   locator short-circuit actually fires on dictated rule numbers.
3. `POST /api/speak` with browser `speechSynthesis` and the `[Source: ...]`
   stripping pass. Cheap, private, and enough to evaluate whether the whole
   interaction is worth continuing.
4. The preamble/warning logic from section 5, derived server-side from
   `source` and `faithful`.
5. Only then consider a cloud TTS with real visemes, if lip-sync fidelity
   turns out to matter to the lawyer more than the data-sharing costs.

Next: [14-cartoon-avatar-frontend.md](14-cartoon-avatar-frontend.md)
