# beacon 🔦

> 🇰🇷 **한국어**: [README.ko.md](README.ko.md)

**An expensive model for thinking, cheap models for the legwork — a Claude Code harness enforced by hooks, plus a report that shows how much it saved.**

> One line: `beacon install` drops a small set of Claude Code hooks, rules and skills into `~/.claude`; `beacon report` reads your own transcripts and shows how much cheaper your subagents were than running everything on the head model.

## Install

```
# From PyPI (coming soon)
pipx install claude-beacon          # or: uv tool install claude-beacon

# From source (works today)
git clone https://github.com/mysoul7306/beacon && cd beacon
pipx install .                      # or: pip install .
```

Then:

```
beacon install --dry-run            # what would be installed, nothing written
beacon install                      # hooks + rules + skills + agent into ~/.claude, settings.json wired (backup kept)
beacon doctor                       # everything in place, hook self-tests pass
beacon report --since 2026-10-13    # your own savings, from your own transcripts
```

## Why

The most expensive thing in Claude Code is the head model doing the legwork. Subagent work — scanning files, review lenses, collection — is just as good on a cheaper model, but an `Agent` / `Workflow` call that doesn't name a model inherits the head model. So hooks block that.

- **model and effort must be literals** — `subagent-model-guard.py` denies a missing model, a variable, or a ternary in `Agent` / `Task` / `Workflow` calls. The top tier (fable) and the bottom tier (haiku, blocked by default after quality incidents) are allowed only with an `[owner-approved: <model>]` tag inside that same call. Effort follows the same rule (low / medium / high / xhigh literals). Allowed model names live in the hook's single `TIERS` table.
- **lane loop** — `/lane` (the head only decodes, decomposes and synthesizes; read is opus high, collect is sonnet low, refute is opus xhigh) and `/review-lane` (3 lenses → 2 refuters, agent cap 10) turn that split into a procedure.
- **protected-branch push block** — `git-push-guard.py` denies pushes to develop / main / master, resolving refspec and upstream to decide, and failing closed when it can't. Merges by the AI, pushes by a human.
- **memory hygiene** — `memory-lint-hook.py` checks the memory directory for broken links, public IPs, secrets and router size at session start.

Both guards are **regex-based**: a safety net against the AI accidentally pushing or forgetting a model, not a sandbox against deliberate evasion (eval, config injection, shell tricks). Known evasion shapes are closed one by one as self-test cases (push 60, model 80); an AST-parser rewrite is the next milestone.

## How much it saves

`beacon report` reads the transcripts (jsonl) under `~/.claude/projects/`, collects per-request model and tokens (input, output, cache read / write), and compares actual cost against a **counterfactual: every subagent request re-priced at the head model's rate**. It needs no before/after setup, so numbers show up on day one.

Measured by the person who built this harness (10 days since turning the hooks on 2026-10-13, `beacon report --since 2026-10-13`, run on the night of 2026-10-22):

| Metric | Value |
|---|---|
| Requests | 16,051 (head 6,501, subagent 9,550, of which workflow 8,031) |
| Actual cost (list-price estimate) | $2,897.65 |
| If every subagent had run on the head model | $3,929.62 |
| **Savings** | **$1,031.97** — 26.3% of the counterfactual, 35.6% of actual spend |
| Subagent effort | high 65%, xhigh 33%, medium 2% |
| Share of cache writes at 1-hour TTL | 40% (1-hour writes priced at input rate × 2) |

97% of subagent requests ran on opus (fable 2%, opus 4.8 1%); the savings are that rate gap. Head-side cost (~$1,507) is left untouched, so looking at the subagent share alone, it drops below half.

- Rates are public list price (`src/beacon/prices.json`, dated) × tokens, so they are an **estimate**, not a real invoice. Override with `--prices`.
- Each requestId is counted once (one response leaves a record per content block; the record with the largest output is kept). `<synthetic>` is excluded. Head vs subagent is classified by file path (`<session>.jsonl` vs `subagents/`), which assumes the post-2026-10 transcript layout.
- Give the head model with `--head`, or it is inferred as the costliest model on the head side (shown as `inferred` in the output).
- To see your own numbers, dump them with `beacon report --json > report.json`.

