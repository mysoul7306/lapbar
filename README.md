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

- **model and effort must be literals** - `subagent-model-guard.py` denies a missing model, a variable, or a ternary in `Agent` / `Task` / `Workflow` calls. The top tier (fable) and the bottom tier (haiku, blocked by default after quality incidents) are allowed only with an `[owner-approved: <model>]` tag inside that same call. Effort follows the same rule (low / medium / high / xhigh literals). Allowed model names live in the hook's single `TIERS` table.
- **lane loop** - `/lane` (the head only decodes, decomposes and synthesizes; read is opus high, collect is sonnet low, refute is opus xhigh) and `/review-lane` (3 lenses → 2 refuters, agent cap 10) turn that split into a procedure.
- **protected-branch push block** - `git-push-guard.py` denies pushes to develop / main / master, resolving refspec and upstream to decide, and failing closed when it can't. Merges by the AI, pushes by a human.
- **memory hygiene** - `memory-lint-hook.py` checks the memory directory for broken links, public IPs, secrets and router size at session start.

Both guards are **regex-based**: a safety net against the AI accidentally pushing or forgetting a model, not a sandbox against deliberate evasion (eval, config injection, shell tricks). Known evasion shapes are closed one by one as self-test cases (push 60, model 80); an AST-parser rewrite is the next milestone.

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
| hook | `hooks/subagent-model-guard.py` (PreToolUse: Task, Agent, Workflow) | forces model and effort literals, top tier needs a tag. `--self-test` 80 cases |
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
2. When you call a subagent, name the model and effort. If you don't, the hook blocks it and shows why.
3. Review with `/review-lane`, long tasks with `/lane`.
4. Occasionally look at the numbers with `lapbar report`, and at one session with `lapbar session`.
5. For "was this ever here before?" questions during planning, hand the question to the `scout-git` agent; its git is read-only.

The rule files are in Korean (the working language of the person who uses this harness). Copy and edit them as you like - that is why `install` never overwrites.

## What was removed from the public release

The hooks and rules in this repo were extracted from one person's live `~/.claude` with **`lapbar bundle`**. The substitution table (`scrub_map.json`) holds the originals, so it lives only outside the repo (`$LAPBAR_SCRUB_MAP`) and the public code carries only digests. The extraction substituted or dropped terms of address, real names, accounts, home paths, project and company names, vendors, wrapper names, IPs and token patterns. `lapbar bundle --check` is the gate: in CI it looks only at generic patterns (home paths, IPs, JWTs, credential assignments, memory file names), while proper nouns (kept as digests) and terms of address are checked locally by the maintainer with `--require-map`. The external-access ledger hook (trust-ledger) is tightly coupled to a company wrapper table, so it is coming in a later release.

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

- Author and copyright: **Geunrok Kim** (license: MIT)
- Co-author: **Claude** (Anthropic) - the hooks, rules and this tool were built together in Claude Code sessions; commits carry a `Co-Authored-By: Claude` trailer.
