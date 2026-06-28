"""Corpus manifest loader + coverage report.

All fixtures are temporary (tmp_path); no real corpus files or external URLs.
DB writes still go exclusively through argus.actions (the orchestrator only
reads + coordinates), which is also enforced by
test_actions.py::test_writes_only_in_actions_module (now including corpus.py).
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from argus import actions, cli, corpus

COLUMNS = corpus.MANIFEST_COLUMNS


def _row(**kw) -> corpus.ManifestRow:
    base = {c: "" for c in COLUMNS}
    base["doc_id"] = "d"
    base["source_name"] = "S"
    base.update(kw)
    base.pop("line", None)
    return corpus.ManifestRow(**base)


def _write_manifest(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in COLUMNS})


def _corpus(tmp_path: Path, files: dict[str, str], rows: list[dict]):
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    for rel, content in files.items():
        target = raw / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest, rows)
    return manifest, raw


# ---- 1: parser handles required columns + optional blanks -------------------

def test_load_manifest_parses_required_and_blank_columns(tmp_path):
    manifest, _ = _corpus(tmp_path, {"a.txt": "hello"}, [
        {"doc_id": "d1", "source_name": "Src", "local_path": "a.txt"},
    ])
    rows = corpus.load_manifest(manifest)
    assert len(rows) == 1
    row = rows[0]
    assert row.doc_id == "d1" and row.source_name == "Src" and row.local_path == "a.txt"
    assert row.title == "" and row.language == "" and row.notes == ""  # blanks tolerated


def test_load_manifest_bad_header_raises(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("doc_id,source_name\nd1,Src\n", encoding="utf-8")  # no local_path
    with pytest.raises(ValueError):
        corpus.load_manifest(bad)


def test_load_manifest_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        corpus.load_manifest(tmp_path / "nope.csv")


# ---- 2: relative local_path resolves under raw root -------------------------

def test_resolve_local_path_relative_under_root(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    resolved = corpus.resolve_local_path(_row(local_path="sub/a.txt"), raw)
    assert resolved == (raw / "sub" / "a.txt").resolve()
    assert resolved.is_relative_to(raw.resolve())


def test_resolve_local_path_absolute_passthrough(tmp_path):
    abs_path = tmp_path / "x.txt"
    assert corpus.resolve_local_path(_row(local_path=str(abs_path)), tmp_path / "raw") == abs_path


def test_resolve_local_path_escape_raises(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    with pytest.raises(ValueError):
        corpus.resolve_local_path(_row(local_path="../escape.txt"), raw)


# ---- 3: missing files reported, load continues ------------------------------

def test_missing_file_reported_and_load_continues(tmp_path):
    manifest, raw = _corpus(tmp_path, {"present.txt": "hi"}, [
        {"doc_id": "d1", "source_name": "S", "local_path": "absent.txt"},
        {"doc_id": "d2", "source_name": "S", "local_path": "present.txt"},
    ])
    report = cli.load_manifest_corpus(manifest, raw)
    by = {r["doc_id"]: r for r in report["rows"]}
    assert by["d1"]["status"] == "missing_file"
    assert by["d2"]["status"] == "ingested"
    assert report["summary"]["missing_file"] == 1
    assert report["summary"]["ingested"] == 1


def test_missing_source_name_is_row_error(tmp_path):
    manifest, raw = _corpus(tmp_path, {"a.txt": "x"}, [
        {"doc_id": "d1", "source_name": "", "local_path": "a.txt"},
    ])
    report = cli.load_manifest_corpus(manifest, raw)
    assert report["rows"][0]["status"] == "error"
    assert "source_name" in report["rows"][0]["error"]


# ---- 4: unsupported extensions reported, not ingested -----------------------

def test_unsupported_type_reported(tmp_path):
    manifest, raw = _corpus(tmp_path, {"a.xyz": "data", "b.pdf": "%PDF-1.4 ..."}, [
        {"doc_id": "d1", "source_name": "S", "local_path": "a.xyz"},
        {"doc_id": "d2", "source_name": "S", "local_path": "b.pdf"},
    ])
    report = cli.load_manifest_corpus(manifest, raw)
    by = {r["doc_id"]: r for r in report["rows"]}
    assert by["d1"]["status"] == "unsupported_type"
    assert by["d2"]["status"] == "unsupported_type"
    assert by["d1"]["document_id"] is None and by["d2"]["document_id"] is None


# ---- 5: .txt ingests via actions; metadata stored ---------------------------

def test_txt_row_ingests_and_stores_metadata(tmp_path, db_conn):
    manifest, raw = _corpus(tmp_path, {"a.txt": "Some decision text."}, [
        {"doc_id": "d1", "title": "Decision A", "source_name": "DPA", "source_type": "regulator",
         "language": "en", "jurisdiction": "EU", "document_type": "enforcement_decision",
         "local_path": "a.txt", "topic_tags": "gdpr;fine", "notes": "hello"},
    ])
    report = cli.load_manifest_corpus(manifest, raw)
    row = report["rows"][0]
    assert row["status"] == "ingested"
    assert row["document_id"] and row["document_version_id"] and row["raw_hash"]

    with db_conn.cursor() as cur:
        cur.execute("select * from documents where id = %s", (row["document_id"],))
        doc = cur.fetchone()
        cur.execute("select * from document_versions where id = %s", (row["document_version_id"],))
        dv = cur.fetchone()
    assert doc["title"] == "Decision A"
    assert doc["language"] == "en" and doc["document_type"] == "enforcement_decision"
    assert doc["metadata"]["doc_id"] == "d1"
    assert doc["metadata"]["topic_tags"] == ["gdpr", "fine"]
    assert doc["metadata"]["notes"] == "hello"
    assert doc["metadata"]["manifest"]["jurisdiction"] == "EU"
    assert dv["raw_hash"] == row["raw_hash"]
    assert dv["extracted_text"] == "Some decision text."


def test_title_defaults_to_filename(tmp_path, db_conn):
    manifest, raw = _corpus(tmp_path, {"untitled.txt": "body"}, [
        {"doc_id": "d1", "source_name": "S", "local_path": "untitled.txt"},
    ])
    report = cli.load_manifest_corpus(manifest, raw)
    with db_conn.cursor() as cur:
        cur.execute("select title from documents where id = %s", (report["rows"][0]["document_id"],))
        assert cur.fetchone()["title"] == "untitled.txt"


# ---- HTML stripping ---------------------------------------------------------

def test_html_row_is_stripped_and_ingested(tmp_path, db_conn):
    html = ("<html><head><style>h1{color:red}</style></head><body>"
            "<h1>Title</h1><p>Hello &amp; world</p><script>bad()</script></body></html>")
    manifest, raw = _corpus(tmp_path, {"a.html": html}, [
        {"doc_id": "d1", "source_name": "S", "local_path": "a.html"},
    ])
    report = cli.load_manifest_corpus(manifest, raw)
    assert report["rows"][0]["status"] == "ingested"
    with db_conn.cursor() as cur:
        cur.execute("select extracted_text, extraction_method from document_versions where id = %s",
                    (report["rows"][0]["document_version_id"],))
        dv = cur.fetchone()
    assert "<" not in dv["extracted_text"]
    assert "Hello & world" in dv["extracted_text"]      # entity unescaped
    assert "bad()" not in dv["extracted_text"]          # <script> dropped
    assert "color:red" not in dv["extracted_text"]      # <style> dropped
    assert dv["extraction_method"] == "html_strip"


# ---- 6 & 7: duplicate handling ----------------------------------------------

def test_duplicate_skipped_by_default(tmp_path):
    content = "identical content"
    manifest, raw = _corpus(tmp_path, {"a.txt": content, "b.txt": content}, [
        {"doc_id": "d1", "source_name": "S", "local_path": "a.txt"},
        {"doc_id": "d2", "source_name": "S", "local_path": "b.txt"},
    ])
    report = cli.load_manifest_corpus(manifest, raw)
    by = {r["doc_id"]: r for r in report["rows"]}
    assert by["d1"]["status"] == "ingested"
    assert by["d2"]["status"] == "duplicate_skipped"
    assert by["d2"]["document_version_id"] == by["d1"]["document_version_id"]


def test_allow_duplicates_ingests_both(tmp_path, db_conn):
    content = "identical content"
    manifest, raw = _corpus(tmp_path, {"a.txt": content, "b.txt": content}, [
        {"doc_id": "d1", "source_name": "S", "local_path": "a.txt"},
        {"doc_id": "d2", "source_name": "S", "local_path": "b.txt"},
    ])
    report = cli.load_manifest_corpus(manifest, raw, allow_duplicates=True)
    by = {r["doc_id"]: r for r in report["rows"]}
    assert by["d1"]["status"] == "ingested" and by["d2"]["status"] == "ingested"
    assert by["d1"]["document_version_id"] != by["d2"]["document_version_id"]
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from document_versions where raw_hash = %s",
                    (by["d1"]["raw_hash"],))
        assert cur.fetchone()["n"] == 2


# ---- 8: dry-run creates nothing ---------------------------------------------

def test_dry_run_creates_nothing(tmp_path, db_conn):
    manifest, raw = _corpus(tmp_path, {"a.txt": "x"}, [
        {"doc_id": "d1", "source_name": "S", "local_path": "a.txt"},
    ])
    report = cli.load_manifest_corpus(manifest, raw, dry_run=True)
    assert report["rows"][0]["status"] == "dry_run"
    assert report["rows"][0]["raw_hash"]               # hash still computed
    assert report["rows"][0]["document_id"] is None
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from documents")
        assert cur.fetchone()["n"] == 0
        cur.execute("select count(*) as n from sources")
        assert cur.fetchone()["n"] == 0


# ---- 9: existing source reused ----------------------------------------------

def test_existing_source_is_reused(tmp_path, db_conn):
    actions.create_source("Reused DPA", "regulator")
    manifest, raw = _corpus(tmp_path, {"a.txt": "x", "b.txt": "y"}, [
        {"doc_id": "d1", "source_name": "Reused DPA", "local_path": "a.txt"},
        {"doc_id": "d2", "source_name": "Reused DPA", "local_path": "b.txt"},
    ])
    report = cli.load_manifest_corpus(manifest, raw)
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from sources")
        assert cur.fetchone()["n"] == 1  # no new source created
        cur.execute("select id from sources where name = %s", ("Reused DPA",))
        source_id = cur.fetchone()["id"]
    assert all(r["source_id"] == source_id for r in report["rows"])


# ---- 10: corpus-report counts -----------------------------------------------

def test_corpus_report_counts(tmp_path, db_conn):
    manifest, raw = _corpus(tmp_path, {"a.txt": "alpha", "b.txt": "beta", "c.txt": "alpha"}, [
        {"doc_id": "d1", "source_name": "A", "source_type": "regulator", "language": "en",
         "jurisdiction": "EU", "document_type": "decision", "local_path": "a.txt"},
        {"doc_id": "d2", "source_name": "A", "source_type": "regulator", "language": "fr",
         "jurisdiction": "FR", "document_type": "decision", "local_path": "b.txt"},
        {"doc_id": "d3", "source_name": "A", "source_type": "regulator", "language": "en",
         "jurisdiction": "EU", "document_type": "decision", "local_path": "c.txt"},  # dup of a.txt
    ])
    cli.load_manifest_corpus(manifest, raw)  # d3 is a duplicate -> skipped

    report = corpus.build_corpus_report(db_conn)
    assert report["total_documents"] == 2
    assert report["total_document_versions"] == 2
    assert report["unique_raw_hashes"] == 2
    assert report["duplicate_hash_groups"] == []
    assert report["documents_by_language"]["en"] == 1
    assert report["documents_by_language"]["fr"] == 1
    assert report["documents_by_jurisdiction"]["EU"] == 1
    assert report["documents_by_source"][0]["source_name"] == "A"
    assert report["empty_extracted_text"] == 0
    assert report["latest_fetched_at"] is not None

    # re-load with duplicates allowed -> a duplicate hash group now exists
    cli.load_manifest_corpus(manifest, raw, allow_duplicates=True)
    report2 = corpus.build_corpus_report(db_conn)
    assert report2["duplicate_hash_groups"], "expected a duplicate hash group after allow-duplicates"


# ---- init-corpus bootstrap (file plumbing, no DB) ---------------------------

def test_init_corpus_creates_raw_and_header_only_manifest(tmp_path):
    (tmp_path / "manifest.csv.example").write_text(
        ",".join(COLUMNS) + "\nx,Fake,Src,,,,,,,,raw/a.txt,,\n", encoding="utf-8"
    )
    info = cli.init_corpus(tmp_path)

    assert (tmp_path / "raw").is_dir()
    assert info["raw_created"] is True
    manifest = tmp_path / "manifest.csv"
    assert manifest.exists() and info["manifest_created"] is True

    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert lines == [",".join(COLUMNS)]  # header only, no fake rows
    assert corpus.load_manifest(manifest) == []  # loader accepts the empty manifest


def test_init_corpus_does_not_overwrite_existing_manifest(tmp_path):
    (tmp_path / "manifest.csv.example").write_text(",".join(COLUMNS) + "\n", encoding="utf-8")
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("doc_id,source_name,local_path\nmine,Me,a.txt\n", encoding="utf-8")

    info = cli.init_corpus(tmp_path)
    assert info["manifest_created"] is False
    assert info["manifest_existed"] is True
    assert manifest.read_text(encoding="utf-8") == "doc_id,source_name,local_path\nmine,Me,a.txt\n"


def test_init_corpus_falls_back_to_known_columns_without_example(tmp_path):
    info = cli.init_corpus(tmp_path)  # no manifest.csv.example present
    assert info["example_present"] is False
    assert (tmp_path / "manifest.csv").read_text(encoding="utf-8").splitlines() == [",".join(COLUMNS)]
