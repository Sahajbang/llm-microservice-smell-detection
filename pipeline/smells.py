"""
Smell specifications: definition, LLM task framing, and refactoring
guidance, one entry per supported smell.

This registry is what keeps the LLM layer free of ``if smell == ...``
dispatching. ``pipeline.llm_detection`` and ``pipeline.llm_refactoring``
build their prompts from the spec of whatever smell a finding carries, so
adding a smell means adding a detector and a spec -- not editing the agent
code.

Scope note (important for the thesis write-up): CLAUDE.md and the PRD
specify two smells -- Cyclic Dependency (Phase 1) and Hub-like Dependency
(Phase 2+, "degree/centrality threshold on the same SDG"). Shared
Persistence is implemented in addition to those; it is a standard
microservice anti-pattern in the literature and is statically observable
from datasource/schema configuration, but it is NOT part of the original
PRD scope. Temporal coupling is deliberately not implemented -- see
README.md "Known limitations".

The Cyclic Dependency prompt text below is byte-identical to the Phase 1
implementation so that runs logged before and after this refactor remain
directly comparable.
"""

from __future__ import annotations

from dataclasses import dataclass

CYCLIC_DEPENDENCY = "cyclic_dependency"
HUB_DEPENDENCY = "hub_dependency"
SHARED_PERSISTENCE = "shared_persistence"


@dataclass(frozen=True)
class SmellSpec:
    """Everything the LLM layer needs to reason about one smell."""

    key: str
    name: str
    definition: str
    # Framing for the detection call: what the model is being shown and
    # what judgement it is being asked to make.
    detection_task: str
    # Framing for the refactoring call: what a minimal fix looks like for
    # this smell specifically.
    refactoring_task: str
    # Noun used when referring to the supplied evidence in the prompt.
    evidence_noun: str = "code evidence"
    # Noun used by the scope rule ("...services participating in the X").
    scope_noun: str = "finding"
    # What static analysis cannot establish for this smell. Included in the
    # prompt so the model does not over-claim certainty.
    static_limits: str = ""


_DETECTION_SCHEMA = """Respond with ONLY a single JSON object -- no prose before or after it, no markdown code fences -- matching exactly this shape:
{{
  "smell": "{name}",
  "detected": true or false,
  "confidence": <number between 0.0 and 1.0>,
  "severity": "LOW" | "MEDIUM" | "HIGH",
  "rationale": "<2-4 sentences, grounded in the specific {evidence_noun} given>",
  "refactoring_recommended": true or false
}}"""

_REFACTORING_SCHEMA = """Respond with ONLY a single JSON object -- no prose before or after it, no markdown code fences -- matching exactly this shape:
{{
  "smell": "{name}",
  "affected_files": ["<repo-relative path>", ...],
  "changes": [
    {{
      "file": "<repo-relative path, must appear in affected_files>",
      "method_or_class": "<the specific class/method touched>",
      "description": "<precise, actionable description of the change>"
    }}
  ],
  "rationale": "<why this specific fix addresses the smell, 2-4 sentences>",
  "expected_impact": "<what becomes safer/possible afterward, and any tradeoff introduced, 2-4 sentences>"
}}

Every path in "affected_files" and every "changes[].file" MUST be either one of the real files you were shown evidence for, or a new file you are proposing to add -- and in both cases it MUST live inside one of the module directories of the services participating in the {scope_noun}. Never name a file belonging to a service outside the {scope_noun}."""

# The Cyclic Dependency refactoring schema differs from the generalized one
# by a single clause ("why this specific fix breaks the cycle") that was in
# the Phase 1 prompt. Kept verbatim so old and new runs stay comparable.
_CYCLIC_REFACTORING_RATIONALE = "<why this specific fix breaks the cycle, 2-4 sentences>"


