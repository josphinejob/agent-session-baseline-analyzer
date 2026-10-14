"""
HTML report builder.

Takes the structured result from either workspace scanner and produces
a self-contained HTML report showing:
  - Session baseline: tokens that load before the user types anything
  - Platform baseline: the AI assistant's own built-in system prompt (fixed, not controllable)
  - Always-loaded files (steering / CLAUDE.md imports)
  - Skills: metadata always loaded vs full content on demand
  - Agents/subagents: additional cost when invoked
  - Findings: configuration problems that cause silent failures
  - Recommendations: what to do if the baseline is too high
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

# ── token estimation ──────────────────────────────────────────────────────────

CHARS_PER_TOKEN = 4   # ~4 English characters per token; a planning estimate, not a billing figure

def to_tokens(chars: int) -> int:
    return chars // CHARS_PER_TOKEN


# ── verdict thresholds on TOTAL session floor ─────────────────────────────────
# The verdict grades the real per-message cost = platform baseline + your controllable
# layer, NOT just your files. A 33K session is heavy no matter how small your own
# config is; calling it "lean" because your part is small would be dishonest.
# Anchored to real platform sizes: Kiro ~10K, Claude ~33K.
LEAN_MAX_TOTAL     = 10_000   # a bare platform with minimal config
MODERATE_MAX_TOTAL = 20_000   # workable

# Fixed platform harness sizes, for context in the verdict and analysis summary.
# (label, size, basis)
PLATFORM_ANCHORS = [
    ("Kiro",            "~10K", "estimate - no public measurement"),
    ("Claude Code 4.x", "~33K", "measured early 2026"),
    ("Claude Code 5.x", "~30K", "only the prompt-rules portion was cut; tool definitions dominate"),
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _e(value) -> str:
    """HTML-escape a value, and normalise em/en dashes to hyphens for a clean report."""
    return html.escape(str(value)).replace("\u2014", "-").replace("\u2013", "-")


def _verdict(total_tokens: int, platform_tokens: int, controllable_tokens: int, findings: list[dict]) -> dict:
    """Grade the TOTAL session floor (platform baseline + your controllable layer).

    The threshold is applied to the total because that is what every message actually
    pays. The message names the split so it is clear how much is fixed (platform) vs
    reducible (your files).
    """
    critical = [f for f in findings if f["level"] == "critical"]
    if critical:
        return {
            "status": "FIX FIRST",
            "colour": "#c53030",
            "soft": "#fdecea",
            "message": f"{len(critical)} critical finding(s) mean part of your instruction layer is not loading as intended. Fix these before judging token cost.",
        }

    split = (f"= {platform_tokens:,} fixed platform baseline + {controllable_tokens:,} you control "
             f"(thresholds: \u2264{LEAN_MAX_TOTAL:,} lean, {LEAN_MAX_TOTAL:,}-{MODERATE_MAX_TOTAL:,} moderate, >{MODERATE_MAX_TOTAL:,} heavy).")

    if total_tokens > MODERATE_MAX_TOTAL:
        return {
            "status": "HEAVY",
            "colour": "#c53030",
            "soft": "#fdecea",
            "message": (f"Every message pays {total_tokens:,} tokens before you type {split} "
                        f"Much of this is the fixed platform baseline; the {controllable_tokens:,} you control is where any reduction has to come from."),
        }
    if total_tokens > LEAN_MAX_TOTAL:
        return {
            "status": "MODERATE",
            "colour": "#975a16",
            "soft": "#fdf3e2",
            "message": (f"Every message pays {total_tokens:,} tokens before you type {split} Workable."),
        }
    return {
        "status": "LEAN",
        "colour": "#276749",
        "soft": "#e8f5ee",
        "message": (f"Every message pays {total_tokens:,} tokens before you type {split} "
                    f"About as lean as a real coding-agent session gets."),
    }


def _analysis_summary_table(platform_label: str, layers: list[tuple], total_tokens: int) -> str:
    """Worked example: platform + workspace (+ agent) = context at session start.

    layers: list of (label, tokens, reducible_bool). The platform baseline row is
    always first with reducible=False.
    """
    rows = []
    for label, tokens, reducible in layers:
        mark = "<span class='ok'>Yes</span>" if reducible else "<span class='muted'>No - fixed</span>"
        rows.append(
            f"<tr><td>{_e(label)}</td><td class='num'>{tokens:,}</td><td>{mark}</td></tr>"
        )
    rows.append(
        f"<tr class='total-row'><td><strong>Total context at session start</strong></td>"
        f"<td class='num'><strong>{total_tokens:,}</strong></td><td></td></tr>"
    )
    return "".join(rows)


def _reference_anchors_table() -> str:
    rows = []
    for label, size, basis in PLATFORM_ANCHORS:
        rows.append(
            f"<tr><td>{_e(label)}</td><td class='num'>{_e(size)}</td><td class='muted small'>{_e(basis)}</td></tr>"
        )
    return "".join(rows)


def _verdict_tag(total_tokens: int) -> str:
    """Compact coloured verdict tag for a single agent's total session floor."""
    if total_tokens > MODERATE_MAX_TOTAL:
        label, colour = "HEAVY", "#c53030"
    elif total_tokens > LEAN_MAX_TOTAL:
        label, colour = "MODERATE", "#975a16"
    else:
        label, colour = "LEAN", "#276749"
    return f"<span class='tag' style='color:{colour};border-color:{colour}'>{label}</span>"


