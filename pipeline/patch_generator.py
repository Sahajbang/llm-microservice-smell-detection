"""
Patch generation: turn an approved refactoring *plan* into concrete file edits.

The refactoring agent (pipeline.llm_refactoring) describes changes in prose,
and prose cannot be applied. This is a third LLM call that receives the plan
plus the current contents of the files it names, and returns the full new
content of every file it changes.

Full-file output is used instead of unified diffs on purpose: models
routinely produce diffs with wrong hunk offsets or context lines that no
longer match, and a diff that fails to apply is wasted work. A full file
always "applies"; whether it is *correct* is the verifier's job.

The response format is delimited blocks rather than JSON, because whole
Java files embedded as JSON strings break on the first unescaped quote::

    <<<FILE order-mod/src/main/java/PaymentClient.java>>>
    ...full content...
    <<<END FILE>>>
    <<<DELETE payment-mod/src/main/java/OrderClient.java>>>

Every path the model returns is untrusted: it is normalized, checked for
traversal outside the repository, and checked against the same module-scope
rule the refactoring plan was held to. A response that fails any check is
re-prompted with the reason, bounded by ``max_retries``.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from pipeline.llm_client import call_llm
from pipeline.llm_refactoring import find_out_of_scope_files
from pipeline.models import Finding
from pipeline.run_logger import RunLogger

_FILE_BLOCK_RE = re.compile(
    r"^<<<FILE[ \t]+(?P<path>[^\n>]+?)[ \t]*>>>[ \t]*\r?\n(?P<body>.*?)^<<<END FILE>>>[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_DELETE_RE = re.compile(r"^<<<DELETE[ \t]+(?P<path>[^\n>]+?)[ \t]*>>>[ \t]*$", re.MULTILINE)
_FENCE_LINE_RE = re.compile(r"^```[\w-]*[ \t]*$")

SYSTEM_PROMPT = """You are a senior software engineer applying an approved refactoring plan to a codebase. The plan has already been decided; your job is to write the code that implements it.

Rules:
- Implement exactly the plan you are given, as the smallest change that achieves it. Keep the existing code style, naming, package structure and conventions.
- The result must compile: if you change or remove a type, method or client, update every caller of it in the files you were shown.
- {scope_rule}
- Output every changed or new file IN FULL. Never output a fragment, an ellipsis, or a placeholder such as "// rest unchanged".
- Do not output files you did not change.

Respond with ONLY file blocks, in exactly this format, one block per file:

<<<FILE relative/path/from/repository/root>>>
the complete new content of the file
<<<END FILE>>>

To delete a file, output a single line instead of a block:
<<<DELETE relative/path/from/repository/root>>>