SPECS: dict[str, SmellSpec] = {
    CYCLIC_DEPENDENCY: SmellSpec(
        key=CYCLIC_DEPENDENCY,
        name="Cyclic Dependency",
        definition="""Cyclic Dependency (architectural smell): two or more microservices depend on
each other's APIs, directly or through a chain of calls, forming a closed
loop (A -> B -> ... -> A). This couples the services' availability,
deployability, and release cadence together, undermining a core purpose of
a microservice architecture -- independent evolution and failure isolation.
A synchronous cycle can also compound into cascading latency or, in the
worst case, deadlock, if calls are ever nested across it.""",
        detection_task=(
            "You will be given ONE candidate Cyclic Dependency, found by static analysis of a real "
            "repository's source code (regex-matched service-to-service call sites -- not your own "
            "judgement). Your job is to confirm whether this is a genuine architectural problem in "
            "this specific case, using only the code evidence given, and to explain why."
        ),
        refactoring_task=(
            "You will be given a CONFIRMED Cyclic Dependency -- already verified as a genuine problem "
            "by a separate review step -- between the services in the cycle, plus the specific code "
            "evidence for each hop. Propose the MINIMAL change that breaks the cycle. Do not redesign "
            "the services beyond what is needed to remove the cyclic call, and do not reference any "
            "file outside the services named in the cycle."
        ),
        evidence_noun="code evidence",
        scope_noun="cycle",
        static_limits=(
            "The call sites were found by regex matching, so a call assembled dynamically at runtime "
            "may be missing, and gRPC or message-queue communication is not visible at all."
        ),
    ),
    HUB_DEPENDENCY: SmellSpec(
        key=HUB_DEPENDENCY,
        name="Hub-like Dependency",
        definition="""Hub-like Dependency (architectural smell): a single microservice is connected to
an unusually large share of the other services -- typically because most of
the system calls it, or because it orchestrates most of the system. The
service becomes a shared point of failure and a change bottleneck: it must
be deployed and scaled for almost any feature, its API accumulates
unrelated responsibilities, and an outage in it degrades most of the
system. Not every highly connected service is a smell: an API gateway, a
BFF, or a deliberate composition layer is SUPPOSED to fan out, and a
service that is central because it owns a genuinely central domain concept
may be correctly designed.""",
        detection_task=(
            "You will be given ONE candidate Hub-like Dependency, found by static analysis of a real "
            "repository's service dependency graph (measured in-/out-degree against the graph's own "
            "degree distribution -- not your own judgement), together with the concrete call sites "
            "that produced each edge. Your job is to judge whether this service's high connectivity "
            "is a genuine architectural problem, or whether it is the expected shape for this service's "
            "role (for example an API gateway, BFF, or deliberate composition layer), and to explain why."
        ),
        refactoring_task=(
            "You will be given a CONFIRMED Hub-like Dependency -- already verified as a genuine problem "
            "by a separate review step -- plus the concrete call sites connecting the hub to its "
            "neighbours. Propose the MINIMAL change that reduces the hub's coupling, for example "
            "splitting one clearly separable responsibility out of the hub's API, moving a "
            "cross-cutting concern to the caller, or removing a dependency that does not need to be "
            "synchronous. Do not redesign the whole system, and do not reference any file outside the "
            "hub and the neighbouring services named in the finding."
        ),
        evidence_noun="code evidence",
        scope_noun="finding",
        static_limits=(
            "Degree is computed from statically matched call sites only; call volume, latency, and how "
            "often each dependency is actually exercised at runtime are not observable here."
        ),
    ),
    SHARED_PERSISTENCE: SmellSpec(
        key=SHARED_PERSISTENCE,
        name="Shared Persistence",
        definition="""Shared Persistence (architectural smell): two or more microservices read from or
write to the same database, schema, or tables, instead of each owning its
own data and exchanging it through APIs or events. The shared store becomes
a hidden coupling point that no service's API documents: a schema migration
by one service can break another, services cannot be deployed or scaled
independently, and ownership of the data becomes ambiguous. It is one of
the most commonly reported microservice anti-patterns precisely because it
is invisible in the service-to-service call graph.""",
        detection_task=(
            "You will be given ONE candidate Shared Persistence finding, derived by static analysis of "
            "a real repository's datasource configuration, JPA entity/table declarations, and SQL "
            "schema/migration files -- not your own judgement. Static configuration can be misleading: "
            "the same logical database name may refer to different physical instances per environment, "
            "configuration may be externalized (Spring Cloud Config, environment variables, Kubernetes "
            "secrets) and therefore unresolvable here, and identically named tables are not necessarily "
            "the same physical table. Your job is to judge, from the evidence given and its stated "
            "confidence, whether these services genuinely share persistent storage, and to explain why."
        ),
        refactoring_task=(
            "You will be given a CONFIRMED Shared Persistence finding -- already verified as a genuine "
            "problem by a separate review step -- plus the datasource and schema evidence for each "
            "service involved. Propose the MINIMAL change that moves these services toward separate "
            "ownership of their data, for example giving one service its own datasource/schema, "
            "assigning clear ownership of the shared tables to exactly one service and exposing the "
            "rest through that service's API, or replacing a direct read of another service's table "
            "with a call or an event. Do not redesign the whole data model, and do not reference any "
            "file outside the services named in the finding."
        ),
        evidence_noun="configuration and schema evidence",
        scope_noun="finding",
        static_limits=(
            "Static analysis cannot prove two services reach the same physical database: profiles, "
            "environment variables, and externalized configuration are resolved at runtime. Treat the "
            "confidence attached to each piece of evidence as a ceiling on your own certainty."
        ),
    ),
}


def get_spec(smell_key: str) -> SmellSpec:
    """Look up a smell spec, with an explicit error for unknown keys.

    Raises KeyError rather than returning a generic fallback: silently
    prompting an LLM with the wrong smell definition would corrupt results
    in a way that is hard to notice in a log.
    """
    try:
        return SPECS[smell_key]
    except KeyError:
        raise KeyError(f"Unknown smell '{smell_key}'. Known smells: {sorted(SPECS)}") from None


def detection_system_prompt(spec: SmellSpec) -> str:
    """System prompt for the detection agent for one smell."""
    schema = _DETECTION_SCHEMA.format(name=spec.name, evidence_noun=spec.evidence_noun)
    return (
        "You are an expert software architect specializing in microservice architecture smells.\n\n"
        f"{spec.definition}\n\n"
        f"{spec.detection_task}\n\n"
        f"{schema}"
    )


def refactoring_system_prompt(spec: SmellSpec) -> str:
    """System prompt for the refactoring agent for one smell."""
    schema = _REFACTORING_SCHEMA.format(name=spec.name, scope_noun=spec.scope_noun)
    if spec.key == CYCLIC_DEPENDENCY:
        schema = schema.replace(
            '"rationale": "<why this specific fix addresses the smell, 2-4 sentences>"',
            f'"rationale": "{_CYCLIC_REFACTORING_RATIONALE}"',
        )
    return (
        "You are an expert software architect specializing in microservice architecture refactoring.\n\n"
        f"{spec.definition}\n\n"
        f"{spec.refactoring_task}\n\n"
        f"{schema}"
    )
