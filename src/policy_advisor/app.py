"""Streamlit demo (docs/07): chat UI showing the answer plus its real sources,
latency/token cost, and an honest 'not found' path rather than a guess.

Extended for CLAUDE-2.md capability 1: a sidebar lets the lawyer pick or
create a matter (a private, scoped corpus - see HybridRetriever) and add or
remove documents within it, instead of being limited to the fixed Phase 1
demo corpus.

Run with: uv run streamlit run src/policy_advisor/app.py
"""

import tempfile
import time
from pathlib import Path

import streamlit as st

from policy_advisor.auth import require_login
from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.generation.advisory import AdvisoryChain
from policy_advisor.generation.advisory_models import AdvisoryResult
from policy_advisor.generation.case_reasoning import CaseReasoningChain
from policy_advisor.generation.chain import RAGChain
from policy_advisor.ingestion.chunk import SUPPORTED_SUFFIXES
from policy_advisor.ingestion.ingest_document import UnsupportedDocumentError, add_document, remove_document
from policy_advisor.ingestion.matter_store import list_documents, list_matters_for_user, set_matter_owner

st.set_page_config(page_title="Law & Policy Advisor (Demo)", page_icon="⚖️")
st.title("Law & Policy Advisor")
st.caption(
    "Research aid only, not a substitute for your own legal judgment. "
    "Answers are grounded only in the documents added to the selected matter below."
)

username, display_name = require_login()

JURISDICTION_LABELS = {"Any": None, "Federal High Court": "federal", "Lagos Magistrates' Courts": "lagos"}
CONVERSATION_LANGUAGE_LABELS = {"English": "en", "French": "fr"}


@st.cache_resource
def get_chain() -> RAGChain:
    return RAGChain()


@st.cache_resource
def get_case_chain() -> CaseReasoningChain:
    return CaseReasoningChain()


@st.cache_resource
def get_advisory_chain() -> AdvisoryChain:
    # Shares the case chain, and through it the retriever, rather than loading
    # a second embedding model and BM25 cache into the same process.
    return AdvisoryChain(case_chain=get_case_chain())


with st.sidebar:
    st.caption(f"Logged in as **{display_name}**")
    st.header("Matter")
    matters = list_matters_for_user(username)
    matter_id = st.selectbox("Select a matter", matters, index=matters.index(PHASE1_DEMO_MATTER_ID) if PHASE1_DEMO_MATTER_ID in matters else 0)
    is_shared_demo_matter = matter_id == PHASE1_DEMO_MATTER_ID
    if is_shared_demo_matter:
        st.caption("Shared reference matter - visible to everyone, read-only.")

    new_matter_id = st.text_input("...or create a new matter", placeholder="e.g. smith-v-acme-2026")
    if st.button("Create matter") and new_matter_id.strip():
        matter_id = new_matter_id.strip()
        set_matter_owner(matter_id, username)
        is_shared_demo_matter = False
        st.success(f"New matter '{matter_id}' will be created once you add a document below.")

    # Conversational-language preference (CLAUDE-2.md capability 3) - asked
    # explicitly per matter, never inferred silently, and shown again above
    # the chat area below so it's never ambiguous which language an answer
    # was generated in versus the underlying document's own language.
    st.divider()
    conversation_language_label = st.selectbox(
        "Converse in", list(CONVERSATION_LANGUAGE_LABELS.keys()), key=f"conv_lang_{matter_id}"
    )
    conversation_language = CONVERSATION_LANGUAGE_LABELS[conversation_language_label]

    st.divider()
    st.subheader(f"Documents in '{matter_id}'")
    for doc_name in list_documents(matter_id):
        col1, col2 = st.columns([4, 1])
        col1.text(doc_name)
        if not is_shared_demo_matter and col2.button("Remove", key=f"remove-{doc_name}"):
            remove_document(matter_id, doc_name)
            get_chain().invalidate_matter(matter_id)
            st.rerun()

    st.divider()
    if is_shared_demo_matter:
        st.caption("This shared matter is read-only - create your own matter to add documents.")
    else:
        st.subheader("Add a document")
        uploaded_file = st.file_uploader("PDF or DOCX", type=[s.lstrip(".") for s in SUPPORTED_SUFFIXES])
        upload_jurisdiction_label = st.selectbox("Jurisdiction (optional)", list(JURISDICTION_LABELS.keys()), key="upload_jurisdiction")
        if uploaded_file is not None and st.button("Ingest document"):
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir) / uploaded_file.name
                tmp_path.write_bytes(uploaded_file.getvalue())
                try:
                    with st.spinner(f"Ingesting {uploaded_file.name}..."):
                        chunk_count = add_document(
                            matter_id, tmp_path, jurisdiction=JURISDICTION_LABELS[upload_jurisdiction_label]
                        )
                    get_chain().invalidate_matter(matter_id)
                    st.success(f"Added {uploaded_file.name} ({chunk_count} chunks).")
                    st.rerun()
                except UnsupportedDocumentError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Ingestion failed: {exc}")