## What gets installed

| Kind | File | What it does |
|---|---|---|
| hook | `hooks/subagent-model-guard.py` (PreToolUse: Task, Agent, Workflow) | forces model and effort literals, top tier needs a tag. `--self-test` 80 cases |
| hook | `hooks/git-push-guard.py` (PreToolUse: Bash) | denies develop / main / master push, fail-closed. `--self-test` 60 cases |
| hook | `hooks/memory-lint-hook.py` (SessionStart) + `scripts/memory-lint.py` | checks the memory directory, notifies only on problems |
| rule | `rules/zsh-pitfalls.md` | silent-failure traps in the Bash tool's zsh (incident-based) |
| rule | `rules/security.md` | output hygiene — keep values from leaking when you read config, env, wrappers |
| rule | `rules/memory-scope.md` | four buckets for deciding which scope a memory belongs in |
| rule | `rules/subagent-review.md` | review-lane size, model tiers, the read-only principle |
| rule | `rules/git-workflow.md` | branches, commits, worktrees, push permissions |
| skill | `commands/lane.md`, `commands/review-lane.md` + `scripts/*-workflow.js` | the lane loop and review-lane procedures |
| agent | `agents/review-readonly.md` | a read-only reviewer with only Read, Grep, Glob (the lane's refute / review role) |

`beacon install` **never overwrites your files** (if contents differ it skips and tells you; use `--force` to overwrite). Before writing `settings.json` it keeps a `settings.json.bak-<time>`, and it won't re-add a hook wiring that is already there.

## Using it (short)

Assumes a Claude Code where the `Agent` tool has an `effort` parameter and the `Workflow` tool exists (latest as of 2026-10). Without those, use only the model guard and skip `/lane` and `/review-lane`.

1. `beacon install` → restart Claude Code
2. When you call a subagent, name the model and effort. If you don't, the hook blocks it and shows why.
3. Review with `/review-lane`, long tasks with `/lane`.
4. Occasionally look at the numbers with `beacon report`.

The rule files are in Korean (the working language of the person who uses this harness). Copy and edit them as you like — that is why `install` never overwrites.

## What was removed from the public release

The hooks and rules in this repo were extracted from one person's live `~/.claude` with **`beacon bundle`**. The substitution table (`scrub_map.json`) holds the originals, so it lives only outside the repo (`$BEACON_SCRUB_MAP`) and the public code carries only digests. The extraction substituted or dropped terms of address, real names, accounts, home paths, project and company names, vendors, wrapper names, IPs and token patterns. `beacon bundle --check` is the gate: in CI it looks only at generic patterns (home paths, IPs, JWTs, credential assignments, memory file names), while proper nouns (kept as digests) and terms of address are checked locally by the maintainer with `--require-map`. The external-access ledger hook (trust-ledger) is tightly coupled to a company wrapper table, so it is coming in a later release.

## Development

```
git clone https://github.com/mysoul7306/beacon && cd beacon
python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m beacon.cli --help
```

Zero dependencies (Python 3.11+ standard library only).

## License

**PolyForm Noncommercial 1.0.0** — free to use, modify and share for any noncommercial purpose (personal, educational, research, nonprofit). Commercial use is not granted (ask separately). This is not open source under the OSI definition. Full text in `LICENSE`.

**Why it is public** — using an expensive model for thinking and cheap models for the legwork is just sensible spending. I made it public because I did not like the idea of someone charging for, and profiting off, such a sensible habit. The noncommercial license is there for the same reason.

## Credits

- Author and copyright: **rok_root** (license: PolyForm Noncommercial 1.0.0)
- Co-author: **Claude** (Anthropic) — the hooks, rules and this tool were built together in Claude Code sessions; commits carry a `Co-Authored-By: Claude` trailer.
