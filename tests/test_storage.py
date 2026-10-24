"""The local blob store (docs/22 slice P0-B): keys stay inside the root,
puts are atomic replacements, deletes are per document or per matter."""

import pytest

from policy_advisor.storage import BlobKeyError, LocalBlobStore, document_key, sha256_of


def test_put_open_and_hash_round_trip(tmp_path):
    store = LocalBlobStore(tmp_path / "originals")
    source = tmp_path / "upload.pdf"
    source.write_bytes(b"%PDF-1.4 synthetic")
    info = store.put(document_key("matter-a", "upload.pdf"), source)
    assert info.size == len(b"%PDF-1.4 synthetic")
    assert info.sha256 == sha256_of(source)
    with store.open(info.key) as f:
        assert f.read() == b"%PDF-1.4 synthetic"
    assert store.exists(info.key)
    assert not (tmp_path / "originals" / "matter-a" / "upload.pdf.tmp").exists()


def test_put_replaces_a_previous_blob_under_the_same_key(tmp_path):
    store = LocalBlobStore(tmp_path / "originals")
    first = tmp_path / "v1.docx"
    first.write_bytes(b"one")
    second = tmp_path / "v2.docx"
    second.write_bytes(b"two-two")
    key = document_key("m", "contract.docx")
    store.put(key, first)
    info = store.put(key, second)
    assert info.size == 7
    with store.open(key) as f:
        assert f.read() == b"two-two"


def test_delete_and_delete_prefix(tmp_path):
    store = LocalBlobStore(tmp_path / "originals")
    source = tmp_path / "a.pdf"
    source.write_bytes(b"a")
    store.put(document_key("m1", "a.pdf"), source)
    store.put(document_key("m1", "b.pdf"), source)
    store.put(document_key("m2", "a.pdf"), source)
    store.delete(document_key("m1", "a.pdf"))
    assert not store.exists(document_key("m1", "a.pdf"))
    assert store.exists(document_key("m1", "b.pdf"))
    store.delete_prefix("m1/")
    assert not store.exists(document_key("m1", "b.pdf"))
    assert store.exists(document_key("m2", "a.pdf"))
    store.delete(document_key("m2", "missing.pdf"))  # idempotent


@pytest.mark.parametrize(
    "key", ["../etc/passwd", "/abs/path", "m//x", "m/.", "", "m/../other/x.pdf"]
)
def test_keys_that_escape_or_are_malformed_are_rejected(tmp_path, key):
    store = LocalBlobStore(tmp_path / "originals")
    with pytest.raises(BlobKeyError):
        store.exists(key)
