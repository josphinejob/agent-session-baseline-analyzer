"""
Claude Code workspace scanner.

Walks a Claude Code project and returns a structured result describing what
loads at session start and what loads on demand.

Claude Code's always-loaded layer:
  - CLAUDE.md at the project root
  - Any files pulled in via @file imports in CLAUDE.md (expanded at session start)
  - ~/.claude/CLAUDE.md (user-level, outside this repo — flagged but not counted)

On-demand:
  - .claude/agents/ (subagents) — load in full when invoked
  - .claude/commands/ (slash commands) — load when the slash command is called
  - .claude/skills/ — load when relevant (same progressive model as Kiro)

Reference: https://docs.anthropic.com/en/docs/claude-code
"""

from __future__ import annotations

import re
from pathlib import Path

# ── constants ────────────────────────────────────────────────────────────────

# Claude Code's built-in system prompt baseline.
# History:
#   ~33K tokens total (system prompt + tool definitions) for Claude 3.x/4.x models — measured early 2026.
#   Anthropic cut 80%+ of the system prompt's behavioural-rules TEXT on 2026-07-24 for Claude 5
#   (Opus 5, Fable 5). That cut is to the instruction text only — the bulk of the 33K is tool
#   definitions, which were not cut. So the 5.x total is only modestly lower (~30K), not dramatically.
#   Use 33K for Claude Sonnet 4.x or earlier; ~30K for Claude 5. Update when a full post-cut
#   harness measurement (including tool definitions) is published.
CLAUDE_CODE_PLATFORM_BASELINE_TOKENS = 33000
CLAUDE_CODE_PLATFORM_BASELINE_SOURCE  = "~33K for Claude 4.x and earlier (measured early 2026). The 2026-07-24 '80% cut' applied to the system prompt's behavioural-rules text, not the tool definitions that dominate the harness — so Claude 5 is only modestly lower (~30K), not dramatically reduced."

# Matches a bare @file import on its own line (documented convention).
# An @import mid-sentence is intentionally not matched — it would false-positive
# on email addresses and decorators.
IMPORT_RE = re.compile(r"^@(\S+)\s*$")
FENCE_RE   = re.compile(r"^\s*(```|~~~)")


# ── helpers ──────────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _count_chars(text: str) -> int:
    return len(text)


