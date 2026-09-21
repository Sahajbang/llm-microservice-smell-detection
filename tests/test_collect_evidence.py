"""
Tests for the evidence-collection layer's own honesty guard.

Every detector depends on services that only come from
``discover_services`` (a top-level directory with a ``pom.xml`` or a
``build.gradle``/``build.gradle.kts``). If that finds nothing -- most often
because the repository uses a build tool the extractor does not recognize,
not because the repository has no smells -- an empty result must not look
identical to a clean, fully-analyzed one.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.extractors import collect_evidence
from tests.conftest import write_module


def test_no_services_discovered_is_recorded_as_an_error(tmp_path: Path):
    (tmp_path / "README.md").write_text("not a build tool this pipeline recognizes\n", encoding="utf-8")

    bundle = collect_evidence(tmp_path)

    assert bundle.services == []
    assert len(bundle.errors) == 1
    assert bundle.errors[0].component == "service_discovery"
    assert "No services discovered" in bundle.errors[0].message


def test_a_gradle_only_repository_is_not_treated_as_having_no_services(tmp_path: Path):
    module_dir = tmp_path / "CatalogSrvc"
    module_dir.mkdir()
    (module_dir / "build.gradle").write_text("", encoding="utf-8")
    resources = module_dir / "src" / "main" / "resources"
    resources.mkdir(parents=True)
    (resources / "application.properties").write_text(
        "spring.application.name=catalog-catalogservice\n", encoding="utf-8"
    )

    bundle = collect_evidence(tmp_path)

    assert bundle.services == ["catalog-catalogservice"]
    assert bundle.errors == []


def test_a_repository_with_real_services_records_no_discovery_error(cycle_repo: Path):
    """The warning must fire only on genuine absence, never alongside real findings."""
    bundle = collect_evidence(cycle_repo)
    assert bundle.services != []
    assert bundle.errors == []


def test_partial_discovery_is_not_flagged_as_zero(tmp_path: Path):
    """One recognizable module among unrelated directories is still a result,
    not the "nothing was recognized" case -- the two must not be conflated."""
    write_module(tmp_path, "order-mod", "order-service")
    (tmp_path / "some-frontend").mkdir()
    (tmp_path / "some-frontend" / "package.json").write_text("{}", encoding="utf-8")

    bundle = collect_evidence(tmp_path)

    assert bundle.services == ["order-service"]
    assert bundle.errors == []
