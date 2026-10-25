"""Artefacts through the API (docs/35)."""

import io
from dataclasses import dataclass

from docx import Document

from policy_advisor import tenancy
from policy_advisor.api.credentials import create_user
from policy_advisor.artefacts.draft import DraftResult
from policy_advisor.artefacts.outline import Outline, Paragraph, Section
from policy_advisor.artefacts.provenance import build_manifest
from policy_advisor.artefacts.render import render_docx
from policy_advisor.artefacts.store import save_artefact


@dataclass
class FakeDraftingAgent:
    blob_store: object
    calls: list = None

    def draft(
        self, instruction, *, matter_id, username, kind, template_id, jurisdiction, allow_web
    ):
        self.calls = (self.calls or []) + [
            {"instruction": instruction, "matter_id": matter_id, "kind": kind, "username": username}
        ]
        outline = Outline(
            title="Fake memo",
            sections=[
                Section(
                    heading="Law", paragraphs=[Paragraph(text="Rule.", sources=["Order 1 Rule 1"])]
                )
            ],
        )
        manifest = build_manifest(outline, [], {})
        import tempfile
        from pathlib import Path

        key = f"{matter_id}/artefacts/fake.docx"
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as handle:
            handle.write(render_docx(outline, manifest))
            path = Path(handle.name)
        self.blob_store.put(key, path)
        record = save_artefact(
            matter_id=matter_id,
            kind=kind,
            title=outline.title,
            instruction=instruction,
            outline=outline.model_dump(),
            manifest=manifest.as_dict(),
            docx_key=key,
            pptx_key=None,
            template_id=template_id or "memo-default",
            run_id="run-x",
            created_by=username,
        )
        return DraftResult(artefact=record, outline=outline, manifest=manifest)


def test_draft_list_read_and_download(api_with_llm):
    services = api_with_llm.services
    services._drafting_agent = FakeDraftingAgent(blob_store=services.blob_store)
    alice = api_with_llm.login("alice")
    matter = alice.post("/api/matters", json={}).json()
    assert [t["template_id"] for t in alice.get("/api/templates").json()["templates"]] == [
        "memo-default",
        "slides-default",
    ]

    created = alice.post(
        f"/api/matters/{matter['id']}/artefacts",
        json={"instruction": "Memo on late defences for the partner.", "kind": "memo"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (
        body["status"] == "draft"
        and body["has_docx"]
        and not body["has_pptx"]
        and body["created_by"] == "alice"
    )
    assert body["manifest"]["counts"] == {"unverified": 1}
    assert services._drafting_agent.calls[0]["matter_id"] == matter["id"]

    listed = alice.get(f"/api/matters/{matter['id']}/artefacts").json()["artefacts"]
    assert [a["artefact_id"] for a in listed] == [body["artefact_id"]]
    one = alice.get(f"/api/matters/{matter['id']}/artefacts/{body['artefact_id']}").json()
    assert one["outline"]["title"] == "Fake memo"
    download = alice.get(
        f"/api/matters/{matter['id']}/artefacts/{body['artefact_id']}/file?format=docx"
    )
    assert download.status_code == 200 and "wordprocessingml" in download.headers["content-type"]
    assert "Fake memo" in "\n".join(
        p.text for p in Document(io.BytesIO(download.content)).paragraphs
    )
    assert (
        alice.get(
            f"/api/matters/{matter['id']}/artefacts/{body['artefact_id']}/file?format=pptx"
        ).status_code
        == 404
    )
    assert (
        alice.post(
            f"/api/matters/{matter['id']}/artefacts",
            json={"instruction": "Memo please, long enough.", "template_id": "nope"},
        ).status_code
        == 404
    )

    # Another user, same firm, no share: nothing visible. A reviewer with a share reads but cannot draft.
    bob = api_with_llm.login("bob")
    assert bob.get(f"/api/matters/{matter['id']}/artefacts").status_code == 404
    create_user("rita", "Rita", "rita-pass-123", rounds=4)
    tenancy.add_user_to_tenant("rita", "default", "reviewer")
    alice.post(f"/api/matters/{matter['id']}/share", json={"username": "rita"})
    rita = api_with_llm.login("rita", "rita-pass-123")
    assert rita.get(f"/api/matters/{matter['id']}/artefacts").status_code == 200
    assert (
        rita.post(
            f"/api/matters/{matter['id']}/artefacts",
            json={"instruction": "Memo please, long enough."},
        ).status_code
        == 403
    )
