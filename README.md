# lapbar 🎢

> 🇰🇷 **한국어**: [README.ko.md](README.ko.md)

*Named after the lap bar on a roller coaster: it keeps you in your seat, it is not a cage.*

**An expensive model for thinking, cheap models for the legwork - a Claude Code harness enforced by hooks, plus a report that shows how much it saved.**

> One line: `lapbar install` drops a small set of Claude Code hooks, rules and skills into `~/.claude`; `lapbar report` reads your own transcripts and shows how much cheaper your subagents were than running everything on the head model.

## Install

```
# From PyPI (coming soon)
pipx install lapbar                 # or: uv tool install lapbar

# From source (works today)
git clone https://github.com/mysoul7306/lapbar && cd lapbar
pipx install .                      # or: pip install .
```

Then:

```
lapbar install --dry-run            # what would be installed, nothing written
lapbar install                      # hooks + rules + skills + agent into ~/.claude, settings.json wired (backup kept)
lapbar doctor                       # everything in place, hook self-tests pass
lapbar report --since 2026-10-13    # your own savings, from your own transcripts
lapbar session                      # one session: hook injection, rules loaded, subagent models, tokens
```

## Why

The most expensive thing in Claude Code is the head model doing the legwork. Subagent work - scanning files, review lenses, collection - is just as good on a cheaper model, but an `Agent` / `Workflow` call that doesn't name a model inherits the head model. So hooks block that.

