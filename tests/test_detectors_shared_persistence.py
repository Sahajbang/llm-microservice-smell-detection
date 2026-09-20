"""Shared Persistence detector tests.

Two rules are covered separately, because they carry very different
evidential weight: shared datasource (strong) and table-name overlap with
no resolvable datasource (weak, unproven). The false-positive guards --
in-memory databases, implicit entity names, independent databases -- get
their own tests, since the detector's credibility depends on them.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pipeline.config import SharedPersistenceConfig, default_config
from pipeline.detectors.shared_persistence import SharedPersistenceDetector
from pipeline.models import AnalysisContext, EvidenceBundle, PersistenceEvidence, RepositoryInfo


def datasource(service: str, identity: str, *, resolved: bool = True, in_memory: bool = False) -> PersistenceEvidence:
    return PersistenceEvidence(
        service=service,
        kind="datasource",
        value=identity,
        file=f"{service}/application.yml",
        line=4,
        evidence=f"url: {identity}",
        confidence=0.9 if resolved else 0.6,
        metadata={"identity": identity, "resolved": resolved, "in_memory": in_memory, "driver": "mysql"},
    )


def table(service: str, name: str, *, kind: str = "table", explicit: bool = True) -> PersistenceEvidence:
    return PersistenceEvidence(
        service=service,
        kind=kind,
        value=name,
        file=f"{service}/schema.sql",
        line=1,
        evidence=f"CREATE TABLE {name}",
        confidence=0.9,
        metadata={"table": name, "explicit": explicit},
    )


def make_ctx(records: list[PersistenceEvidence], tmp_path: Path, sp: SharedPersistenceConfig | None = None):
    config = default_config()
    if sp is not None:
        config = replace(config, shared_persistence=sp)
    return AnalysisContext(
        repo_root=tmp_path,
        repository=RepositoryInfo(source=str(tmp_path), kind="local", root=str(tmp_path), name="t"),
        evidence=EvidenceBundle(persistence=records),
        config=config,
        graph=None,
    )


# -- Rule 1: shared datasource ------------------------------------------------


def test_detects_two_services_on_one_database(tmp_path: Path):
    records = [
        datasource("orders-service", "mysql://db:3306/shop"),
        datasource("billing-service", "mysql://db:3306/shop"),
    ]
    findings = SharedPersistenceDetector().detect(make_ctx(records, tmp_path))

    assert len(findings) == 1
    assert findings[0].services == ["billing-service", "orders-service"]
    assert findings[0].metrics["rule"] == "shared_datasource"
    assert findings[0].severity == "MEDIUM"  # shared db, no observed table overlap


def test_shared_database_with_overlapping_tables_is_high_severity(tmp_path: Path):
    records = [
        datasource("orders-service", "mysql://db:3306/shop"),
        datasource("billing-service", "mysql://db:3306/shop"),
        table("orders-service", "orders"),
        table("billing-service", "orders"),
    ]
    finding = SharedPersistenceDetector().detect(make_ctx(records, tmp_path))[0]

    assert finding.severity == "HIGH"
    assert finding.metrics["shared_tables"] == ["orders"]


def test_detects_shared_schema(tmp_path: Path):
    records = [
        datasource("orders-service", "schema:shared_core"),
        datasource("billing-service", "schema:shared_core"),
    ]
    findings = SharedPersistenceDetector().detect(make_ctx(records, tmp_path))
    assert findings[0].metrics["identity"] == "schema:shared_core"


def test_independent_databases_are_not_flagged(tmp_path: Path):
    records = [
        datasource("orders-service", "mysql://db:3306/orders"),
        datasource("billing-service", "mysql://db:3306/billing"),
    ]
    assert SharedPersistenceDetector().detect(make_ctx(records, tmp_path)) == []


def test_single_service_on_a_database_is_not_sharing(tmp_path: Path):
    records = [datasource("orders-service", "mysql://db:3306/shop")]
    assert SharedPersistenceDetector().detect(make_ctx(records, tmp_path)) == []


def test_unresolved_placeholder_caps_confidence_and_is_disclosed(tmp_path: Path):
    records = [
        datasource("orders-service", "mysql://${DB_HOST}:3306/shop", resolved=False),
        datasource("billing-service", "mysql://${DB_HOST}:3306/shop", resolved=False),
    ]
    finding = SharedPersistenceDetector().detect(make_ctx(records, tmp_path))[0]

    assert finding.confidence == 0.6
    assert finding.metrics["fully_resolved"] is False
    assert "unresolved placeholder" in finding.description


def test_in_memory_databases_are_never_reported(tmp_path: Path):
    """Each JVM gets its own hsqldb:mem instance, so a name match is not sharing."""
    records = [
        datasource("orders-service", "hsqldb:mem:petclinic", in_memory=True),
        datasource("billing-service", "hsqldb:mem:petclinic", in_memory=True),
    ]
    assert SharedPersistenceDetector().detect(make_ctx(records, tmp_path)) == []


def test_three_services_sharing_one_database_is_one_finding(tmp_path: Path):
    records = [datasource(f"svc{i}", "mysql://db:3306/shop") for i in range(3)]
    findings = SharedPersistenceDetector().detect(make_ctx(records, tmp_path))

    assert len(findings) == 1
    assert findings[0].metrics["service_count"] == 3


# -- Rule 2: table overlap without datasource evidence ------------------------


def test_table_overlap_without_datasource_is_low_confidence(tmp_path: Path):
    records = [table("orders-service", "orders"), table("billing-service", "orders")]
    findings = SharedPersistenceDetector().detect(make_ctx(records, tmp_path))

    assert len(findings) == 1
    assert findings[0].metrics["rule"] == "table_overlap_without_datasource"
    assert findings[0].confidence == SharedPersistenceConfig().confidence_table_overlap_only
    assert findings[0].severity == "LOW"
    assert "unproven" in findings[0].description


def test_multiple_shared_tables_group_into_one_finding(tmp_path: Path):
    records = [
        table("orders-service", "orders"),
        table("orders-service", "order_lines"),
        table("billing-service", "orders"),
        table("billing-service", "order_lines"),
    ]
    findings = SharedPersistenceDetector().detect(make_ctx(records, tmp_path))

    assert len(findings) == 1, "one coupling, not one finding per table"
    assert findings[0].metrics["shared_tables"] == ["order_lines", "orders"]
    assert findings[0].severity == "MEDIUM"


def test_table_overlap_is_skipped_when_datasources_are_known_and_different(tmp_path: Path):
    """Same table names in provably different databases is not sharing."""
    records = [
        datasource("orders-service", "mysql://db:3306/orders"),
        datasource("billing-service", "mysql://db:3306/billing"),
        table("orders-service", "events"),
        table("billing-service", "events"),
    ]
    assert SharedPersistenceDetector().detect(make_ctx(records, tmp_path)) == []


def test_implicit_entity_names_do_not_trigger_table_overlap(tmp_path: Path):
    """@Entity without @Table(name=...) has a runtime-determined name."""
    records = [
        table("orders-service", "order", kind="entity", explicit=False),
        table("billing-service", "order", kind="entity", explicit=False),
    ]
    assert SharedPersistenceDetector().detect(make_ctx(records, tmp_path)) == []


def test_explicit_entity_tables_do_count_for_overlap(tmp_path: Path):
    records = [
        table("orders-service", "orders", kind="entity", explicit=True),
        table("billing-service", "orders", kind="entity", explicit=True),
    ]
    assert len(SharedPersistenceDetector().detect(make_ctx(records, tmp_path))) == 1


def test_table_overlap_rule_can_be_disabled(tmp_path: Path):
    records = [table("orders-service", "orders"), table("billing-service", "orders")]
    sp = SharedPersistenceConfig(report_table_overlap_without_datasource=False)
    assert SharedPersistenceDetector().detect(make_ctx(records, tmp_path, sp)) == []


def test_same_coupling_is_not_reported_by_both_rules(tmp_path: Path):
    records = [
        datasource("orders-service", "mysql://db:3306/shop"),
        datasource("billing-service", "mysql://db:3306/shop"),
        table("orders-service", "orders"),
        table("billing-service", "orders"),
    ]
    findings = SharedPersistenceDetector().detect(make_ctx(records, tmp_path))
    assert len(findings) == 1


# -- Degenerate input ---------------------------------------------------------


def test_no_persistence_evidence_produces_no_findings(tmp_path: Path):
    assert SharedPersistenceDetector().detect(make_ctx([], tmp_path)) == []


def test_findings_carry_evidence_and_files_for_the_llm_stage(tmp_path: Path):
    records = [
        datasource("orders-service", "mysql://db:3306/shop"),
        datasource("billing-service", "mysql://db:3306/shop"),
    ]
    finding = SharedPersistenceDetector().detect(make_ctx(records, tmp_path))[0]

    assert len(finding.evidence) == 2
    assert finding.files == ["billing-service/application.yml", "orders-service/application.yml"]
    assert finding.smell == "shared_persistence"
