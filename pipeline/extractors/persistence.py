"""
Persistence evidence collector (data layer).

Collects the static facts needed to reason about Shared Persistence: which
database each service is configured to talk to, and which tables it
declares. Like the REST collector this is regex/string matching, not
semantic analysis, and it is deliberately explicit about what it cannot
establish.

What is collected
-----------------
``datasource``  JDBC/R2DBC URLs found in each module's
                ``src/main/resources`` configuration (``application*.yml``,
                ``*.yaml``, ``*.properties``), and in ``docker-compose``
                service environment blocks. Each URL is parsed into a
                normalized database identity (driver, host, port, database)
                used to compare services.
``entity``      JPA ``@Table(name = "...")`` declarations, and ``@Entity``
                classes without an explicit table name (recorded at low
                confidence, since the effective table name then depends on
                Hibernate's naming strategy).
``table``       ``CREATE TABLE`` statements in SQL schema files and in
                Flyway migrations, and ``createTable`` entries in Liquibase
                changelogs.

What static analysis CANNOT establish (and is therefore never claimed)
---------------------------------------------------------------------
* Whether two identical database names are the same physical instance.
  Host/port are frequently placeholders (``${DB_HOST}``) or differ per
  environment; such identities are marked ``resolved: False`` and carried
  at reduced confidence.
* Configuration that never appears in the repository at all. Spring Cloud
  Config, Kubernetes secrets and plain environment variables are resolved
  at runtime; a repository that externalizes its datasource config (the
  Phase 1 target repo does exactly this) yields no datasource evidence,
  and the absence is reported as absence, not as "no sharing".
* Whether two identically named tables are the same physical table, or two
  unrelated tables that happen to share a common noun.
* Which profile is actually active at runtime. Evidence from all profiles
  is collected, with the profile recorded in metadata where detectable.

False-positive controls
-----------------------
* Test configuration (``src/test/resources``) is skipped: sharing an H2
  instance in tests says nothing about production coupling.
* In-memory databases (HSQLDB/H2/Derby/SQLite ``mem:``) are marked
  ``in_memory``; the detector never reports them as shared, because each
  JVM gets its own instance even when the name matches.
* ``@Entity`` classes without an explicit ``@Table`` name are recorded but
  excluded from table-overlap reasoning by the detector.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml

from pipeline.config import SharedPersistenceConfig
from pipeline.models import PersistenceEvidence

CONFIG_SUFFIXES = (".yml", ".yaml", ".properties")
SQL_SUFFIXES = (".sql",)

# jdbc:mysql://host:3306/db?params | jdbc:hsqldb:mem:name | r2dbc:postgresql://...
# Semicolons are part of the URL (SQL Server's ";databaseName=", H2's
# ";DB_CLOSE_DELAY="), so they must not terminate the match.
_DB_URL_RE = re.compile(r"(?P<scheme>jdbc|r2dbc):(?P<rest>[^\s\"'`,]+)", re.IGNORECASE)
_SCHEMA_KEY_RE = re.compile(
    r"(?:default[_-]schema|currentSchema|default_schema_name)\s*[:=]\s*[\"']?([\w$-]+)",
    re.IGNORECASE,
)
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(?:(?P<schema>[\w$]+)[`\"\]]?\s*\.\s*[`\"\[]?)?(?P<table>[\w$]+)",
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(r"@Entity\b")
_TABLE_ANNOTATION_RE = re.compile(r'@Table\s*\(([^)]*)\)')
_TABLE_NAME_ARG_RE = re.compile(r'name\s*=\s*"([^"]+)"')
_TABLE_SCHEMA_ARG_RE = re.compile(r'schema\s*=\s*"([^"]+)"')
_CLASS_NAME_RE = re.compile(r"\bclass\s+(\w+)")
_LIQUIBASE_TABLE_RE = re.compile(r'(?:tableName\s*=\s*"([^"]+)"|tableName\s*:\s*[\"\']?([\w$-]+))')
_PLACEHOLDER_RE = re.compile(r"\$\{[^}]*\}")


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def parse_db_url(raw_url: str, in_memory_drivers: frozenset[str]) -> dict[str, Any]:
    """Parse a JDBC/R2DBC URL into a normalized database identity.

    Returns a dict with ``driver``, ``host``, ``port``, ``database``,
    ``identity``, ``in_memory`` and ``resolved``. ``identity`` is what the
    detector compares across services; ``resolved`` is False when the URL
    still contains unresolved ``${...}`` placeholders, which means the
    identity is a naming match, not a proven physical match.
    """
    url = raw_url.strip().strip("\"'")
    match = _DB_URL_RE.match(url)
    rest = match.group("rest") if match else url
    scheme = (match.group("scheme") if match else "jdbc").lower()

    driver, _, remainder = rest.partition(":")
    driver = driver.lower()
    remainder = remainder.strip()

    host: Optional[str] = None
    port: Optional[str] = None
    database: Optional[str] = None
    in_memory = False

    if remainder.startswith("//"):
        # //host[:port]/database[?params] -- the networked form
        authority, _, tail = remainder[2:].partition("/")
        host, _, port = authority.partition(":")
        # Trim any ";param=value" tail that rode along on the authority
        # (SQL Server puts its database there rather than in the path).
        port = port.split(";", 1)[0]
        host = host.split(";", 1)[0]
        database = tail.split("?", 1)[0].split(";", 1)[0] or None
        # SQL Server carries the database as a ;databaseName= parameter
        if not database and "databasename=" in remainder.lower():
            db_match = re.search(r"databaseName=([\w$-]+)", remainder, re.IGNORECASE)
            database = db_match.group(1) if db_match else None
    else:
        # driver-local form, e.g. hsqldb:mem:petclinic, h2:file:./data/db
        parts = [p for p in remainder.split(":") if p]
        if parts and parts[0].lower() in {"mem", "file", "tcp", "res"}:
            in_memory = parts[0].lower() == "mem"
            database = parts[1].split(";", 1)[0] if len(parts) > 1 else None
        elif parts:
            database = parts[0].split(";", 1)[0]

    if driver in in_memory_drivers and (in_memory or ":mem:" in url.lower()):
        in_memory = True

    resolved = not _PLACEHOLDER_RE.search(url)

    if in_memory:
        identity = f"{driver}:mem:{(database or '').lower()}"
    elif host:
        identity = f"{driver}://{host.lower()}:{port or ''}/{(database or '').lower()}"
    else:
        identity = f"{driver}:{(database or '').lower()}"

    return {
        "scheme": scheme,
        "driver": driver,
        "host": host or None,
        "port": port or None,
        "database": database,
        "identity": identity,
        "in_memory": in_memory,
        "resolved": resolved,
        "url": url,
    }


def _iter_config_files(module_dir: Path) -> list[Path]:
    """Main (non-test) configuration files for one module."""
    resources = module_dir / "src" / "main" / "resources"
    if not resources.exists():
        return []
    return sorted(p for p in resources.rglob("*") if p.is_file() and p.suffix.lower() in CONFIG_SUFFIXES)


def scan_config_file(
    path: Path,
    service: str,
    repo_root: Path,
    sp_config: SharedPersistenceConfig,
) -> list[PersistenceEvidence]:
    """Datasource URLs and explicit schema names in one config file."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    rel = path.relative_to(repo_root).as_posix()
    records: list[PersistenceEvidence] = []
    lines = text.splitlines()

    for match in _DB_URL_RE.finditer(text):
        parsed = parse_db_url(match.group(0), sp_config.in_memory_drivers)
        line_no = _line_number(text, match.start())
        records.append(
            PersistenceEvidence(
                service=service,
                kind="datasource",
                value=parsed["identity"],
                file=rel,
                line=line_no,
                evidence=(lines[line_no - 1].strip() if 0 < line_no <= len(lines) else match.group(0)),
                confidence=(
                    sp_config.confidence_resolved_datasource
                    if parsed["resolved"]
                    else sp_config.confidence_unresolved_datasource
                ),
                metadata=parsed,
            )
        )

    for match in _SCHEMA_KEY_RE.finditer(text):
        line_no = _line_number(text, match.start())
        records.append(
            PersistenceEvidence(
                service=service,
                kind="datasource",
                value=f"schema:{match.group(1).lower()}",
                file=rel,
                line=line_no,
                evidence=(lines[line_no - 1].strip() if 0 < line_no <= len(lines) else match.group(0)),
                confidence=sp_config.confidence_unresolved_datasource,
                metadata={"schema": match.group(1), "identity": f"schema:{match.group(1).lower()}", "kind": "schema"},
            )
        )

    return records


