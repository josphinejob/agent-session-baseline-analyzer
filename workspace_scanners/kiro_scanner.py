"""
Kiro workspace scanner.

Walks a .kiro/ folder and returns a structured result describing what loads
at session start (steering files, skill metadata) and what loads on demand
(skill index + sub-files, agents when invoked).

Inclusion mode behaviour (verified against Kiro docs):
  always    — loaded every session, counted in the session baseline
  fileMatch — loaded when matching files are in context
  manual    — only when explicitly invoked; zero baseline tokens
  missing / unrecognised — Kiro fails open: treated as 'always'
"""

from __future__ import annotations

import json
from pathlib import Path

# ── constants ────────────────────────────────────────────────────────────────

# Kiro's own system prompt baseline.
# No public independent measurement exists as of September 2026.
# The 10K figure is based on workspace research (May 2026) against the Kiro IDE/CLI default agent.
# Kiro bills via credits rather than tokens, so this value is harder to verify than Claude Code's.
# Update this when a reliable public measurement becomes available.
KIRO_PLATFORM_BASELINE_TOKENS = 10000
KIRO_PLATFORM_BASELINE_SOURCE  = "~10K estimated from workspace research (May 2026). No public independent measurement available — update when one is published."

KNOWN_INCLUSION_MODES = {"always", "fileMatch", "manual"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _count_chars(text: str) -> int:
    """Return raw character count. Caller converts to tokens."""
    return len(text)


def _parse_inclusion(content: str) -> str:
    """Extract inclusion mode from YAML frontmatter.

    Falls back to 'always' on missing, malformed, or unrecognised values —
    matching Kiro's fail-open behaviour.
    """
    if not content.startswith("---"):
        return "always"
    parts = content.split("---", 2)
    if len(parts) < 3:
        return "always"
    for line in parts[1].splitlines():
        if line.strip().startswith("inclusion:"):
            value = line.split(":", 1)[1].strip().strip("\"'")
            return value if value in KNOWN_INCLUSION_MODES else "always"
    return "always"


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


# ── steering ─────────────────────────────────────────────────────────────────

def _scan_steering(kiro_path: Path) -> list[dict]:
    """Return one dict per steering file under .kiro/steering/."""
    steering_path = kiro_path / "steering"
    if not steering_path.exists():
        return []

    files = []
    for path in sorted(steering_path.rglob("*.md")):
        try:
            content = _read(path)
            inclusion = _parse_inclusion(content)
            chars = _count_chars(content)
            files.append({
                "path": path,
                "relative": str(path.relative_to(kiro_path.parent)),
                "chars": chars,
                "lines": content.count("\n") + 1,
                "inclusion": inclusion,
                "counts_in_baseline": inclusion != "manual",
            })
        except OSError:
            files.append({"path": path, "error": "unreadable", "counts_in_baseline": False})
    return files


# ── skills ───────────────────────────────────────────────────────────────────

def _scan_skills(kiro_path: Path) -> dict:
    """
    Skills use progressive loading:
      - Only name + description load every session (metadata, ~0% measured 2026-05-20)
      - SKILL.md index loads when Kiro detects relevance
      - Sub-files load one at a time per task
    """
    skills_path = kiro_path / "skills"
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

        sub_files = []
        for sub in sorted(skill_dir.glob("*.md")):
            if sub.name == "SKILL.md":
                continue
            try:
                sub_content = _read(sub)
                sub_files.append({
                    "path": sub,
                    "relative": str(sub.relative_to(kiro_path.parent)),
                    "chars": _count_chars(sub_content),
                    "lines": sub_content.count("\n") + 1,
                })
            except OSError:
                continue

        sub_chars = sum(s["chars"] for s in sub_files)
        skills.append({
            "name": name,
            "dir": skill_dir.name,
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


# ── agents ───────────────────────────────────────────────────────────────────

def _scan_agents(kiro_path: Path, steering: list[dict], skills: dict) -> list[dict]:
    """
    Resolve each agent's real per-session baseline.

    In Kiro a session runs ONE selected agent. A JSON agent declares the steering
    files and skills it loads via its `resources` list (file:// and skill:// URIs),
    so its session floor is agent-specific — not the sum of all steering in the
    workspace. This resolves those references to actual token costs.

    A .md agent is prompt-only: its whole body is the system prompt, with no
    declared resources.
    """
    agents_path = kiro_path / "agents"
    if not agents_path.exists():
        return []

    # Lookups for resolving resource URIs to token costs.
    steering_by_rel = {f["relative"]: f for f in steering if "chars" in f}
    skill_by_dir = {s["dir"]: s for s in skills["skills"]}

    agents = []

    # JSON agents — resolve declared resources
    for path in sorted(agents_path.glob("*.json")):
        try:
            config = json.loads(_read(path))
        except (OSError, json.JSONDecodeError):
            continue
        name = config.get("name", path.stem)
        description = config.get("description", "")

        # The prompt may be inline text or a file:// reference to a prompt file
        # (e.g. "file://prompts/jira-manager-prompt.md", relative to .kiro/agents/).
        # Resolve the reference so the real prompt size is counted, not the URI string.
        prompt_value = config.get("prompt", "")
        if prompt_value.startswith("file://"):
            prompt_path = agents_path / prompt_value[len("file://"):]
            try:
                prompt_text = _read(prompt_path)
            except OSError:
                prompt_text = ""  # referenced prompt file missing/unreadable
        else:
            prompt_text = prompt_value
        prompt_chars = _count_chars(prompt_text)

        steering_refs: list[dict] = []
        skill_refs: list[dict] = []
        for resource in config.get("resources", []):
            if resource.startswith("file://"):
                rel = resource[len("file://"):]
                f = steering_by_rel.get(rel)
                if f:
                    steering_refs.append({"relative": rel, "chars": f["chars"]})
            elif resource.startswith("skill://"):
                # skill://.kiro/skills/<dir>/SKILL.md → match by directory name
                dir_name = Path(resource[len("skill://"):]).parent.name
                sk = skill_by_dir.get(dir_name)
                if sk:
                    skill_refs.append({"dir": dir_name, "metadata_chars": sk["metadata_chars"]})

        steering_chars = sum(r["chars"] for r in steering_refs)
        skill_meta_chars = sum(r["metadata_chars"] for r in skill_refs)
        agents.append({
            "name": name,
            "description": description,
            "kind": "json",
            "prompt_chars": prompt_chars,
            "steering_refs": steering_refs,
            "skill_refs": skill_refs,
            "steering_chars": steering_chars,
            "skill_meta_chars": skill_meta_chars,
            # Per-agent workspace baseline (excludes the fixed platform baseline)
            "baseline_chars": prompt_chars + steering_chars + skill_meta_chars,
        })

    # Markdown prompt-only agents — the body is the whole system prompt
    for path in sorted(agents_path.glob("*.md")):
        try:
            content = _read(path)
        except OSError:
            continue
        name = _parse_frontmatter_field(content, "name") or path.stem
        description = _parse_frontmatter_field(content, "description")
        prompt_chars = _count_chars(content)
        agents.append({
            "name": name,
            "description": description,
            "kind": "markdown",
            "prompt_chars": prompt_chars,
            "steering_refs": [],
            "skill_refs": [],
            "steering_chars": 0,
            "skill_meta_chars": 0,
            "baseline_chars": prompt_chars,
        })

    return agents


# ── findings ─────────────────────────────────────────────────────────────────

def _check_findings(kiro_path: Path, steering: list[dict], skills: dict) -> list[dict]:
    findings = []

    # Steering: missing folder
    if not (kiro_path / "steering").exists():
        findings.append({
            "level": "warn",
            "what": ".kiro/steering/ does not exist",
            "why": "no steering files found — the workspace has no always-loaded instruction layer",
        })

    # Skills: SKILL.md without description
    for skill in skills["skills"]:
        if not skill["description"]:
            findings.append({
                "level": "warn",
                "what": f"{skill['name']}/SKILL.md has no description",
                "why": "description is the load trigger — without it the skill may never be activated",
            })

    return findings


# ── public entry point ────────────────────────────────────────────────────────

def scan(workspace_path: Path) -> dict:
    """Scan a Kiro workspace and return structured results for the report builder."""
    kiro_path = workspace_path / ".kiro"

    steering = _scan_steering(kiro_path)
    skills   = _scan_skills(kiro_path)
    agents   = _scan_agents(kiro_path, steering, skills)
    findings = _check_findings(kiro_path, steering, skills)

    baseline_chars = sum(f["chars"] for f in steering if f.get("counts_in_baseline"))

    return {
        "platform": "Kiro",
        "workspace_path": workspace_path,
        "platform_baseline_tokens": KIRO_PLATFORM_BASELINE_TOKENS,
        "platform_baseline_source": KIRO_PLATFORM_BASELINE_SOURCE,
        "steering": steering,
        "skills": skills,
        "agents": agents,
        "findings": findings,
        "baseline_chars": baseline_chars,
    }