# ── section renderers ─────────────────────────────────────────────────────────

def _findings_rows(findings: list[dict]) -> str:
    order = {"critical": 0, "warn": 1, "info": 2}
    sorted_findings = sorted(findings, key=lambda f: order.get(f["level"], 9))
    rows = []
    for f in sorted_findings:
        rows.append(
            f"<tr>"
            f"<td><span class='tag {_e(f['level'])}'>{_e(f['level'])}</span></td>"
            f"<td>{_e(f['what'])}</td>"
            f"<td class='muted'>{_e(f['why'])}</td>"
            f"</tr>"
        )
    return "".join(rows)


def _findings_section(findings: list[dict]) -> str:
    """Return the full Findings section, or empty string when there are none."""
    if not findings:
        return ""
    return (
        '<h2>Findings <span class="hint">configuration problems that fail silently</span></h2>\n'
        '<table><tr><th>level</th><th>what</th><th>why it matters</th></tr>\n'
        f'{_findings_rows(findings)}</table>'
    )


def _always_loaded_rows_kiro(steering: list[dict]) -> str:
    rows = []
    for f in sorted(steering, key=lambda x: x.get("chars", 0), reverse=True):
        if "error" in f:
            rows.append(f"<tr><td class='mono'>{_e(f['relative'])}</td><td colspan='3' class='muted'>unreadable</td></tr>")
            continue
        inclusion = f["inclusion"]
        colour = {"always": "#276749", "fileMatch": "#975a16", "manual": "#718096"}.get(inclusion, "#718096")
        not_counted = "" if f["counts_in_baseline"] else " <span class='note'>(not in baseline)</span>"
        rows.append(
            f"<tr>"
            f"<td class='mono'>{_e(f['relative'])}</td>"
            f"<td><span style='color:{colour};font-weight:600'>{_e(inclusion)}</span></td>"
            f"<td class='num'>{f['lines']:,}</td>"
            f"<td class='num'>{to_tokens(f['chars']):,}{not_counted}</td>"
            f"</tr>"
        )
    return "".join(rows) or "<tr><td colspan='4' class='none'>No steering files found.</td></tr>"


def _always_loaded_rows_claude(always_loaded: list[dict]) -> str:
    rows = []
    for f in sorted(always_loaded, key=lambda x: x.get("chars", 0), reverse=True):
        rows.append(
            f"<tr>"
            f"<td class='mono'>{_e(f['relative'])}</td>"
            f"<td class='muted'>{_e(f['role'])}</td>"
            f"<td class='num'>{f['lines']:,}</td>"
            f"<td class='num'>{to_tokens(f['chars']):,}</td>"
            f"</tr>"
        )
    return "".join(rows) or "<tr><td colspan='4' class='none'>No always-loaded files found.</td></tr>"


