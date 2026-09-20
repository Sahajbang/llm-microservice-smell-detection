"""
The detector contract.

A detector answers one question -- "where does smell X occur in this
repository?" -- deterministically, from already-extracted evidence. It

* never clones, scans, or otherwise touches the repository itself
  (extraction already happened; the tree is available only for reading
  source snippets when building evidence for the LLM),
* never calls an LLM (that is a separate, later stage, so the
  deterministic signal stays independent of model behaviour),
* returns structured ``Finding`` objects, never strings,
* declares which evidence it needs, so the orchestrator can skip it with a
  recorded reason instead of failing when that evidence is missing.

Adding a smell therefore means adding one module here plus a spec in
:mod:`pipeline.smells` -- no changes to the orchestrator or the LLM agents.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from pipeline.models import AnalysisContext, Finding

# Evidence kinds a detector can require from the extraction layer.
EVIDENCE_DEPENDENCIES = "dependencies"
EVIDENCE_PERSISTENCE = "persistence"


class SmellDetector(ABC):
    """Base class for all deterministic smell detectors."""

    #: Smell key, matching an entry in :data:`pipeline.smells.SPECS`.
    smell: str = ""
    #: Evidence kinds that must be non-empty for this detector to be useful.
    required_evidence: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return type(self).__name__

    def missing_evidence(self, ctx: AnalysisContext) -> Optional[str]:
        """Return the first required evidence kind that is absent, if any.

        Used by the registry to skip a detector with an explicit,
        recorded reason rather than having it silently return nothing.
        """
        for kind in self.required_evidence:
            if kind == EVIDENCE_DEPENDENCIES and not ctx.evidence.dependencies:
                return EVIDENCE_DEPENDENCIES
            if kind == EVIDENCE_PERSISTENCE and not ctx.evidence.persistence:
                return EVIDENCE_PERSISTENCE
        return None

    @abstractmethod
    def detect(self, ctx: AnalysisContext) -> list[Finding]:
        """Return every candidate finding for this smell, or an empty list."""
        raise NotImplementedError
