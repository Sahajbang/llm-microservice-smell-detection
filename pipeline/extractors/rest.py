"""
REST/HTTP dependency evidence collector.

Scans a Java Spring Boot repository's source with regex/string matching
(not AST parsing, per the project's Phase 1 scope in CLAUDE.md Step 2) for
service-to-service calls. This is the Phase 1 extractor, moved here
unchanged in behaviour and extended with a per-edge ``confidence`` and
``metadata``; ``pipeline.extractor`` re-exports it so existing callers and
the ``python -m pipeline.extractor`` CLI keep working.

Detected patterns:
  - ``@FeignClient(name = "...")`` / ``@FeignClient(value = "...")`` /
    ``@FeignClient("...")`` annotations.
  - ``DiscoveryClient.getInstances("service-name")`` lookups -- a common
    Spring Cloud pattern for resolving another service's address without
    ever embedding a URL literal.
  - Any string literal of the form ``"http://<service-name>/..."`` found
    anywhere in a module's Java source. This single pass covers both
    RestTemplate calls (``.exchange(...)``, ``.getForObject(...)``, etc.)
    and WebClient/RestClient calls (``.uri(...)``, ``.baseUrl(...)``),
    including the common indirection where the host is first assigned to a
    field/variable and only concatenated into the actual call later. Each
    match is tagged "resttemplate", "webclient", or the generic
    "http-literal" fallback by looking for nearby (then file-wide)
    client-type hints.

Known limitations (documented rather than solved, per project scope):
  - This is string/regex matching, not semantic analysis. It can miss calls
    assembled dynamically in ways that never put a full "http://<service>"
    literal anywhere in the source, and can in principle false-positive on
    an unrelated string that happens to look like one of the above (e.g. a
    comment).
  - gRPC and message-queue-based inter-service communication are entirely
    out of scope for this extractor, as stated in the PRD (Section 8).
  - A call's "caller" is always the top-level module (a directory directly
    under the repo root with a ``pom.xml`` or a ``build.gradle``/
    ``build.gradle.kts``) that contains the source file. A repository using
    neither build tool, or nesting modules more than one level deep, is
    invisible to this extractor -- see ``discover_services``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from pipeline.models import DEFAULT_DEPENDENCY_CONFIDENCE, DEPENDENCY_SOURCE_CONFIDENCE


@dataclass(frozen=True)
class Edge:
    caller: str
    callee: str
    source: str  # "feign" | "resttemplate" | "webclient" | "discovery-client" | "http-literal" | "docker-compose"
    file: str  # path relative to repo root, forward-slash separated
    line: int  # 1-indexed; 0 for edges with no single source line (docker-compose)
    evidence: str  # the matched snippet / line, trimmed
    confidence: float = DEFAULT_DEPENDENCY_CONFIDENCE
    metadata: dict[str, Any] = field(default_factory=dict)


def edge_confidence(source: str) -> float:
    """Per-source confidence for a dependency edge (see pipeline.models)."""
    return DEPENDENCY_SOURCE_CONFIDENCE.get(source, DEFAULT_DEPENDENCY_CONFIDENCE)


def make_edge(caller: str, callee: str, source: str, file: str, line: int, evidence: str, **metadata: Any) -> Edge:
    return Edge(
        caller=caller,
        callee=callee,
        source=source,
        file=file,
        line=line,
        evidence=evidence,
        confidence=edge_confidence(source),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Service discovery: map each top-level Maven module directory to the
# Spring service name it registers under (spring.application.name), falling
# back to the module's Maven artifactId if that isn't set.
# ---------------------------------------------------------------------------

_APP_NAME_YAML_RE = re.compile(
    r"spring:\s*\n(?:.*\n)*?\s*application:\s*\n\s*name:\s*([\w.-]+)"
)
_APP_NAME_PROPS_RE = re.compile(r"^\s*spring\.application\.name\s*=\s*([\w.-]+)", re.MULTILINE)
_ARTIFACT_ID_RE = re.compile(r"<artifactId>\s*([\w.-]+)\s*</artifactId>")


def discover_services(repo_root: Path) -> dict[str, str]:
    """Return {module_dir_name: service_name} for every top-level module.

    A module is recognized by a Maven or a Gradle build descriptor at its
    root (``pom.xml``, or ``build.gradle``/``build.gradle.kts``). Either way
    the declared Spring ``spring.application.name`` is preferred, since that
    is the name the service actually calls itself over REST/discovery.
    Absent that:
      - Maven falls back to the module's ``<artifactId>`` (a real declared
        identifier).
      - Gradle falls back to the module directory's own name. Unlike Maven,
        a standalone Gradle module (no multi-project ``settings.gradle``)
        has no other declared identifier -- Gradle itself defaults an
        unnamed project to its containing directory name, so this mirrors
        Gradle's own convention rather than inventing one.
    """
    repo_root = Path(repo_root)
    services: dict[str, str] = {}
    for module_dir in sorted(p for p in repo_root.iterdir() if p.is_dir()):
        pom = module_dir / "pom.xml"
        if pom.exists():
            name = _find_application_name(module_dir) or _find_artifact_id(pom)
        elif (module_dir / "build.gradle").exists() or (module_dir / "build.gradle.kts").exists():
            name = _find_application_name(module_dir) or module_dir.name
        else:
            continue
        if name:
            services[module_dir.name] = name
    return services


def _find_application_name(module_dir: Path) -> Optional[str]:
    for candidate in (
        module_dir / "src" / "main" / "resources" / "application.yml",
        module_dir / "src" / "main" / "resources" / "application.yaml",
        module_dir / "src" / "main" / "resources" / "application.properties",
    ):
        if not candidate.exists():
            continue
        text = candidate.read_text(encoding="utf-8", errors="ignore")
        if candidate.suffix == ".properties":
            m = _APP_NAME_PROPS_RE.search(text)
        else:
            m = _APP_NAME_YAML_RE.search(text)
        if m:
            return m.group(1)
    return None


def _find_artifact_id(pom_path: Path) -> Optional[str]:
    text = pom_path.read_text(encoding="utf-8", errors="ignore")
    # The project's own <artifactId> is the first one after </parent> (the
    # parent's artifactId normally appears first, before its own close tag).
    parent_end = text.find("</parent>")
    search_text = text[parent_end:] if parent_end != -1 else text
    m = _ARTIFACT_ID_RE.search(search_text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Java source scanning
# ---------------------------------------------------------------------------

_FEIGN_RE = re.compile(r"@FeignClient\s*\(([^)]*)\)")
_FEIGN_NAMED_ARG_RE = re.compile(r'(?:name|value)\s*=\s*"([^"]+)"')
_FEIGN_BARE_STRING_RE = re.compile(r'"([^"]+)"')

_DISCOVERY_CLIENT_RE = re.compile(
    r'discoveryClient\s*\.\s*getInstances\s*\(\s*"([\w-]+)"\s*\)', re.IGNORECASE
)

_HTTP_LITERAL_RE = re.compile(r'https?://([a-zA-Z][\w-]*)')

_RESTTEMPLATE_HINT_RE = re.compile(r"restTemplate\s*\.", re.IGNORECASE)
_WEBCLIENT_HINT_RE = re.compile(r"WebClient|RestClient")


def _feign_target(annotation_args: str) -> Optional[str]:
    m = _FEIGN_NAMED_ARG_RE.search(annotation_args)
    if m:
        return m.group(1)
    m = _FEIGN_BARE_STRING_RE.search(annotation_args)
    if m:
        return m.group(1)
    return None


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _classify_http_literal(window: str, full_text: str) -> str:
    if _RESTTEMPLATE_HINT_RE.search(window):
        return "resttemplate"
    if _WEBCLIENT_HINT_RE.search(window):
        return "webclient"
    if _RESTTEMPLATE_HINT_RE.search(full_text):
        return "resttemplate"
    if _WEBCLIENT_HINT_RE.search(full_text):
        return "webclient"
    return "http-literal"


def scan_java_file(
    path: Path, caller_service: str, all_services: set[str], repo_root: Path
) -> list[Edge]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    rel_path = path.relative_to(repo_root).as_posix()
    edges: list[Edge] = []

    for m in _FEIGN_RE.finditer(text):
        target = _feign_target(m.group(1))
        if target and target in all_services and target != caller_service:
            line_no = _line_number(text, m.start())
            edges.append(make_edge(caller_service, target, "feign", rel_path, line_no, m.group(0).strip()))

    for m in _DISCOVERY_CLIENT_RE.finditer(text):
        target = m.group(1)
        if target in all_services and target != caller_service:
            line_no = _line_number(text, m.start())
            edges.append(
                make_edge(caller_service, target, "discovery-client", rel_path, line_no, m.group(0).strip())
            )

    seen_in_file: set[tuple[str, int]] = set()
    lines = text.splitlines()
    for m in _HTTP_LITERAL_RE.finditer(text):
        target = m.group(1)
        if target not in all_services or target == caller_service:
            continue
        line_no = _line_number(text, m.start())
        key = (target, line_no)
        if key in seen_in_file:
            continue
        seen_in_file.add(key)
        window = text[max(0, m.start() - 300): m.end() + 100]
        source = _classify_http_literal(window, text)
        evidence = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else m.group(0)
        edges.append(make_edge(caller_service, target, source, rel_path, line_no, evidence))

    return edges


def extract_rest_edges(repo_root: Path, module_to_service: Optional[dict[str, str]] = None) -> list[Edge]:
    """Every REST/discovery dependency edge in the repository."""
    repo_root = Path(repo_root).resolve()
    module_to_service = module_to_service if module_to_service is not None else discover_services(repo_root)
    all_services = set(module_to_service.values())

    edges: list[Edge] = []
    for module_dir_name, caller_service in module_to_service.items():
        java_src = repo_root / module_dir_name / "src" / "main" / "java"
        if not java_src.exists():
            continue
        for java_file in java_src.rglob("*.java"):
            edges.extend(scan_java_file(java_file, caller_service, all_services, repo_root))
    return edges


def edges_to_dicts(edges: list[Edge]) -> list[dict]:
    """Deduplicate, sort, and render edges as the plain dicts the rest of
    the pipeline (and the API/frontend) consume."""
    unique: dict[tuple, Edge] = {}
    for e in edges:
        unique[(e.caller, e.callee, e.source, e.file, e.line)] = e
    ordered = sorted(unique.values(), key=lambda e: (e.caller, e.callee, e.file, e.line))
    return [asdict(e) for e in ordered]
