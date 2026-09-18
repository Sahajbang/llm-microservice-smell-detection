"""Tests for the shared Step 4/5 code-context formatting helpers."""

from pipeline.code_context import format_evidence_block, read_snippet


def test_read_snippet_returns_numbered_context(tmp_path):
    f = tmp_path / "A.java"
    f.write_text("\n".join(f"line{i}" for i in range(1, 21)), encoding="utf-8")

    snippet = read_snippet(f, line=10, context=2)

    assert "    8: line8" in snippet
    assert "   10: line10" in snippet
    assert "   12: line12" in snippet
    assert "line7" not in snippet
    assert "line13" not in snippet


def test_read_snippet_missing_file_returns_empty(tmp_path):
    assert read_snippet(tmp_path / "nope.java", line=1) == ""


def test_format_evidence_block_includes_all_records(tmp_path):
    evidence = [
        {
            "caller": "visits-service",
            "callee": "customers-service",
            "source": "resttemplate",
            "file": "does-not-exist.java",
            "line": 0,
            "evidence": '"http://customers-service/owners/-/pets/{petId}"',
        }
    ]

    block = format_evidence_block(evidence, tmp_path)

    assert "visits-service -> customers-service" in block
    assert "resttemplate" in block
    assert "http://customers-service/owners/-/pets/{petId}" in block
