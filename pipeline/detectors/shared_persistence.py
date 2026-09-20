"""
Shared Persistence detector.

Flags services that appear to read or write the same database, schema, or
tables, using the evidence collected by
:mod:`pipeline.extractors.persistence`.

Scope note: unlike Cyclic Dependency and Hub-like Dependency, this smell is
NOT part of the PRD's stated scope (which limits itself to two smells). It
is implemented because shared persistence is a standard microservice
anti-pattern and is statically observable; that provenance is recorded here
and in README.md rather than being presented as a PRD requirement.

Two rules, deliberately separated by strength of evidence
---------------------------------------------------------
**Rule 1 -- shared datasource (primary).** Two or more services resolve to
the same database identity (driver + host + port + database name, or an
explicit shared schema name). This is the strong signal: it is the actual
connection configuration.

  * Confidence is the minimum confidence across contributing records, so a
    single unresolved ``${DB_HOST}`` placeholder caps the whole finding.
  * Severity is HIGH when the sharing services *also* declare overlapping
    table names (they demonstrably touch the same data), MEDIUM when they
    share a database but no observed table overlap (still a deployment and
    migration coupling, but each may own its own tables).

**Rule 2 -- table-name overlap without datasource evidence (secondary,
low confidence).** Two or more services declare the same explicit table
name and neither's datasource could be resolved from the repository at all.
This is suggestive, not proof: identically named tables in genuinely
separate databases are common (``users``, ``events``). It is reported at
low confidence, flagged as unproven in its description, disabled by a
single config switch, and never raised above MEDIUM severity. Services
already covered by a Rule 1 finding are excluded so the same coupling is
not reported twice.

False positives explicitly prevented
------------------------------------
* In-memory databases (``jdbc:hsqldb:mem:``, ``jdbc:h2:mem:``) are skipped
  entirely: each JVM gets a private instance, so a matching name is not
  shared storage.
* ``@Entity`` classes without an explicit ``@Table(name = ...)`` are
  excluded from table-overlap reasoning, because their effective table
  name depends on Hibernate's runtime naming strategy.
* Evidence from ``src/test/resources`` is never collected (see the
  extractor), so a shared in-test H2 cannot trigger a finding.

What the LLM is asked to validate
---------------------------------
Whether the configuration genuinely implies shared storage at runtime --
profiles, per-environment overrides, externalized config -- and whether the
sharing is a real coupling problem or, for example, a single service's own
database that another module only reads during a migration.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from pipeline.detectors.base import EVIDENCE_PERSISTENCE, SmellDetector
from pipeline.models import AnalysisContext, Finding, PersistenceEvidence
from pipeline.smells import SHARED_PERSISTENCE, get_spec


class SharedPersistenceDetector(SmellDetector):
    smell = SHARED_PERSISTENCE
    required_evidence = (EVIDENCE_PERSISTENCE,)

    def detect(self, ctx: AnalysisContext) -> list[Finding]:
        cfg = ctx.config.shared_persistence
        records = ctx.evidence.persistence
        if not records:
            return []

        findings = self._datasource_findings(records, cfg)
        covered = {frozenset(f.services) for f in findings}
        if cfg.report_table_overlap_without_datasource:
            findings.extend(self._table_overlap_findings(records, cfg, covered))
        return findings

    # -- Rule 1: shared datasource -----------------------------------------

    def _datasource_findings(
        self, records: list[PersistenceEvidence], cfg: Any
    ) -> list[Finding]:
        spec = get_spec(self.smell)
        by_identity: dict[str, list[PersistenceEvidence]] = defaultdict(list)
        for record in records:
            if record.kind != "datasource":
                continue
            if record.metadata.get("in_memory"):
                continue  # private per JVM; a matching name is not shared storage
            by_identity[record.value].append(record)

        tables_by_service = self._explicit_tables_by_service(records)
        findings: list[Finding] = []

        for identity, group in sorted(by_identity.items()):
            services = sorted({r.service for r in group})
            if len(services) < cfg.min_services_sharing:
                continue

            shared_tables = self._overlapping_tables(services, tables_by_service)
            resolved = all(r.metadata.get("resolved", True) for r in group)
            confidence = min(r.confidence for r in group)

            findings.append(
                Finding(
                    smell=self.smell,
                    smell_name=spec.name,
                    key=f"{self.smell}:datasource:{identity}",
                    services=services,
                    files=sorted({r.file for r in group}),
                    severity="HIGH" if shared_tables else "MEDIUM",
                    confidence=round(confidence, 3),
                    description=(
                        f"{len(services)} services are configured against the same database "
                        f"identity '{identity}': {', '.join(services)}."
                        + (
                            f" They also declare {len(shared_tables)} table name(s) in common: "
                            f"{', '.join(sorted(shared_tables))}."
                            if shared_tables
                            else " No overlapping table declarations were observed, so the sharing"
                            " may be at database level only."
                        )
                        + (
                            ""
                            if resolved
                            else " At least one datasource URL contains an unresolved placeholder,"
                            " so this is a configuration-name match rather than a proven"
                            " same-instance match."
                        )
                    ),
                    evidence=[r.to_dict() for r in group],
                    metrics={
                        "rule": "shared_datasource",
                        "identity": identity,
                        "service_count": len(services),
                        "shared_tables": sorted(shared_tables),
                        "fully_resolved": resolved,
                        "drivers": sorted({r.metadata.get("driver") for r in group if r.metadata.get("driver")}),
                    },
                )
            )
        return findings

    # -- Rule 2: table-name overlap only -----------------------------------

    def _table_overlap_findings(
        self,
        records: list[PersistenceEvidence],
        cfg: Any,
        covered: set[frozenset[str]],
    ) -> list[Finding]:
        spec = get_spec(self.smell)
        # Services whose datasource is known and shareable are handled by
        # Rule 1; only services with no usable datasource evidence at all
        # qualify for this weaker signal.
        services_with_datasource = {
            r.service
            for r in records
            if r.kind == "datasource" and not r.metadata.get("in_memory")
        }
        tables_by_service = self._explicit_tables_by_service(records)
        by_table: dict[str, set[str]] = defaultdict(set)
        for service, tables in tables_by_service.items():
            if service in services_with_datasource:
                continue
            for table in tables:
                by_table[table].add(service)

        # Group by the exact set of services sharing tables, so two services
        # sharing five tables produce one finding, not five.
        by_service_set: dict[frozenset[str], set[str]] = defaultdict(set)
        for table, services in by_table.items():
            if len(services) >= cfg.min_services_sharing:
                by_service_set[frozenset(services)].add(table)

        findings: list[Finding] = []
        for service_set, tables in sorted(by_service_set.items(), key=lambda kv: sorted(kv[0])):
            if service_set in covered:
                continue
            services = sorted(service_set)
            evidence = [
                r.to_dict()
                for r in records
                if r.service in service_set and self._is_explicit_table(r) and r.value in tables
            ]
            findings.append(
                Finding(
                    smell=self.smell,
                    smell_name=spec.name,
                    key=f"{self.smell}:tables:{'-'.join(services)}",
                    services=services,
                    files=sorted({e["file"] for e in evidence}),
                    severity="MEDIUM" if len(tables) > 1 else "LOW",
                    confidence=round(cfg.confidence_table_overlap_only, 3),
                    description=(
                        f"{', '.join(services)} each declare the table name(s) "
                        f"{', '.join(sorted(tables))}, and no datasource configuration for these "
                        "services could be resolved from the repository. This is an unproven "
                        "signal: identically named tables may belong to entirely separate "
                        "databases."
                    ),
                    evidence=evidence,
                    metrics={
                        "rule": "table_overlap_without_datasource",
                        "shared_tables": sorted(tables),
                        "service_count": len(services),
                        "fully_resolved": False,
                    },
                )
            )
        return findings

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _is_explicit_table(record: PersistenceEvidence) -> bool:
        """Table declarations solid enough to compare across services.

        Excludes ``@Entity`` classes with no explicit ``@Table(name = ...)``,
        whose real table name is decided by Hibernate at runtime.
        """
        if record.kind == "table":
            return True
        return record.kind == "entity" and bool(record.metadata.get("explicit"))

    @classmethod
    def _explicit_tables_by_service(
        cls, records: Iterable[PersistenceEvidence]
    ) -> dict[str, set[str]]:
        tables: dict[str, set[str]] = defaultdict(set)
        for record in records:
            if cls._is_explicit_table(record):
                tables[record.service].add(record.value)
        return tables

    @staticmethod
    def _overlapping_tables(services: list[str], tables_by_service: dict[str, set[str]]) -> set[str]:
        seen: dict[str, set[str]] = defaultdict(set)
        for service in services:
            for table in tables_by_service.get(service, set()):
                seen[table].add(service)
        return {table for table, owners in seen.items() if len(owners) > 1}
