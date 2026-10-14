#!/usr/bin/env python3
"""
agent-session-baseline-analyzer

Shows how many tokens load before you type anything in a Kiro or Claude Code
workspace - broken down by steering files, skills, and agents.

Usage:
    python3 agent_session_baseline_analyzer.py <path-to-workspace> [platform]

    python3 agent_session_baseline_analyzer.py ~/development/my-kiro-project
    python3 agent_session_baseline_analyzer.py ~/development/my-claude-project
    python3 agent_session_baseline_analyzer.py ~/development/my-project kiro
    python3 agent_session_baseline_analyzer.py ~/development/my-project claude

The optional [platform] argument is 'kiro' or 'claude'. When omitted, the
script detects the workspace type from the folder structure:
  .kiro/    → Kiro
  CLAUDE.md → Claude Code

If a workspace contains BOTH a .kiro/ folder and a CLAUDE.md, detection is
ambiguous and the platform argument is required.

Writes a timestamped HTML report to a reports/ folder inside this tool's
directory (git-ignored) and opens it in the default browser.

Options:
    --no-open    Write the report but do not open it in the browser.
"""

import sys
import webbrowser
from pathlib import Path

# Add the project root to sys.path so workspace_scanners and html_report import correctly
# regardless of which directory the user runs the script from.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from workspace_scanners import kiro_scanner, claude_code_scanner
from html_report import html_report_builder

# Reports are written to a reports/ folder inside this tool's own directory,
# resolved from the script location - not into the repo being analyzed (which
# may not be ours to write into) and not into the current working directory.
# This folder is git-ignored.
REPORTS_DIR = Path(__file__).resolve().parent / "reports"


VALID_PLATFORMS = {"kiro", "claude"}


def detect_workspace(path: Path) -> str | list[str]:
    """
    Identify the workspace type by looking for known marker files/folders.

    Returns:
      'kiro'         if only a .kiro/ folder is found
      'claude_code'  if only a CLAUDE.md / .claude/ is found
      []             if neither is found
      ['kiro', 'claude_code']  if BOTH are found (ambiguous - caller must resolve)
    """
    found = []
    if (path / ".kiro").is_dir():
        found.append("kiro")
    if (path / "CLAUDE.md").is_file() or (path / ".claude").is_dir():
        found.append("claude_code")

    if len(found) == 1:
        return found[0]
    return found  # [] or both


def resolve_platform(workspace_path: Path, platform_arg: str | None) -> str | None:
    """
    Decide which platform to analyze.

    If platform_arg is given ('kiro' or 'claude'), it takes priority and detection
    is skipped - this is what makes the tool work on a workspace that has both.

    If platform_arg is omitted, fall back to auto-detection. When detection is
    ambiguous (both configs present) or finds nothing, return None and the caller
    prints guidance.
    """
    if platform_arg:
        return "kiro" if platform_arg == "kiro" else "claude_code"

    detected = detect_workspace(workspace_path)
    if isinstance(detected, str):
        return detected
    return None  # empty (nothing found) or ambiguous (both found)


def main() -> int:
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    if not positional:
        print("Usage: python3 agent_session_baseline_analyzer.py <path-to-workspace> [platform] [--no-open]")
        print("")
        print("  <path-to-workspace>  Path to a Kiro or Claude Code project folder.")
        print("  [platform]           Optional: 'kiro' or 'claude'. Forces the platform,")
        print("                       skipping auto-detection. Required if the workspace")
        print("                       has both a .kiro/ folder and a CLAUDE.md.")
        print("  --no-open            Write the report without opening it in the browser.")
        return 1

    workspace_path = Path(positional[0]).expanduser().resolve()

    # Optional second positional argument: the platform override.
    platform_arg = None
    if len(positional) > 1:
        platform_arg = positional[1].lower()
        if platform_arg not in VALID_PLATFORMS:
            print(f"ERROR: '{positional[1]}' is not a valid platform. Use 'kiro' or 'claude'.")
            return 1

    if not workspace_path.is_dir():
        print(f"ERROR: {workspace_path} is not a directory.")
        return 1

    workspace_type = resolve_platform(workspace_path, platform_arg)

    if workspace_type is None:
        detected = detect_workspace(workspace_path)
        if isinstance(detected, list) and len(detected) == 2:
            # Both configs present - ambiguous, needs an explicit choice.
            print(f"ERROR: {workspace_path} contains BOTH a .kiro/ folder and a CLAUDE.md.")
            print("  Detection is ambiguous. Specify which to analyze by adding the platform as the last argument:")
            print(f"    python3 agent_session_baseline_analyzer.py {positional[0]} kiro")
            print(f"    python3 agent_session_baseline_analyzer.py {positional[0]} claude")
        else:
            print(f"ERROR: {workspace_path} is not a recognised AI assistant workspace.")
            print("  Expected a .kiro/ folder (Kiro) or a CLAUDE.md / .claude/ folder (Claude Code).")
        return 1

    print(f"Workspace: {workspace_path}")
    print(f"Type     : {workspace_type.replace('_', ' ').title()}")

    if workspace_type == "kiro":
        result = kiro_scanner.scan(workspace_path)
    else:
        result = claude_code_scanner.scan(workspace_path)

    report_path = html_report_builder.build_report(result, REPORTS_DIR)

    # Print summary to terminal.
    # Workspace tokens = always-loaded files + skill metadata (which also always loads).
    # This must match the report card, so include skill metadata here too.
    to_tokens       = html_report_builder.to_tokens
    baseline_tokens = to_tokens(result["baseline_chars"]) + to_tokens(result["skills"]["metadata_chars"])
    platform_tokens = result["platform_baseline_tokens"]
    session_floor   = platform_tokens + baseline_tokens

    # For Kiro the floor is per-agent; the terminal shows the built-in default agent
    # (all always-steering). The HTML report breaks down each agent's real floor.
    # For Claude Code the main session floor is fixed (CLAUDE.md + imports).
    if workspace_type == "kiro":
        floor_note = "default agent - see report for per-agent floors"
        workspace_note = "always-steering + skill metadata (default agent)"
        baseline_note = "ESTIMATE - no public measurement of Kiro's harness exists"
    else:
        floor_note = "main session - every message pays this"
        workspace_note = "CLAUDE.md + @imports + skill metadata"
        baseline_note = "estimate for Claude 4.x - measured early 2026, varies by model"

    print(f"")
    print(f"Workspace tokens : {baseline_tokens:,}  ({workspace_note})")
    print(f"Platform baseline: ~{platform_tokens:,}  ({baseline_note})")
    print(f"Session floor    : ~{session_floor:,}  ({floor_note})")
    print(f"")

    findings = result.get("findings", [])
    criticals = [f for f in findings if f["level"] == "critical"]
    warns     = [f for f in findings if f["level"] == "warn"]
    if criticals:
        for f in criticals:
            print(f"  [CRITICAL] {f['what']}")
    if warns:
        for f in warns:
            print(f"  [WARN] {f['what']}")

    print(f"Report: {report_path}")

    if "--no-open" not in flags:
        webbrowser.open(report_path.as_uri())

    return 0


if __name__ == "__main__":
    sys.exit(main())