jurisdiction_label = st.selectbox("Jurisdiction filter", list(JURISDICTION_LABELS.keys()))
jurisdiction = JURISDICTION_LABELS[jurisdiction_label]

st.caption(f"Conversing in **{conversation_language_label}** - this is the language Claude will respond in, "
           "independent of whichever language each retrieved document is actually written in.")

allow_web_fallback = st.checkbox(
    "Allow searching official sources if my documents don't have this",
    value=False,
    help="Used in every mode, and only when nothing relevant is found in this matter's own "
    "documents. Restricted to a small allowlist of official domains, and always shown separately "
    "from your documents' answers - never checked the same way as a citation from something you "
    "uploaded.",
)

mode = st.radio("Mode", ["Ask a question", "Analyze a case", "Advise on a case"], horizontal=True)

if mode == "Ask a question":
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Ask a question about the documents in this matter...")
    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            start = time.monotonic()
            chain = get_chain()
            result = chain.answer(
                question,
                matter_id=matter_id,
                jurisdiction=jurisdiction,
                conversation_language=conversation_language,
                allow_web_fallback=allow_web_fallback,
            )
            latency_s = time.monotonic() - start

            if result.source == "web":
                # Visually distinct from a normal answer bubble, on purpose -
                # this never went through the corpus faithfulness check, so
                # it must never look as trustworthy as one that did.
                with st.container(border=True):
                    st.caption("⚠️ From an official source on the web - not verified against this matter's documents.")
                    st.markdown(result.answer)
                    if result.web_citations:
                        for citation in result.web_citations:
                            st.markdown(f"- [{citation.title}]({citation.url})")
            else:
                st.markdown(result.answer)

            if not result.faithful and result.source == "corpus":
                st.warning(
                    "Citation check failed - this answer referenced locators not found in the "
                    f"retrieved passages: {', '.join(result.unsupported_citations)}. Treat with caution."
                )

            if result.retrieved:
                with st.expander(f"Sources ({len(result.retrieved)})"):
                    for chunk in result.retrieved:
                        meta = chunk.metadata
                        st.markdown(
                            f"**{meta['locator']}** — {meta['doc_type']}, {meta['jurisdiction']}, "
                            f"{meta['source_document']} (p. {meta['page']})"
                        )
                        st.text(chunk.text)

            input_tokens = result.usage.get("input_tokens", 0)
            output_tokens = result.usage.get("output_tokens", 0)
            st.caption(f"Latency: {latency_s:.2f}s · Tokens: {input_tokens} in / {output_tokens} out")

        st.session_state.messages.append({"role": "assistant", "content": result.answer})