def _skills_rows(skills_data: dict) -> str:
    rows = []
    for i, skill in enumerate(sorted(skills_data["skills"], key=lambda s: s["total_on_demand_chars"], reverse=True), 1):
        rows.append(
            f"<tr class='skill-row'>"
            f"<td class='num muted'>{i}.</td>"
            f"<td><strong>{_e(skill['name'])}</strong><br><span class='muted small'>{_e(skill['description'][:100])}</span></td>"
            f"<td class='num'>{to_tokens(skill['metadata_chars']):,}</td>"
            f"<td class='num'>{to_tokens(skill['index_chars']):,}</td>"
            f"<td class='num'>{to_tokens(skill['sub_chars']):,}</td>"
            f"<td class='num'>{to_tokens(skill['total_on_demand_chars']):,}</td>"
            f"</tr>"
        )
        for j, sub in enumerate(skill["sub_files"]):
            label = chr(ord("a") + j)
            rows.append(
                f"<tr class='sub-row'>"
                f"<td class='num muted'>{i}{label}.</td>"
                f"<td class='mono indent'>&#8627; {_e(Path(sub['relative']).name)}</td>"
                f"<td class='num muted'>-</td>"
                f"<td class='num muted'>-</td>"
                f"<td class='num'>{to_tokens(sub['chars']):,}</td>"
                f"<td class='num muted'>-</td>"
                f"</tr>"
            )
    return "".join(rows) or "<tr><td colspan='6' class='none'>No skills configured.</td></tr>"


def _agents_rows_kiro(agents: list[dict], platform_tokens: int) -> str:
    if not agents:
        return "<tr><td colspan='7' class='none'>No custom agents configured.</td></tr>"
    rows = []
    # Sort by the true per-agent session floor, heaviest first.
    for i, a in enumerate(sorted(agents, key=lambda x: x["baseline_chars"], reverse=True), 1):
        workspace_tokens = to_tokens(a["baseline_chars"])
        session_floor    = platform_tokens + workspace_tokens
        steering_count   = len(a.get("steering_refs", []))
        skill_count      = len(a.get("skill_refs", []))
        refs = f"{steering_count} steering &middot; {skill_count} skills" if a["kind"] == "json" else "prompt-only"
        rows.append(
            f"<tr>"
            f"<td class='num muted'>{i}.</td>"
            f"<td><strong>{_e(a['name'])}</strong> <span class='muted small'>{_e(a['kind'])}</span></td>"
            f"<td class='muted small'>{refs}</td>"
            f"<td class='num'>{to_tokens(a['prompt_chars']):,}</td>"
            f"<td class='num'>{workspace_tokens:,}</td>"
            f"<td class='num'><strong>{session_floor:,}</strong></td>"
            f"<td>{_verdict_tag(session_floor)}</td>"
            f"</tr>"
        )
    return "".join(rows)


def _subagents_rows_claude(subagents: list[dict], platform_tokens: int, workspace_tokens: int) -> str:
    if not subagents:
        return "<tr><td colspan='5' class='none'>No subagents configured.</td></tr>"
    rows = []
    for i, a in enumerate(sorted(subagents, key=lambda x: x["chars"], reverse=True), 1):
        body_tokens   = to_tokens(a["chars"])
        # Each subagent runs in its own context: it re-pays the platform baseline and
        # the always-loaded workspace layer (CLAUDE.md + imports + skill metadata),
        # then adds its own system prompt body.
        session_floor = platform_tokens + workspace_tokens + body_tokens
        rows.append(
            f"<tr>"
            f"<td class='num muted'>{i}.</td>"
            f"<td><strong>{_e(a['name'])}</strong></td>"
            f"<td class='muted small'>{_e(a['description'])}</td>"
            f"<td class='num'>{body_tokens:,}</td>"
            f"<td class='num'><strong>{session_floor:,}</strong></td>"
            f"</tr>"
        )
    return "".join(rows)


def _slash_commands_rows(commands: list[dict]) -> str:
    if not commands:
        return "<tr><td colspan='3' class='none'>No slash commands configured.</td></tr>"
    rows = []
    for c in commands:
        rows.append(
            f"<tr>"
            f"<td class='mono'>{_e(c['name'])}</td>"
            f"<td class='num'>{c['lines']:,}</td>"
            f"<td class='num'>{to_tokens(c['chars']):,}</td>"
            f"</tr>"
        )
    return "".join(rows)


# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
:root {
  --bg: #eef4fb; --surface: #ffffff; --border: #d5e3f0;
  --text: #1f2d3d; --muted: #5a6b7f; --accent: #2563a8;
  --accent-soft: #e4eff9; --head-from: #2f6fb0; --head-to: #3f8fc4;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg); color: var(--text); padding: 0 0 48px;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 0 20px; }
.hero {
  background: linear-gradient(135deg, var(--head-from), var(--head-to));
  color: #fff; padding: 28px 0 24px; margin-bottom: 24px;
  box-shadow: 0 2px 10px rgba(31,45,61,.12);
}
.hero .wrap { padding: 0 20px; }
h1 { font-size: 24px; margin-bottom: 4px; font-weight: 700; letter-spacing: -.01em; }
.meta { color: rgba(255,255,255,.85); font-size: 13px; }
.verdict {
  border-radius: 10px; padding: 18px 22px; margin-bottom: 26px;
  background: var(--surface); border: 1px solid var(--border);
  border-left: 6px solid var(--vcolour, var(--accent));
  box-shadow: 0 1px 4px rgba(31,45,61,.06);
}
.verdict .status {
  font-size: 20px; font-weight: 800; letter-spacing: .02em;
  display: inline-block; padding: 2px 12px; border-radius: 6px;
  background: var(--vsoft, var(--accent-soft)); color: var(--vcolour, var(--accent));
}
.verdict .scope { color: var(--muted); font-size: 13px; margin-top: 8px; }
.verdict p { margin-top: 8px; }
.metrics {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px; margin-bottom: 28px;
}
.metric {
  background: var(--surface); border: 1px solid var(--border);
  border-top: 3px solid var(--accent); border-radius: 8px; padding: 15px 16px;
  box-shadow: 0 1px 4px rgba(31,45,61,.05);
}
.metric .n { font-size: 24px; font-weight: 800; color: var(--accent); }
.metric .l { color: var(--muted); font-size: 12px; margin-top: 3px; }
h2 { font-size: 16px; margin: 30px 0 10px; color: var(--text); font-weight: 700; }
h2 .hint { font-weight: 400; color: var(--muted); font-size: 13px; }
table {
  width: 100%; border-collapse: collapse;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; overflow: hidden; margin-bottom: 8px;
  box-shadow: 0 1px 4px rgba(31,45,61,.05);
}
th {
  text-align: left; font-size: 12px; text-transform: uppercase;
  letter-spacing: .04em; color: var(--accent); padding: 10px 14px;
  background: var(--accent-soft); border-bottom: 1px solid var(--border); font-weight: 700;
}
td { padding: 9px 14px; border-bottom: 1px solid var(--border); vertical-align: top; font-size: 14px; }
tr:last-child td { border-bottom: none; }
tbody tr:nth-child(even) { background: #f7fafd; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
td.muted, span.muted { color: var(--muted); }
td.none { color: var(--muted); text-align: center; }
span.small { font-size: 12px; }
span.note { font-size: 11px; color: var(--muted); }
td.indent { padding-left: 28px; }
tr.sub-row td { color: var(--muted); }
.tag {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  padding: 2px 7px; border-radius: 3px; border: 1px solid; white-space: nowrap;
}
.tag.critical { color: #c53030; border-color: #c53030; }
.tag.warn     { color: #975a16; border-color: #975a16; }
.tag.info     { color: #2b6cb0; border-color: #2b6cb0; }
span.ok { color: #2f855a; font-weight: 600; }
tr.total-row td { border-top: 2px solid var(--accent); background: var(--accent-soft); }
p.footnote { color: var(--muted); font-size: 13px; margin-top: 6px; }
"""


# ── full page assembly ────────────────────────────────────────────────────────

def _render_kiro(result: dict) -> str:
    workspace_path  = result["workspace_path"]
    steering        = result["steering"]
    skills_data     = result["skills"]
    agents          = result["agents"]
    findings        = result["findings"]
    baseline_chars  = result["baseline_chars"]
    platform_tokens = result["platform_baseline_tokens"]
    platform_source = result["platform_baseline_source"]

    workspace_tokens = to_tokens(baseline_chars) + to_tokens(skills_data["metadata_chars"])

    # The headline verdict is for the built-in DEFAULT agent - what loads when no
    # custom agent is selected (all always-steering + skill metadata). This is the
    # representative case; custom agents load a declared subset (see the per-agent table).
    controllable_tokens = workspace_tokens
    total_session_start = platform_tokens + controllable_tokens
    layers = [
        ("Platform baseline - Kiro's own harness (estimate, no public measurement)", platform_tokens, False),
        ("Workspace - all always-steering + skill metadata", workspace_tokens, True),
    ]
    v = _verdict(total_session_start, platform_tokens, controllable_tokens, findings)
    findings_section = _findings_section(findings)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Kiro session baseline: {_e(workspace_path.name)}</title>
<style>{CSS}</style></head><body>

<div class="hero"><div class="wrap">
<h1>Kiro session baseline</h1>
<div class="meta">{_e(workspace_path)} &middot; {datetime.now().strftime("%Y-%m-%d %H:%M")} &middot; estimates at ~{CHARS_PER_TOKEN} chars/token</div>
</div></div>

<div class="wrap">

<div class="verdict" style="--vcolour:{v['colour']}; --vsoft:{v['soft']}">
  <div class="status">{_e(v['status'])}</div>
  <div class="scope">Rating for the <strong>default agent</strong> &middot; ~{total_session_start:,} tokens load at session start</div>
  <p>{_e(v['message'])}</p>
</div>

<h2>Analysis summary <span class="hint">the default agent: what loads when no agent is selected</span></h2>
<table>
  <tr><th>layer</th><th class="num">tokens</th><th>can you reduce it?</th></tr>
  {_analysis_summary_table("Kiro", layers, total_session_start)}
</table>
<p class="footnote">This is the built-in default agent (loads all <strong>always</strong> steering). A session running a
custom agent loads only its declared subset - see the per-agent table for each agent's own total. The verdict
grades the whole total (fixed platform + your controllable layer), because that is what every message actually pays.</p>

<h2>How this compares <span class="hint">fixed platform harness sizes, for context</span></h2>
<table>
  <tr><th>tool</th><th class="num">platform baseline</th><th>basis</th></tr>
  {_reference_anchors_table()}
</table>
<p class="footnote">The verdict grades the total session floor (fixed platform baseline + your controllable layer). Bands: <strong>&le;10K</strong> lean &middot; <strong>10-20K</strong> moderate &middot; <strong>&gt;20K</strong> heavy. A Kiro session starts at ~10K before any files, so it is moderate at best; that is honest, not a fault of your setup.</p>

<div class="metrics">
  <div class="metric"><div class="n">{len(skills_data['skills'])}</div><div class="l">skills &middot; {to_tokens(skills_data['total_on_demand_chars']):,} tokens max on-demand</div></div>
  <div class="metric"><div class="n">{len(agents)}</div><div class="l">agents - each has its own session floor (see table)</div></div>
  <div class="metric"><div class="n">{workspace_tokens:,}</div><div class="l">default-agent workspace tokens (all always-steering)</div></div>
</div>

{findings_section}

<h2>All steering files <span class="hint">inventory; what actually loads depends on the agent</span></h2>
<table>
  <tr><th>file</th><th>inclusion</th><th class="num">lines</th><th class="num">tokens</th></tr>
  {_always_loaded_rows_kiro(steering)}
</table>
<p class="footnote">
  This is every steering file in the workspace, not what loads in one session. A session runs
  <strong>one agent</strong>, and each agent loads only the steering files it declares (see the agents table below).
  Kiro's built-in default agent (no <code>--agent</code> selected) loads all <strong>always</strong> files.<br>
  <strong>always</strong> - loaded by the default agent &nbsp;|&nbsp;
  <strong>fileMatch</strong> - loaded when matching files are in context &nbsp;|&nbsp;
  <strong>manual</strong> - only when explicitly invoked (not in baseline)
</p>

<h2>Skills <span class="hint">metadata upfront, full content on demand</span></h2>
<table>
  <tr><th>#</th><th>skill / sub-file</th><th class="num">metadata<br><span style="font-weight:400;font-size:11px">always</span></th>
  <th class="num">index<br><span style="font-weight:400;font-size:11px">on trigger</span></th>
  <th class="num">sub-files<br><span style="font-weight:400;font-size:11px">per task</span></th>
  <th class="num">max on-demand</th></tr>
  {_skills_rows(skills_data)}
</table>
<p class="footnote">Only the metadata column counts toward a session floor. Sub-files load one at a time.
Kiro's own context display may round skill metadata to 0.0%; this report shows the estimated
character-based figure, so a small difference from what Kiro displays is expected.</p>

<h2>Per-agent session floor <span class="hint">a session runs one agent; this is what each actually loads</span></h2>
<table>
  <tr><th>#</th><th>agent</th><th>loads</th>
  <th class="num">prompt<br><span style="font-weight:400;font-size:11px">tokens</span></th>
  <th class="num">workspace<br><span style="font-weight:400;font-size:11px">prompt+steering+skills</span></th>
  <th class="num">session floor<br><span style="font-weight:400;font-size:11px">+ platform</span></th>
  <th>verdict<br><span style="font-weight:400;font-size:11px">total-based</span></th></tr>
  {_agents_rows_kiro(agents, platform_tokens)}
</table>
<p class="footnote">
  A JSON agent declares its steering and skills in <code>resources</code>; a prompt-only <code>.md</code> agent's
  whole body is its system prompt. Session floor = platform baseline ({platform_tokens:,}) + the agent's prompt +
  its declared steering + its declared skills' metadata. The verdict grades that total against the same
  &le;10K / 10-20K / &gt;20K bands.
</p>

<h2>How to read this report</h2>
<table>
  <tr><td><strong>Session floor</strong></td><td>The minimum token cost of any session in this workspace. Every message you send pays this, before your question and before any response.</td></tr>
  <tr><td><strong>Workspace tokens</strong></td><td>The part you can reduce. The platform baseline ({platform_tokens:,} tokens) is fixed - Kiro's own system prompt.</td></tr>
  <tr><td><strong>Token estimate</strong></td><td>~{CHARS_PER_TOKEN} characters per token. A planning figure, not a billing figure. Actual counts may vary by model and content.</td></tr>
  <tr><td><strong>Platform baseline source</strong></td><td>{_e(platform_source)}</td></tr>
</table>

</div></body></html>"""


def _render_claude(result: dict) -> str:
    workspace_path   = result["workspace_path"]
    always_loaded    = result["always_loaded"]
    subagents        = result["subagents"]
    slash_commands   = result["slash_commands"]
    skills_data      = result["skills"]
    findings         = result["findings"]
    baseline_chars   = result["baseline_chars"]
    platform_tokens  = result["platform_baseline_tokens"]
    platform_source  = result["platform_baseline_source"]

    workspace_tokens = to_tokens(baseline_chars) + to_tokens(skills_data["metadata_chars"])
    session_floor    = platform_tokens + workspace_tokens
    # Claude Code has no agent selection at session start, so the controllable layer
    # is the main-session workspace (CLAUDE.md + imports + skill metadata).
    controllable_tokens = workspace_tokens
    v = _verdict(session_floor, platform_tokens, controllable_tokens, findings)
    findings_section = _findings_section(findings)

    layers = [
        ("Platform baseline - Claude Code's own harness (Claude 4.x estimate)", platform_tokens, False),
        ("Workspace - CLAUDE.md + @imports + skill metadata", workspace_tokens, True),
    ]

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Claude Code session baseline: {_e(workspace_path.name)}</title>
<style>{CSS}</style></head><body>

<div class="hero"><div class="wrap">
<h1>Claude Code session baseline</h1>
<div class="meta">{_e(workspace_path)} &middot; {datetime.now().strftime("%Y-%m-%d %H:%M")} &middot; estimates at ~{CHARS_PER_TOKEN} chars/token</div>
</div></div>

<div class="wrap">

<div class="verdict" style="--vcolour:{v['colour']}; --vsoft:{v['soft']}">
  <div class="status">{_e(v['status'])}</div>
  <div class="scope">Rating for the <strong>main session</strong> &middot; ~{session_floor:,} tokens load at session start</div>
  <p>{_e(v['message'])}</p>
</div>

<h2>Analysis summary <span class="hint">the main session: what loads before you type</span></h2>
<table>
  <tr><th>layer</th><th class="num">tokens</th><th>can you reduce it?</th></tr>
  {_analysis_summary_table("Claude Code", layers, session_floor)}
</table>
<p class="footnote">Claude Code has no agent to select, so this is the main session, the same every time.
Each subagent invoked mid-task re-pays this baseline plus its own body (see the subagents table).
The verdict grades the whole total (fixed platform + your controllable layer), because that is what every message actually pays.</p>

<h2>How this compares <span class="hint">fixed platform harness sizes, for context</span></h2>
<table>
  <tr><th>tool</th><th class="num">platform baseline</th><th>basis</th></tr>
  {_reference_anchors_table()}
</table>
<p class="footnote">The verdict grades the total session floor (fixed platform baseline + your controllable layer). Bands: <strong>&le;10K</strong> lean &middot; <strong>10-20K</strong> moderate &middot; <strong>&gt;20K</strong> heavy. Claude Code's ~33K platform baseline alone puts every session in the heavy band regardless of your files - the controllable row is the only part you can move.</p>

<div class="metrics">
  <div class="metric"><div class="n">{len(skills_data['skills'])}</div><div class="l">skills &middot; {to_tokens(skills_data['total_on_demand_chars']):,} tokens max on-demand</div></div>
  <div class="metric"><div class="n">{len(subagents)}</div><div class="l">subagents - each runs in its own isolated context</div></div>
</div>

{findings_section}

<h2>Always loaded <span class="hint">CLAUDE.md and its @file imports, every session</span></h2>
<table>
  <tr><th>file</th><th>role</th><th class="num">lines</th><th class="num">tokens</th></tr>
  {_always_loaded_rows_claude(always_loaded)}
</table>

<h2>Skills <span class="hint">metadata upfront, full content on demand</span></h2>
<table>
  <tr><th>#</th><th>skill / sub-file</th>
  <th class="num">metadata<br><span style="font-weight:400;font-size:11px">always</span></th>
  <th class="num">index<br><span style="font-weight:400;font-size:11px">on trigger</span></th>
  <th class="num">sub-files<br><span style="font-weight:400;font-size:11px">per task</span></th>
  <th class="num">max on-demand</th></tr>
  {_skills_rows(skills_data)}
</table>
<p class="footnote">Only the metadata column counts toward the session floor. Sub-files load one at a time.</p>

<h2>Subagents <span class="hint">each runs in its own isolated context, re-paying the baseline</span></h2>
<table>
  <tr><th>#</th><th>subagent</th><th>description</th>
  <th class="num">own body<br><span style="font-weight:400;font-size:11px">tokens</span></th>
  <th class="num">isolated floor<br><span style="font-weight:400;font-size:11px">platform + workspace + body</span></th></tr>
  {_subagents_rows_claude(subagents, platform_tokens, workspace_tokens)}
</table>
<p class="footnote">
  A subagent does <strong>not</strong> add to the main session floor. It runs in its own isolated
  context: it re-pays the platform baseline and the always-loaded workspace layer (CLAUDE.md + imports
  + skill metadata), then adds its own system prompt body. It does not inherit the parent conversation.
  The cost to watch is the <strong>multiplier</strong> - running N subagents in parallel re-pays that
  baseline N times.<br>
  <em>Note:</em> whether skill metadata re-loads into a subagent's context is undocumented and
  version-dependent - Anthropic's docs say subagents start without inherited skills, but observed
  behaviour often includes the skill listing. This floor includes skill metadata as a conservative
  upper bound, matching the main-session definition.
</p>

<h2>Slash commands <span class="hint">load only when the command is called</span></h2>
<table>
  <tr><th>command</th><th class="num">lines</th><th class="num">tokens</th></tr>
  {_slash_commands_rows(slash_commands)}
</table>

<h2>How to read this report</h2>
<table>
  <tr><td><strong>Session floor</strong></td><td>The minimum token cost of any session in this workspace. Every message you send pays this, before your question and before any response.</td></tr>
  <tr><td><strong>Workspace tokens</strong></td><td>The part you can reduce. The platform baseline ({platform_tokens:,} tokens) is fixed - Claude Code's own system prompt.</td></tr>
  <tr><td><strong>Token estimate</strong></td><td>~{CHARS_PER_TOKEN} characters per token. A planning figure, not a billing figure. Actual counts may vary by model and content.</td></tr>
  <tr><td><strong>Platform baseline source</strong></td><td>{_e(platform_source)}</td></tr>
</table>

</div></body></html>"""


# ── public entry point ────────────────────────────────────────────────────────

def build_report(result: dict, output_dir: Path) -> Path:
    """Build an HTML report from scanner results, write it to output_dir, return the path."""
    platform = result["platform"].lower().replace(" ", "_")
    workspace_name = result["workspace_path"].name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"session-baseline-{platform}-{workspace_name}-{timestamp}.html"

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / filename

    if result["platform"] == "Kiro":
        content = _render_kiro(result)
    else:
        content = _render_claude(result)

    report_path.write_text(content, encoding="utf-8")
    return report_path