def _parse_frontmatter_field(content: str, field: str) -> str:
    """Return a single scalar field from YAML frontmatter, or empty string."""
    if not content.startswith("---"):
        return ""
    parts = content.split("---", 2)
    if len(parts) < 3:
        return ""
    for line in parts[1].splitlines():
        if line.strip().startswith(f"{field}:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


def _first_body_line(content: str) -> str:
    """Fallback description: first non-heading, non-empty body line."""
    in_frontmatter = False
    past_frontmatter = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "---":
            if not past_frontmatter:
                in_frontmatter = not in_frontmatter
                if not in_frontmatter:
                    past_frontmatter = True
            continue
        if in_frontmatter:
            continue
        if stripped and not stripped.startswith("#"):
            return stripped[:120]
    return ""


def _find_imports(content: str) -> tuple[list[str], list[str]]:
    """Return (live_imports, fenced_imports).

    An @file import inside a code fence is inert — the file never loads,
    with no warning from Claude Code. This is reported as a finding.
    """
    live: list[str] = []
    fenced: list[str] = []
    in_fence = False
    for line in content.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        m = IMPORT_RE.match(line)
        if m:
            (fenced if in_fence else live).append(m.group(1))
    return live, fenced


# ── always-loaded layer ───────────────────────────────────────────────────────

def _scan_always_loaded(workspace_path: Path, findings: list[dict]) -> list[dict]:
    """
    CLAUDE.md is the entry point. It can pull in other files via @file imports.
    All of these load at session start and stay in context for the whole session.
    """
    entry = workspace_path / "CLAUDE.md"
    if not entry.exists():
        findings.append({
            "level": "warn",
            "what": "CLAUDE.md not found at project root",
            "why": "Claude Code has no always-loaded instruction layer for this project",
        })
        return []

    files = []
    try:
        content = _read(entry)
    except OSError:
        findings.append({"level": "warn", "what": "CLAUDE.md is unreadable", "why": "permission or encoding error"})
        return []

    files.append({
        "path": entry,
        "relative": "CLAUDE.md",
        "chars": _count_chars(content),
        "lines": content.count("\n") + 1,
        "role": "entry point",
    })

    live_imports, fenced_imports = _find_imports(content)

    for target in fenced_imports:
        findings.append({
            "level": "critical",
            "what": f"@{target} in CLAUDE.md is inside a code fence",
            "why": "a fenced @import is inert — the file never loads, silently, so its instructions are absent from every session",
        })

    for target in live_imports:
        resolved = (workspace_path / target).resolve()
        # Guard against path traversal — reject imports that escape the workspace root.
        if not resolved.is_relative_to(workspace_path.resolve()):
            findings.append({
                "level": "critical",
                "what": f"@{target} in CLAUDE.md resolves outside the workspace",
                "why": "the import path escapes the project folder and will not be loaded",
            })
            continue
        if not resolved.exists():
            findings.append({
                "level": "critical",
                "what": f"@{target} in CLAUDE.md does not resolve to a file on disk",
                "why": "the import points at a missing file; nothing loads and Claude Code gives no warning",
            })
            continue
        try:
            imported_content = _read(resolved)
            files.append({
                "path": resolved,
                "relative": str(resolved.relative_to(workspace_path)),
                "chars": _count_chars(imported_content),
                "lines": imported_content.count("\n") + 1,
                "role": f"imported by CLAUDE.md",
            })
        except OSError:
            findings.append({"level": "warn", "what": f"@{target} is unreadable", "why": "permission or encoding error"})

    return files


# ── subagents ─────────────────────────────────────────────────────────────────

def _scan_subagents(workspace_path: Path, findings: list[dict]) -> list[dict]:
    """
    Subagents live in .claude/agents/.
    Each subagent's full body loads when it is invoked — it is not in the session baseline.
    Claude Code uses the `name` frontmatter field (not the filename) as the agent identity.
    """
    agents_path = workspace_path / ".claude" / "agents"
    if not agents_path.exists():
        return []

    agents = []
    for path in sorted(agents_path.glob("*.md")):
        try:
            content = _read(path)
        except OSError:
            continue
        name = _parse_frontmatter_field(content, "name") or path.stem
        description = _parse_frontmatter_field(content, "description")

        # Warn if the file declares no name frontmatter — the filename is not authoritative
        if not _parse_frontmatter_field(content, "name"):
            findings.append({
                "level": "info",
                "what": f".claude/agents/{path.name} has no `name:` in frontmatter",
                "why": "Claude Code uses the `name` field as the subagent identity; falling back to filename may cause invocation mismatches",
            })

        agents.append({
            "path": path,
            "relative": str(path.relative_to(workspace_path)),
            "name": name,
            "description": description,
            "chars": _count_chars(content),
            "lines": content.count("\n") + 1,
        })
    return agents


# ── slash commands ────────────────────────────────────────────────────────────

def _scan_slash_commands(workspace_path: Path) -> list[dict]:
    """Slash commands live in .claude/commands/. They load only when invoked."""
    commands_path = workspace_path / ".claude" / "commands"
    if not commands_path.exists():
        return []
    commands = []
    for path in sorted(commands_path.glob("*.md")):
        try:
            content = _read(path)
            commands.append({
                "path": path,
                "relative": str(path.relative_to(workspace_path)),
                "name": path.stem,
                "chars": _count_chars(content),
                "lines": content.count("\n") + 1,
            })
        except OSError:
            continue
    return commands


# ── skills ────────────────────────────────────────────────────────────────────

def _scan_skills(workspace_path: Path, findings: list[dict]) -> dict:
    """Skills in .claude/skills/ use the same progressive loading model as Kiro."""
    skills_path = workspace_path / ".claude" / "skills"
    if not skills_path.exists():
        return {"skills": [], "metadata_chars": 0, "total_on_demand_chars": 0}

    skills = []
    for skill_dir in sorted(p for p in skills_path.iterdir() if p.is_dir()):
        index = skill_dir / "SKILL.md"
        if not index.exists():
            continue
        try:
            content = _read(index)
        except OSError:
            continue
        name = _parse_frontmatter_field(content, "name") or skill_dir.name
        description = _parse_frontmatter_field(content, "description") or _first_body_line(content)
        metadata_chars = _count_chars(name + " " + description)
        index_chars = _count_chars(content)

        # Finding: a skill with no description will never be triggered
        if not description:
            findings.append({
                "level": "warn",
                "what": f"{name}/SKILL.md has no description",
                "why": "description is the load trigger — without it the skill may never be activated",
            })

        sub_files = []
        for sub in sorted(skill_dir.glob("*.md")):
            if sub.name == "SKILL.md":
                continue
            try:
                sub_content = _read(sub)
                sub_files.append({
                    "path": sub,
                    "relative": str(sub.relative_to(workspace_path)),
                    "chars": _count_chars(sub_content),
                    "lines": sub_content.count("\n") + 1,
                })
            except OSError:
                continue

        sub_chars = sum(s["chars"] for s in sub_files)
        skills.append({
            "name": name,
            "description": description,
            "metadata_chars": metadata_chars,
            "index_chars": index_chars,
            "sub_files": sub_files,
            "sub_chars": sub_chars,
            "total_on_demand_chars": index_chars + sub_chars,
        })

    metadata_chars = sum(s["metadata_chars"] for s in skills)
    total_on_demand = sum(s["total_on_demand_chars"] for s in skills)
    return {
        "skills": skills,
        "metadata_chars": metadata_chars,
        "total_on_demand_chars": total_on_demand,
    }


# ── user-level CLAUDE.md check ────────────────────────────────────────────────

def _check_user_level_claude_md(findings: list[dict]) -> None:
    """
    ~/.claude/CLAUDE.md loads on top of everything else if it exists.
    It is outside the project repo so it is not counted in the totals,
    but its presence is flagged so the user knows it affects the session.
    """
    user_md = Path.home() / ".claude" / "CLAUDE.md"
    if user_md.exists():
        try:
            chars = _count_chars(_read(user_md))
            findings.append({
                "level": "info",
                "what": f"~/.claude/CLAUDE.md exists (~{chars // 4:,} tokens estimated)",
                "why": "this file loads on top of your project's CLAUDE.md and is not counted in the totals below — it adds to your actual session cost",
            })
        except OSError:
            pass


# ── public entry point ────────────────────────────────────────────────────────

def scan(workspace_path: Path) -> dict:
    """Scan a Claude Code workspace and return structured results for the report builder."""
    findings: list[dict] = []

    _check_user_level_claude_md(findings)

    always_loaded = _scan_always_loaded(workspace_path, findings)
    subagents     = _scan_subagents(workspace_path, findings)
    slash_commands = _scan_slash_commands(workspace_path)
    skills        = _scan_skills(workspace_path, findings)

    baseline_chars = sum(f["chars"] for f in always_loaded)

    return {
        "platform": "Claude Code",
        "workspace_path": workspace_path,
        "platform_baseline_tokens": CLAUDE_CODE_PLATFORM_BASELINE_TOKENS,
        "platform_baseline_source": CLAUDE_CODE_PLATFORM_BASELINE_SOURCE,
        "always_loaded": always_loaded,
        "subagents": subagents,
        "slash_commands": slash_commands,
        "skills": skills,
        "findings": findings,
        "baseline_chars": baseline_chars,
    }