- **model and effort must be literals** - `subagent-model-guard.py` denies an `Agent` / `Task` / `Workflow` call whose model or effort is missing, a variable or a ternary. The top tier (fable) and the bottom tier (haiku, blocked by default after quality incidents) pass only with an approval tag inside that same call, or haiku in role mode with a role tag and a verify tag. Tags, settings and the constructs refused in Workflow scripts: [The model guard in detail](#the-model-guard-in-detail).
- **lane loop** - `/lane` (the head only decodes, decomposes and synthesizes; read is opus high, collect is sonnet low, refute is opus xhigh) and `/review-lane` (3 lenses → 2 refuters, agent cap 10) turn that split into a procedure.
- **protected-branch push block** - `git-push-guard.py` denies pushes to develop / main / master, resolving refspec and upstream to decide, and failing closed when it can't. Merges by the AI, pushes by a human.
- **memory hygiene** - `memory-lint-hook.py` checks the memory directory for broken links, public IPs, secrets and router size at session start.

Both guards are **regex-based**: a safety net against the AI accidentally pushing or forgetting a model, not a sandbox against deliberate evasion (eval, config injection, shell tricks). Known evasion shapes are closed one by one as self-test cases (push 60, model 153), and the model guard's JS reader is checked against a real parser by a differential fuzz (`tools/fuzz/`, 0 bypasses in 60,000 scripts on this version); an AST-parser rewrite is the next milestone. What that means for `Workflow` scripts is listed in [What a Workflow script may not contain](#what-a-workflow-script-may-not-contain).

## How much it saves

`lapbar report` reads the transcripts (jsonl) under `~/.claude/projects/`, collects per-request model and tokens (input, output, cache read / write), and compares actual cost against a **counterfactual: every subagent request re-priced at the head model's rate**. It needs no before/after setup, so numbers show up on day one.

Measured by the person who built this harness (10 days since turning the hooks on 2026-10-13, `lapbar report --since 2026-10-13`, run on the night of 2026-10-22):

| Metric | Value |
|---|---|
| Requests | 16,051 (head 6,501, subagent 9,550, of which workflow 8,031) |
| Actual cost (list-price estimate) | $2,897.65 |
| If every subagent had run on the head model | $3,929.62 |
| **Savings** | **$1,031.97** - 26.3% of the counterfactual, 35.6% of actual spend |
| Subagent effort | high 65%, xhigh 33%, medium 2% |
| Share of cache writes at 1-hour TTL | 40% (1-hour writes priced at input rate × 2) |

97% of subagent requests ran on opus (fable 2%, opus 4.8 1%); the savings are that rate gap. Head-side cost (~$1,507) is left untouched, so looking at the subagent share alone, it drops below half.

- Rates are public list price (`src/lapbar/prices.json`, dated) × tokens, so they are an **estimate**, not a real invoice. Override with `--prices`.
- Each requestId is counted once (one response leaves a record per content block; the record with the largest output is kept). `<synthetic>` is excluded. Head vs subagent is classified by file path (`<session>.jsonl` vs `subagents/`), which assumes the post-2026-10 transcript layout.
- Give the head model with `--head`, or it is inferred as the costliest model on the head side (shown as `inferred` in the output).
- To see your own numbers, dump them with `lapbar report --json > report.json`.

## What gets installed

| Kind | File | What it does |
|---|---|---|
| hook | `hooks/subagent-model-guard.py` (PreToolUse: Task, Agent, Workflow) | forces model and effort literals, top and bottom tiers need a tag (or the haiku role tags in role mode). `--self-test` 153 cases |
| hook | `hooks/git-push-guard.py` (PreToolUse: Bash) | denies develop / main / master push, fail-closed. `--self-test` 60 cases |
| hook | `hooks/memory-lint-hook.py` (SessionStart) + `scripts/memory-lint.py` | checks the memory directory, notifies only on problems |
| hook | `hooks/git-readonly-guard.py` (agent-scoped PreToolUse: Bash, not wired into `settings.json`) | allowlist for read-only git inside the `scout-git` agent: pipes, redirects, write subcommands and unknown options are denied, `--no-ext-diff` is forced. `--self-test` 76 cases |
| rule | `rules/zsh-pitfalls.md` | silent-failure traps in the Bash tool's zsh (incident-based) |
| rule | `rules/security.md` | output hygiene - keep values from leaking when you read config, env, wrappers |
| rule | `rules/memory-scope.md` | four buckets for deciding which scope a memory belongs in |
| rule | `rules/subagent-review.md` | review-lane size, model tiers, the read-only principle |
| rule | `rules/git-workflow.md` | branches, commits, worktrees, push permissions |
| skill | `commands/lane.md`, `commands/review-lane.md` + `scripts/*-workflow.js` | the lane loop and review-lane procedures |
| agent | `agents/review-readonly.md` | a read-only reviewer with only Read, Grep, Glob (the lane's refute / review role) |
| agent | `agents/scout-git.md` | a git history scout for planning ("did this exist before, when and why") whose Bash is limited to read-only git by the hook above, declared in its own frontmatter |

`lapbar install` **never overwrites your files** (if contents differ it skips and tells you; use `--force` to overwrite). Before writing `settings.json` it keeps a `settings.json.bak-<time>`, and it won't re-add a hook wiring that is already there.

`lapbar doctor` checks that every asset is in place and wired, runs each hook's `--self-test`, feeds each guard a sample input and checks that it really denies or allows (a guard that fails to load is otherwise silent), and verifies that every hook command in `settings.json` (lapbar's or not) points at a file that exists and compiles.

`lapbar session` reads one session transcript (the current one when run inside Claude Code, else `--latest` or a path) and prints, in under 40 lines, how many characters each hook injected (head session only; subagent transcripts are not scanned for hooks yet), which instruction files were loaded and how big they are, which subagent types ran on which model and effort, and the token usage on both sides. Use it before cleaning up memory or trimming rules.

## Using it (short)

Assumes a Claude Code where the `Agent` tool has an `effort` parameter and the `Workflow` tool exists (latest as of 2026-10). Without those, use only the model guard and skip `/lane` and `/review-lane`.

1. `lapbar install` → restart Claude Code
2. When you call a subagent, name the model and effort (fable and haiku also need a tag, see below). If you don't, the hook blocks it and shows why.
3. Review with `/review-lane`, long tasks with `/lane`.
4. Occasionally look at the numbers with `lapbar report`, and at one session with `lapbar session`.
5. For "was this ever here before?" questions during planning, hand the question to the `scout-git` agent; its git is read-only.

What the guard checks, the tags, and the optional `settings.json` `env` keys (effort cap, haiku role mode, owner name) are in [The model guard in detail](#the-model-guard-in-detail).

Hook messages are English. The rule files and the `/lane`, `/review-lane` procedures are in Korean (the working language of the person who uses this harness). Copy and edit them as you like - that is why `install` never overwrites.

## The model guard in detail

`subagent-model-guard.py` runs before every `Agent`, `Task` and `Workflow` call. It reads the call statically (the `Agent` input, or the text of the Workflow script) and either lets it through or denies it with a reason that names what to change. It never edits the call.

### What must be in the call

| Field | Rule |
|---|---|
| `model` | one string literal naming a tier. `opus` and `sonnet` pass as they are; `fable` and `haiku` need a tag (next table). Full IDs such as `claude-opus-5-5` are mapped to their tier. `inherit`, variables, ternaries, parenthesized expressions and names with two tier words (`opus-haiku`) are denied. Allowed tiers live in the hook's single `TIERS` table |
| `effort` | one lowercase literal: `low`, `medium`, `high`, `xhigh`, `max`. Anything above `SUBAGENT_EFFORT_CAP` needs a tag (with the default cap, that is `max`). An omitted effort would inherit the session effort, so it is denied |
| Workflow `agent()` opts | one flat object literal. An object chosen by a ternary, spread, computed keys, getters / setters, identifier escapes and shorthand (`{effort}`) are denied. A key counts only right after `{` or `,`, so `label: c ? model : 'x'` is not a `model` key. If a key repeats, the last one wins, as in JS |
| fable count | at most 2 fable `agent()` call sites per Workflow script, even with tags |

### Tags

A tag is plain text inside the same call: the `prompt` of an `Agent` / `Task`, or anywhere inside the parentheses of a Workflow `agent()` (label, prompt, even a comment). The hook cannot know whether the owner really approved; writing a tag without approval is a rule violation, not something it detects.

| Tag | Lets through |
|---|---|
| `[owner-approved: fable]` | fable (up to the 2-site cap) |
| `[owner-approved: haiku]` | haiku at any effort up to the cap (above the cap the effort tag is still needed) |
| `[owner-approved: max]` (or another level) | that effort level above the cap |
| `[haiku role: <mechanical job>]` **and** `[verify: <check the parent will run>]` | haiku at effort `medium` or `high`, only while role mode is on (next table). `low`, `xhigh` and `max` are denied |

Role mode is meant for work the parent verifies mechanically: test-result aggregation, lint queue cleanup, grep summaries, checksum comparison. The role range is medium to high: a job that needs more thinking than that is not a haiku job.

`owner` is the default name in the tags; `SUBAGENT_GUARD_OWNER` replaces it (`[alice-approved: fable]`).

### Settings (`env` block of `settings.json`)

`lapbar install` does not write these. Claude Code passes the `env` block of `settings.json` to hooks; a terminal shell does not, so the hook's `--self-test` prints the values it finds in the `settings.json` of the Claude home it is installed in (the raw values, before any fallback).

| Key | Default | Effect |
|---|---|---|
| `SUBAGENT_EFFORT_CAP` | `xhigh` | highest effort allowed without a tag: one of `low`, `medium`, `high`, `xhigh`, `max`. Any other value falls back to `xhigh` |
| `SUBAGENT_HAIKU_ROLE_MODE` | off | `role` turns on the haiku role tags; any other value is off |
| `SUBAGENT_GUARD_OWNER` | `owner` | name inside the approval tags. A value left as a `<placeholder>` counts as unset |

If you set the owner name, replace `owner-approved` with the same name in the installed rules and procedures too (`rules/subagent-review.md`, `commands/lane.md`, `commands/review-lane.md`, `scripts/lane-workflow.js`; `grep -rl owner-approved ~/.claude` finds them all). Otherwise the rules tell the model to write a tag the hook no longer accepts.

```
"env": {
  "SUBAGENT_EFFORT_CAP": "xhigh",
  "SUBAGENT_HAIKU_ROLE_MODE": "role",
  "SUBAGENT_GUARD_OWNER": "alice"
}
```

### What a Workflow script may not contain

To find `agent()` calls the hook blanks strings and comments with a small state machine. It is not a JS parser, so a few constructs are refused outright because they could open a string or comment state at the wrong place and hide an `agent()` call from it. Every deny names the construct and the fix.

| Refused | Why | Do instead |
|---|---|---|
| a regex literal `/…/` where JS reads one: at the start of the script, after an operator or one of `( [ { } , ; :`, after a spread, after a keyword such as `return`, or after the `)` of an `if` / `while` / `for` / `with` header | a quote or a comment opener inside the regex pairs up with later text, and everything in between is blanked | `new RegExp('…')` |
| a division followed on the same line by an unpaired quote and another `/` (`n / 2; const u = 'a/b'`), or by a comment glued to a word (`x/2//c`) | the same shape as a misread regex; this is the safety net for contexts the hook cannot classify | split the line, or put a space before the comment |
| a backslash outside strings and comments | a regex body with escaped slashes, or an identifier escape such as `\u006dodel` | `new RegExp('…')`, plain identifiers |
| an unescaped newline or CR inside a `'` / `"` string (a `\` line continuation is fine), U+2028 / U+2029 anywhere, `<!--` or `-->` in code | JS treats these as line ends or comments, the hook would not | template literals for multi-line text, remove the separators |
| `workflow()` whose target is not a string literal or `{scriptPath: '<literal>'}`, or `workflow` used other than as a direct call | the child script never reaches the hook as a tool call, so the hook reads the target file (a saved name under `.claude/workflows/`, or a path relative to the session cwd) and checks it the same way, up to 3 levels | a literal name or path |
| `agent` used other than as a direct call (alias, argument, property), or reached through `eval`, `Function`, `globalThis` | call sites cannot be counted | call `agent(...)` directly |

Division after an identifier, a number, `]` or a non-header `)` is read as division: `Math.round(budget.remaining()/1000)`, `done / total + '%' // progress` and `a / b + ' of ' + c / d` pass.

### Checking it

`python3 ~/.claude/hooks/subagent-model-guard.py --self-test` runs 153 regression cases and prints the env values it found. `lapbar doctor` runs that self-test and three probes (missing model, valid call, haiku without a tag) against the installed copy. The hidden-call shapes found so far are listed in `CHANGELOG.md`.

## What was removed from the public release

The rules, lane procedures and the `review-readonly` agent in this repo were extracted from one person's live `~/.claude` with **`lapbar bundle`**; the hooks and the `scout-git` agent are authored here (marked `keep` in the manifest, so `bundle` never overwrites them). The substitution table (`scrub_map.json`) holds the originals, so it lives only outside the repo (`$LAPBAR_SCRUB_MAP`) and the public code carries only digests. The extraction substituted or dropped terms of address, real names, accounts, home paths, project and company names, vendors, wrapper names, IPs and token patterns. `lapbar bundle --check` is the gate: in CI it looks only at generic patterns (home paths, IPs, JWTs, credential assignments, memory file names), while proper nouns (kept as digests) and terms of address are checked locally by the maintainer with `--require-map`. The external-access ledger hook (trust-ledger) is tightly coupled to a company wrapper table, so it is coming in a later release.

## Development

```
git clone https://github.com/mysoul7306/lapbar && cd lapbar
python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m lapbar.cli --help
```

Zero dependencies (Python 3.11+ standard library only).

## License

**MIT** - use, modify, share and sell it freely; keep the copyright notice. Full text in `LICENSE`.

**Why it is public** - using an expensive model for thinking and cheap models for the legwork is just sensible spending, and a sensible habit should be something anyone can pick up without asking. So it is public, under MIT.

## Credits

- Author and copyright: **rok_root** (license: MIT)
- Co-author: **Claude** (Anthropic) - the hooks, rules and this tool were built together in Claude Code sessions; commits carry a `Co-Authored-By: Claude` trailer.