No explanation before or after the blocks, and no markdown code fences."""


@dataclass
class FileChange:
    """One file the patch writes or removes. Contents are LF-normalized."""

    path: str  # repo-relative, forward slashes
    action: str  # "modify" | "create" | "delete"
    old: Optional[str]
    new: Optional[str]


@dataclass
class Patch:
    finding_key: str
    changes: list[FileChange]
    diff: str

    @property
    def paths(self) -> list[str]:
        return [c.path for c in self.changes]

    def to_dict(self) -> dict[str, Any]:
        """Serializable view. File contents stay out of it -- the diff says
        what changed, and the full new contents are in the patch_ stage log."""
        return {
            "finding_key": self.finding_key,
            "files": [{"path": c.path, "action": c.action} for c in self.changes],
            "diff": self.diff,
        }


def normalize_rel_path(raw: str) -> str:
    """Model-supplied path -> canonical repo-relative POSIX path, or raise.

    Rejects absolute paths, drive letters and any '..' segment outright,
    rather than resolving them, so there is no way to name a file outside
    the repository however the path is spelled.
    """
    text = raw.strip().strip("`\"'").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise ValueError(f"path must be relative to the repository root: {raw!r}")
    parts = PurePosixPath(text).parts
    if any(p in ("..", "") for p in parts) or ".git" in parts:
        raise ValueError(f"path escapes the repository or touches .git: {raw!r}")
    return str(PurePosixPath(*parts))


def resolve_inside(repo_root: Path, rel_path: str) -> Path:
    """Absolute path for `rel_path`, asserted to stay inside `repo_root`
    (a symlinked directory could otherwise point anywhere)."""
    root = repo_root.resolve()
    target = (root / normalize_rel_path(rel_path)).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"path resolves outside the repository: {rel_path!r}")
    return target


def read_source(path: Path) -> str:
    """Read a source file as LF-normalized text. Non-UTF-8 files are refused
    rather than decoded lossily, since a lossy read would be written back."""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _strip_fences(body: str) -> str:
    """Drop a markdown fence a model wrapped a file body in despite the rules."""
    lines = body.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) >= 2 and _FENCE_LINE_RE.match(lines[0].strip()) and lines[-1].strip() == "```":
        lines = lines[1:-1]
    return "\n".join(lines) + "\n"


def parse_patch_response(text: str) -> dict[str, Optional[str]]:
    """{path: new content, or None for a deletion}. Paths are not yet validated."""
    text = text.replace("\r\n", "\n")
    blocks: dict[str, Optional[str]] = {}
    for m in _FILE_BLOCK_RE.finditer(text):
        blocks[m.group("path").strip()] = _strip_fences(m.group("body"))
    for m in _DELETE_RE.finditer(text):
        blocks[m.group("path").strip()] = None
    return blocks


def unified_diff(changes: list[FileChange]) -> str:
    out: list[str] = []
    for c in changes:
        old = (c.old or "").splitlines(keepends=True)
        new = (c.new or "").splitlines(keepends=True)
        out.extend(
            difflib.unified_diff(
                old,
                new,
                fromfile="/dev/null" if c.action == "create" else f"a/{c.path}",
                tofile="/dev/null" if c.action == "delete" else f"b/{c.path}",
            )
        )
    return "".join(out)


def build_changes(
    blocks: dict[str, Optional[str]],
    repo_root: Path,
    *,
    allowed_prefixes: set[str],
    planned_files: set[str],
) -> list[FileChange]:
    """Validate parsed blocks against the repository and the scope rule.

    Raises ValueError with a message written for the model, so the caller
    can feed it straight back as re-prompt feedback.
    """
    if not blocks:
        raise ValueError("The response contained no <<<FILE ...>>> or <<<DELETE ...>>> blocks.")

    changes: list[FileChange] = []
    for raw_path, new in blocks.items():
        rel = normalize_rel_path(raw_path)
        target = resolve_inside(repo_root, rel)
        old = read_source(target) if target.is_file() else None

        if new is None and old is None:
            raise ValueError(f"Cannot delete {rel}: it does not exist.")
        if new is not None and old == new:
            continue  # model echoed an unchanged file; not a change
        action = "delete" if new is None else ("create" if old is None else "modify")
        changes.append(FileChange(path=rel, action=action, old=old, new=new))

    paths = [c.path for c in changes]
    if allowed_prefixes:
        out_of_scope = find_out_of_scope_files({"affected_files": paths}, allowed_prefixes)
        if out_of_scope:
            raise ValueError(
                f"These files are outside the services in the finding: {out_of_scope}. "
                f"Only files under {sorted(allowed_prefixes)} may be changed."
            )
    else:
        # No module could be resolved for the finding's services, so the
        # module rule is unenforceable; fall back to the plan's own file list.
        unplanned = sorted(set(paths) - planned_files)
        if unplanned:
            raise ValueError(
                f"These files are not in the refactoring plan: {unplanned}. "
                f"Only these may be changed: {sorted(planned_files)}."
            )
    if not changes:
        raise ValueError("Every file block was identical to the current file; nothing would change.")
    return changes


def build_patch_prompt(finding: Finding, plan: dict[str, Any], repo_root: Path, max_chars: int) -> str:
    """The plan plus the current text of every file it names."""
    plan_view = {k: plan.get(k) for k in ("affected_files", "changes", "rationale", "expected_impact")}
    sections = [
        f"Finding: {finding.smell_name} involving {', '.join(finding.services)}",
        f"What static analysis found: {finding.description}",
        "",
        "Approved refactoring plan:",
        json.dumps(plan_view, indent=2),
        "",
        "Current contents of the files in the plan:",
    ]
    total = 0
    for raw in plan.get("affected_files") or []:
        rel = normalize_rel_path(str(raw))
        path = resolve_inside(repo_root, rel)
        if not path.is_file():
            sections.append(f"\n=== {rel} (does not exist yet -- create it if the plan needs it) ===")
            continue
        content = read_source(path)
        total += len(content)
        if total > max_chars:
            raise ValueError(
                f"The files in this plan exceed {max_chars} characters, too large to rewrite reliably "
                "in one prompt. Narrow the plan or raise refactor.max_patch_input_chars."
            )
        sections.append(f"\n=== {rel} ===\n{content}")
    return "\n".join(sections)


def generate_patch(
    finding: Finding,
    plan: dict[str, Any],
    repo_root: Path,
    *,
    allowed_prefixes: set[str],
    max_retries: int = 3,
    temperature: float = 0.2,
    max_tokens: int = 16384,
    max_input_chars: int = 120_000,
    logger: Optional[RunLogger] = None,
) -> Patch:
    """Ask the model for the code that implements `plan`; return a validated Patch.

    `repo_root` is the tree the patch will be applied to, so the model
    always rewrites the files as they currently are -- including edits made
    by an earlier finding's patch in the same run.
    """
    planned_files = set()
    for raw in plan.get("affected_files") or []:
        try:
            planned_files.add(normalize_rel_path(str(raw)))
        except ValueError:
            continue  # the plan's own scope check already ran; ignore junk
    scope_rule = (
        f"Only change files under these module directories: {sorted(allowed_prefixes)}."
        if allowed_prefixes
        else f"Only change these files: {sorted(planned_files)}."
    )
    system_prompt = SYSTEM_PROMPT.format(scope_rule=scope_rule)
    base_prompt = build_patch_prompt(finding, plan, repo_root, max_input_chars)

    attempts: list[dict[str, Any]] = []
    changes: Optional[list[FileChange]] = None
    feedback: Optional[str] = None
    for attempt in range(1, max_retries + 1):
        prompt = base_prompt
        if feedback:
            prompt += f"\n\nIMPORTANT: your previous response was rejected: {feedback} Respond again with only the corrected file blocks."
        result = call_llm(system_prompt, prompt, temperature=temperature, max_tokens=max_tokens)
        record: dict[str, Any] = {
            "attempt": attempt,
            "prompt": prompt,
            "raw_response": result.content,
            "reasoning": result.reasoning,
            "model": result.model,
        }
        try:
            changes = build_changes(
                parse_patch_response(result.content),
                repo_root,
                allowed_prefixes=allowed_prefixes,
                planned_files=planned_files,
            )
        except (ValueError, UnicodeDecodeError) as exc:
            feedback = str(exc)
            record.update(parse_ok=False, error=feedback)
            attempts.append(record)
            changes = None
            continue
        record.update(parse_ok=True, files=[c.path for c in changes])
        attempts.append(record)
        break

    patch = Patch(finding.key, changes, unified_diff(changes)) if changes else None
    if logger is not None:
        logger.log_stage(
            f"patch_{finding.key}",
            {
                "finding_key": finding.key,
                "smell": finding.smell,
                "allowed_module_prefixes": sorted(allowed_prefixes),
                "system_prompt": system_prompt,
                "attempts": attempts,
                "files": [
                    {"path": c.path, "action": c.action, "new_content": c.new} for c in (changes or [])
                ],
                "diff": patch.diff if patch else None,
            },
        )
    if patch is None:
        raise ValueError(f"LLM did not produce a valid patch after {max_retries} attempts: {feedback}")
    return patch
