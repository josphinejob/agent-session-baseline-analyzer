# agent-session-baseline-analyzer

Shows how many tokens load before you type anything in a Kiro or Claude Code
workspace — broken down by steering files, skills, and agents — so you know
your session cost floor and can act on it.

This is different from the in-session token display your AI assistant shows.
That tells you how much of the window is used right now. This tool reads your
workspace configuration before starting any session, so you can see the floor
and reduce it without burning tokens to measure it.

> Background: [How to measure what your AI session actually costs](https://medium.com/@tojosphine/how-to-measure-what-your-ai-session-actually-costs-c270a465e5d1)
> — the reasoning behind this tool.

## What it catches

- **(Kiro)** Steering files with an unrecognised inclusion mode that silently load every session
- **(Claude Code)** `@file` imports in CLAUDE.md that are inside a code fence (inert — the file never loads)
- **(Claude Code)** `@file` imports that point to files that do not exist on disk
- **(Both)** Skills with no description (they will never be triggered)
- **(Claude Code)** A `~/.claude/CLAUDE.md` that adds to your session cost from outside the project

## Prerequisites

**Python 3.10 or later.** That is the only requirement. There are no third-party
packages to install — the tool uses only the Python standard library. No API key,
no credentials, no network access.

Check your Python version:

```bash
python3 --version
```

If it prints `Python 3.10.x` or higher, you are ready. If `python3` is not found
or the version is older, install a current Python:

- **macOS:** `brew install python` (or download from [python.org](https://www.python.org/downloads/))
- **Windows:** download from [python.org](https://www.python.org/downloads/) and tick "Add Python to PATH" during install
- **Linux (Debian/Ubuntu):** `sudo apt install python3`

## Installation

Clone (or download) this repository — that is the whole install:

```bash
git clone https://github.com/josphinejob/agent-session-baseline-analyzer.git
cd agent-session-baseline-analyzer
```

There is no `pip install` step and no virtual environment to set up, because the
tool imports nothing outside the standard library.

## Usage

The path you pass is the workspace you want to analyze — any repo that has a
`.kiro/` folder or a `CLAUDE.md`. The analyzer itself is a separate tool; you
run it and point it at your target repo.

`cd` into the analyzer once, then pass the path to the repo you want to measure:

```bash
cd /path/to/agent-session-baseline-analyzer

python3 agent_session_baseline_analyzer.py <path-to-target-repo> [platform]
```

```bash
# Auto-detect the workspace type
python3 agent_session_baseline_analyzer.py ~/development/my-kiro-project
python3 agent_session_baseline_analyzer.py ~/development/my-claude-project

# Force a platform explicitly (skips auto-detection)
python3 agent_session_baseline_analyzer.py ~/development/my-project kiro
python3 agent_session_baseline_analyzer.py ~/development/my-project claude
```

The optional `[platform]` argument is `kiro` or `claude`. When omitted, the script
detects the workspace type from the target repo's folder structure:
- `.kiro/` folder found → Kiro
- `CLAUDE.md` or `.claude/` folder found → Claude Code

### Workspaces with both Kiro and Claude Code

This tool analyzes one platform per run. It does not merge or auto-resolve a workspace
that has both a `.kiro/` folder and a `CLAUDE.md`. If both are present, auto-detection
is ambiguous and the tool will stop and ask you to specify the platform:

```bash
python3 agent_session_baseline_analyzer.py ~/development/my-project kiro
python3 agent_session_baseline_analyzer.py ~/development/my-project claude
```

Run it once per platform to see each baseline separately.

A timestamped HTML report is written to a `reports/` folder inside the tool's own
directory and opened in your browser. Pass `--no-open` to skip the browser. The
`reports/` folder is git-ignored.

## Report sections

| Section | What it shows |
|---|---|
| Verdict + analysis summary | A worked example (platform + workspace + agent = session-start total) and a graded verdict on the part you control |
| Findings | Configuration problems that fail silently |
| All steering (Kiro) / Always loaded (Claude Code) | Kiro: inventory of every steering file (what loads depends on the agent). Claude Code: CLAUDE.md + its imports, which genuinely load every session |
| Skills | Metadata tokens (always) vs full content (on demand) |
| Per-agent session floor (Kiro) | Each agent's real floor — the steering and skills it declares, plus its prompt |
| Subagents (Claude Code) | Each subagent's isolated-context floor — it re-pays the baseline plus its own body |
| Slash commands (Claude Code) | Load only when the command is called |

### The verdict banner and analysis summary

Each report opens with a coloured verdict and an **analysis summary** — a worked example showing what actually loads when a session starts, split into the fixed platform baseline and the part you control:

| Layer | Tokens | Can you reduce it? |
|---|---|---|
| Platform baseline (the tool's own harness) | e.g. 10,000 | No — fixed |
| Workspace (steering / CLAUDE.md + skill metadata) | e.g. 8,000 | Yes |
| **Total context at session start** | **18,000** | |

The verdict is given **per agent**, not for the workspace as a whole — because there is no single workspace floor. A session runs one agent, and each agent has its own total. The headline grades the representative case: the **default agent** for Kiro (what loads when you select none), and the **main session** for Claude Code (which has no agent to select). Kiro's per-agent table then tags every agent with its own verdict.

**The verdict grades the total session floor — platform baseline plus your controllable layer — not just your files.** A 33K session is heavy no matter how small your own config is; calling it "lean" because your part is small would be dishonest. The reasoning and the exact numbers are printed in the report next to the verdict.

| Total session floor | Verdict | Meaning |
|---|---|---|
| ≤ 10,000 | LEAN | About as lean as a real coding-agent session gets |
| 10,001 – 20,000 | MODERATE | Workable |
| > 20,000 | HEAVY | Every message pays a lot before you type; the controllable layer is where any reduction must come from |
| any critical finding | FIX FIRST | Part of your instruction layer is not loading as intended — overrides the token verdict |

The thresholds are anchored to real platform harness sizes (below): Kiro sits at ~10K before any files, Claude Code at ~33K. A consequence worth noting: because Kiro's platform baseline alone is ~10K, a Kiro session is **moderate at best** — never "lean" — and Claude Code's ~33K puts every session in the **heavy** band regardless of your files. That is the honest reading, not a fault in your setup.

**Reference — fixed platform harness sizes** (what you pay before adding anything):

| Tool | Platform baseline | Basis |
|---|---|---|
| Kiro | ~10K | estimate — no public measurement |
| Claude Code 4.x | ~33K | measured early 2026 |
| Claude Code 5.x | ~30K | only the prompt-rules portion was cut; tool definitions dominate and were not |

> **Disclaimer — read this.** This tool demonstrates a concept: that a measurable token floor loads before you type, and that it differs by platform and agent. The platform baseline figures above are **estimates fixed at design time**, using the most common setups then: **Kiro ~10K** (an estimate — no public measurement exists) and **Claude Code 4.x ~33K** (the most-used Claude Code generation at the time). They are **not auto-detected and will drift** as tools and models change (Claude 5.x/6.x, new Kiro versions, or running Claude Code against a non-Anthropic model via `ANTHROPIC_MODEL`). The tool cannot measure a harness it never runs — these numbers come from published measurements. To adapt it to your setup, edit the constants near the top of each scanner: `KIRO_PLATFORM_BASELINE_TOKENS` in `workspace_scanners/kiro_scanner.py` and `CLAUDE_CODE_PLATFORM_BASELINE_TOKENS` in `workspace_scanners/claude_code_scanner.py`, and the verdict bands (`LEAN_MAX_TOTAL`, `MODERATE_MAX_TOTAL`) in `html_report/html_report_builder.py`.

## How token costs work

### The calculation

Every message you send pays a token cost before your words are processed. This tool measures that floor.

```
Session floor = platform baseline + workspace tokens

Platform baseline  = the AI assistant's own built-in harness
                     (system prompt + tool definitions + session scaffolding)
                     Fixed. You cannot reduce it.

Workspace tokens   = your configuration files that load at session start
                     (steering files, CLAUDE.md, skill metadata)
                     Variable. This is what this tool measures and what you can reduce.
```

### Platform baseline — what it includes and why it differs by tool

**Claude Code** (the Anthropic CLI product)

Claude Code injects its own harness before your first message: a built-in system prompt, tool schemas (read_file, write_file, bash, etc.), and session scaffolding. As of early 2026 this totalled approximately 33K tokens for Claude 3.x and 4.x models.

Anthropic cut this on July 24, 2026 for Claude 5 models (Opus 5, Fable 5) — removing over 80% of the system prompt's *behavioural-rules text* (roughly 12,443 → 2,308 characters by one measurement). That cut applies to the instruction text, not the whole harness: the bulk of the 33K is **tool definitions**, which were not cut. So the 5.x baseline is only modestly lower — on the order of ~30K, not dramatically reduced. (One measurement even found the Opus 5 system prompt grew versus 4.8.)

**Model matters for Claude Code** because Anthropic redesigns the harness alongside each model generation. If you upgrade your Claude Code model, the platform baseline changes — but expect a modest shift, not a collapse, because tool definitions dominate.

| Claude Code model | Approximate platform baseline |
|---|---|
| Claude 3.x / 4.x (e.g. Sonnet 4.6) | ~33K tokens |
| Claude Opus 5 / Fable 5 | ~30K tokens (only the prompt-rules text was cut) |

**Kiro** (the AWS coding assistant CLI)

Kiro uses Claude, GPT-5.6, and other models as its underlying LLM, but it calls them via API — it does not use Claude Code's harness. Kiro's platform baseline is its own built-in system prompt and tool definitions, not Claude Code's. The underlying model adds no overhead on top of this.

```
Kiro session floor = Kiro's own harness (~10K estimated)
                   + your .kiro/ workspace files

NOT:               Kiro harness + Claude Code harness + your files
```

Kiro bills via credits rather than tokens, and no public independent measurement of Kiro's baseline exists as of September 2026. The ~10K figure is an estimate from workspace research. The model you choose inside Kiro (Claude vs GPT-5.6) does not change the baseline this tool reports, because Kiro's own harness is constant regardless of model choice.

### How agents change the picture — and why the two platforms differ

The session floor is not one fixed number, because both tools load different context depending on the agent. But they do this in opposite ways, so the report treats them differently.

**Kiro — a session runs one selected agent**

In Kiro you pick an agent for the session (for example `kiro chat --agent architect`). That agent's config file declares, in its `resources` list, exactly which steering files and skills it loads:

```json
"resources": [
  "file://.kiro/steering/workflow-rules.md",
  "file://.kiro/steering/architect.md",
  "skill://.kiro/skills/documentation-standards/SKILL.md"
]
```

So the session floor is **agent-specific**. An agent that declares 3 steering files does not pay for the other steering files in the workspace. The report's "All steering files" section is therefore an *inventory* of everything present — not what loads in any one session — and the "Per-agent session floor" table shows each agent's real cost: its prompt + its declared steering + its declared skills' metadata, plus the fixed platform baseline.

Kiro's built-in default agent (when you select no custom agent) is the exception: it loads all `always`-inclusion steering. That is what the headline metric cards report.

**Claude Code — the main session is fixed; subagents are isolated**

Claude Code has no agent selection at session start. The main session always loads `CLAUDE.md` and its `@file` imports, regardless of the task — so for the main session, "always loaded" really is always loaded.

Subagents (`.claude/agents/*.md`) work differently from Kiro agents. The main agent delegates a task to a subagent, and each subagent runs in its **own isolated context window** with its own system prompt. It does not inherit the parent conversation. Crucially, it still re-loads the project's `CLAUDE.md` at startup — so a subagent's floor is:

```
platform baseline + CLAUDE.md + @imports + the subagent's own system prompt body
```

That is *higher* than the main session floor, not a smaller add-on. The cost that catches people out is the **multiplier**: running several subagents in parallel re-pays the `CLAUDE.md` baseline once per subagent. The report shows each subagent's isolated floor for this reason.

| | Kiro | Claude Code |
|---|---|---|
| Agent chosen at session start? | Yes — one agent per session | No — main session is fixed |
| What sets the floor | The selected agent's declared resources | CLAUDE.md + imports (main); each subagent re-pays it |
| Subagent / agent context | Is the session | Separate isolated context per subagent |
| Loads CLAUDE.md / steering? | Only the subset the agent declares | Main + every subagent re-loads CLAUDE.md |

### Why these are estimates, not accurate counts

This tool estimates token count using the rule of thumb: `characters ÷ 4 ≈ tokens`. This is a planning figure only.

The actual token count for any given file depends on:
- **Which tokenizer the model uses** — Claude's tokenizer (BPE-based) gives different counts than GPT's tiktoken. The same file can differ by 10–20% between models.
- **Content type** — code tokenises differently from prose. Markdown symbols, repeated patterns, and unusual characters all affect the count.
- **Model version** — tokenisers can change between model releases.

For planning and comparison this estimate is accurate enough: if your session floor is 12,000 estimated tokens vs 6,000, the 2x difference is real regardless of the exact count. For billing accuracy, use your provider's token counter (`claude -p --output-format json` for Claude, the OpenAI tokenizer API for GPT).

### Summary table

| | Kiro | Claude Code (4.x) | Claude Code (5.x) |
|---|---|---|---|
| Platform baseline | ~10K (estimated) | ~33K (measured early 2026) | ~30K (only prompt-rules cut) |
| Baseline tied to model? | No — Kiro's harness is constant | Yes — redesigned for each model gen | Yes |
| Uses Claude internally? | Yes (via API) | N/A — is Claude | N/A |
| Inherits Claude Code's 33K? | No | N/A | N/A |
| Workspace tokens | `.kiro/` steering + skill metadata | `CLAUDE.md` + `@imports` + skill metadata | Same as 4.x |
| Can you reduce workspace tokens? | Yes | Yes | Yes |
| Token count method | ~4 chars/token estimate | ~4 chars/token estimate | ~4 chars/token estimate |

## Structure

```
agent-session-baseline-analyzer/
├── agent_session_baseline_analyzer.py   ← run this
├── workspace_scanners/
│   ├── kiro_scanner.py                  ← reads .kiro/ structure
│   └── claude_code_scanner.py           ← reads CLAUDE.md + .claude/ structure
├── html_report/
│   └── html_report_builder.py           ← converts scan results to HTML
└── reports/                             ← generated HTML output (git-ignored)
```

The scanners are separate because the two platforms load their configuration
differently. The report builder is shared — both scanners return the same
result shape so the builder does not need to know which platform produced it,
except for the final render step where platform-specific terminology is used
(steering files vs CLAUDE.md, subagents vs agents).

## What this is not

This tool does not measure dollar cost — token pricing changes frequently and
varies by model and tier. Use your AI provider's pricing page for that.

This tool does not inspect conversation history, MCP tool calls, or anything
that happens during a live session. It reads only the static configuration
files that load at session start.