def scan_java_entities(
    path: Path, service: str, repo_root: Path, sp_config: SharedPersistenceConfig
) -> list[PersistenceEvidence]:
    """JPA table declarations in one Java file."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    if not _ENTITY_RE.search(text):
        return []

    rel = path.relative_to(repo_root).as_posix()
    lines = text.splitlines()
    records: list[PersistenceEvidence] = []

    explicit = False
    for match in _TABLE_ANNOTATION_RE.finditer(text):
        args = match.group(1)
        name_match = _TABLE_NAME_ARG_RE.search(args)
        if not name_match:
            continue
        explicit = True
        schema_match = _TABLE_SCHEMA_ARG_RE.search(args)
        line_no = _line_number(text, match.start())
        records.append(
            PersistenceEvidence(
                service=service,
                kind="entity",
                value=name_match.group(1).lower(),
                file=rel,
                line=line_no,
                evidence=(lines[line_no - 1].strip() if 0 < line_no <= len(lines) else match.group(0)),
                confidence=0.85,
                metadata={
                    "table": name_match.group(1),
                    "schema": schema_match.group(1) if schema_match else None,
                    "explicit": True,
                },
            )
        )

    if not explicit:
        # @Entity with no @Table(name=...): the effective table name depends
        # on Hibernate's naming strategy, so this is recorded but excluded
        # from table-overlap reasoning by the detector.
        class_match = _CLASS_NAME_RE.search(text)
        if class_match:
            line_no = _line_number(text, class_match.start())
            records.append(
                PersistenceEvidence(
                    service=service,
                    kind="entity",
                    value=class_match.group(1).lower(),
                    file=rel,
                    line=line_no,
                    evidence=(lines[line_no - 1].strip() if 0 < line_no <= len(lines) else class_match.group(0)),
                    confidence=0.4,
                    metadata={"table": class_match.group(1), "explicit": False, "inferred_from_class_name": True},
                )
            )

    return records


def scan_sql_file(path: Path, service: str, repo_root: Path) -> list[PersistenceEvidence]:
    """CREATE TABLE statements in one SQL schema/migration file."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    rel = path.relative_to(repo_root).as_posix()
    lines = text.splitlines()
    records: list[PersistenceEvidence] = []
    for match in _CREATE_TABLE_RE.finditer(text):
        line_no = _line_number(text, match.start())
        records.append(
            PersistenceEvidence(
                service=service,
                kind="table",
                value=match.group("table").lower(),
                file=rel,
                line=line_no,
                evidence=(lines[line_no - 1].strip() if 0 < line_no <= len(lines) else match.group(0)),
                confidence=0.9,
                metadata={
                    "table": match.group("table"),
                    "schema": match.group("schema"),
                    "migration": "/db/migration/" in rel or "/changelog" in rel.lower(),
                },
            )
        )
    return records