else:
    def render_case_analysis(result) -> None:
        for issue in result.issues:
            st.subheader(issue.issue)
            badge = "⚠️ unverified" if issue.unverified else issue.confidence
            st.caption(f"Confidence: {badge}")

            if issue.from_web:
                # Same treatment as a web-sourced answer in the chat mode: this
                # never went through the corpus faithfulness check, so it must
                # not sit in the page looking like an issue that did.
                with st.container(border=True):
                    st.caption("⚠️ No authority in this matter covers this issue - answered from an official website.")
                    st.markdown(issue.web_summary or "")
                    for citation in issue.web_sources:
                        st.markdown(f"- [{citation.title}]({citation.url})")
            else:
                for argument in issue.arguments:
                    cites = ", ".join(a.locator for a in argument.supporting_authorities) or "no authority cited"
                    st.markdown(f"**{argument.side}:** {argument.summary}")
                    st.caption(f"Cites: {cites}")

            st.markdown(f"_Assessment:_ {issue.assessment}")
            st.divider()

        st.subheader("Overall position")
        st.markdown(result.overall_position)
        st.warning(result.disclaimer)

    advisory_mode = mode == "Advise on a case"

    if advisory_mode:
        st.info(
            "Gives a direct recommendation and a view on which way the authorities point, on top of "
            "the issue-by-issue analysis. It has not been shown how any comparable case was actually "
            "decided - this matter holds authorities, not outcomes - so the direction is a reading of "
            "those authorities to test, not a forecast. Takes longer than plain analysis: it runs the "
            "full analysis first, then reasons over it."
        )
    else:
        st.info(
            "Identifies the legal issues in the facts below and assesses how strongly each side's "
            "position is supported by the documents in this matter - this is not a prediction of any "
            "court's actual decision. Each case can take a few minutes: this runs many checked reasoning "
            "steps per issue rather than one pass, by design."
        )

    case_facts = st.text_area("Case facts", height=180, placeholder="Describe the facts of the case...")
    button_label = "Advise on case" if advisory_mode else "Analyze case"

    if st.button(button_label) and case_facts.strip():
        spinner_text = "Analyzing - this checks its own citations at every step, so it takes a few minutes..."
        advice: AdvisoryResult | None = None
        with st.spinner(spinner_text):
            if advisory_mode:
                advice = get_advisory_chain().advise(
                    case_facts,
                    matter_id=matter_id,
                    jurisdiction=jurisdiction,
                    conversation_language=conversation_language,
                    allow_web_fallback=allow_web_fallback,
                )
                analysis = advice.case_analysis
            else:
                analysis = get_case_chain().analyze(
                    case_facts,
                    matter_id=matter_id,
                    jurisdiction=jurisdiction,
                    conversation_language=conversation_language,
                    allow_web_fallback=allow_web_fallback,
                )

        if advice is not None:
            st.subheader("Recommended position")
            st.markdown(advice.recommended_position)

            st.subheader(f"Which way the authorities point: {advice.outcome_direction}")
            st.markdown(advice.likely_outcome)
            # The basis is shown next to the band, never the band alone - the
            # word "moderate" on its own is exactly the kind of unearned
            # reassurance this pipeline exists to avoid.
            st.caption(f"Confidence: **{advice.outcome_confidence}** - {advice.confidence_basis}")

            if advice.unsupported_citations:
                st.error(
                    "This advice cited authorities that no issue analysis established: "
                    f"{', '.join(advice.unsupported_citations)}. Treat those as unverified."
                )
            if advice.judge_flagged:
                st.warning(f"Faithfulness check flagged this advice: {advice.judge_notes}")

            st.markdown(f"**What it turns on:** {advice.turns_on}")

            if advice.next_steps:
                st.subheader("Next steps")
                for step in advice.next_steps:
                    st.markdown(f"- **{step.action}** — {step.rationale}")

            if advice.key_authorities:
                st.subheader("Authorities to rely on")
                for authority in advice.key_authorities:
                    st.markdown(f"- **{authority.locator}** — {authority.why}")

            st.warning(advice.disclaimer)
            st.divider()
            with st.expander("The issue-by-issue analysis this rests on"):
                render_case_analysis(analysis)
        else:
            render_case_analysis(analysis)