def scan_liquibase_changelog(path: Path, service: str, repo_root: Path) -> list[PersistenceEvidence]:
    """``createTable`` entries in a Liquibase changelog (XML or YAML form)."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    if "createTable" not in text:
        return []

    rel = path.relative_to(repo_root).as_posix()
    lines = text.splitlines()
    records: list[PersistenceEvidence] = []
    for match in _LIQUIBASE_TABLE_RE.finditer(text):
        table = match.group(1) or match.group(2)
        if not table:
            continue
        line_no = _line_number(text, match.start())
        records.append(
            PersistenceEvidence(
                service=service,
                kind="table",
                value=table.lower(),
                file=rel,
                line=line_no,
                evidence=(lines[line_no - 1].strip() if 0 < line_no <= len(lines) else match.group(0)),
                confidence=0.85,
                metadata={"table": table, "liquibase": True},
            )
        )
    return records


def scan_compose_environment(
    repo_root: Path, sp_config: SharedPersistenceConfig
) -> list[PersistenceEvidence]:
    """Datasource URLs wired into services through compose environment blocks.

    This catches the very common "all services point at one database
    container" arrangement, which never appears in any module's own
    configuration. The compose service name is used as the service
    identity, matching how the docker extractor names nodes.
    """
    from pipeline.extractors.docker import find_compose_files

    records: list[PersistenceEvidence] = []
    for compose_path in find_compose_files(repo_root):
        try:
            text = compose_path.read_text(encoding="utf-8", errors="ignore")
            data = yaml.safe_load(text)
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue

        rel = compose_path.relative_to(repo_root).as_posix()
        lines = text.splitlines()
        for service_name, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue
            environment = svc_def.get("environment")
            values: list[str] = []
            if isinstance(environment, dict):
                values = [str(v) for v in environment.values() if v is not None]
            elif isinstance(environment, list):
                values = [str(v) for v in environment]

            for value in values:
                match = _DB_URL_RE.search(value)
                if not match:
                    continue
                parsed = parse_db_url(match.group(0), sp_config.in_memory_drivers)
                line_no = next(
                    (i + 1 for i, line in enumerate(lines) if match.group(0) in line),
                    0,
                )
                records.append(
                    PersistenceEvidence(
                        service=service_name,
                        kind="datasource",
                        value=parsed["identity"],
                        file=rel,
                        line=line_no,
                        evidence=value.strip(),
                        confidence=(
                            sp_config.confidence_resolved_datasource
                            if parsed["resolved"]
                            else sp_config.confidence_unresolved_datasource
                        ),
                        metadata={**parsed, "from": "docker-compose-environment"},
                    )
                )
    return records


def extract_persistence_evidence(
    repo_root: Path,
    module_to_service: dict[str, str],
    sp_config: Optional[SharedPersistenceConfig] = None,
) -> list[PersistenceEvidence]:
    """All persistence evidence for a repository."""
    repo_root = Path(repo_root).resolve()
    sp_config = sp_config or SharedPersistenceConfig()
    records: list[PersistenceEvidence] = []

    for module_dir_name, service in module_to_service.items():
        module_dir = repo_root / module_dir_name
        if not module_dir.exists():
            continue

        for config_file in _iter_config_files(module_dir):
            records.extend(scan_config_file(config_file, service, repo_root, sp_config))

        java_src = module_dir / "src" / "main" / "java"
        if java_src.exists():
            for java_file in java_src.rglob("*.java"):
                records.extend(scan_java_entities(java_file, service, repo_root, sp_config))

        resources = module_dir / "src" / "main" / "resources"
        if resources.exists():
            for sql_file in resources.rglob("*"):
                if not sql_file.is_file():
                    continue
                if sql_file.suffix.lower() in SQL_SUFFIXES:
                    records.extend(scan_sql_file(sql_file, service, repo_root))
                elif sql_file.suffix.lower() in (".xml", ".yml", ".yaml", ".json"):
                    records.extend(scan_liquibase_changelog(sql_file, service, repo_root))

    records.extend(scan_compose_environment(repo_root, sp_config))
    return records
