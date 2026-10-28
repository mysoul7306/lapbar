# Changelog

## 0.1.2 - Unreleased

- `subagent-model-guard`: **haiku role mode** (`SUBAGENT_HAIKU_ROLE_MODE=role` in the `env` block of `settings.json`, default off) - haiku passes without an approval tag when the call carries a `[haiku role: …]` tag and a `[verify: …]` tag and its effort is medium or high. The approval tag keeps working in either mode.
- `subagent-model-guard`: the name inside the approval tags comes from `SUBAGENT_GUARD_OWNER` (default `owner`; a `<placeholder>` counts as unset), so a team can require `[alice-approved: fable]`.
- `subagent-model-guard`: fail-closed on regex literals (a `/` after an operator, an opening bracket, the start of the script or a keyword), on a `/` that could be division or a regex (after `)`, `]`, `...` or an identifier) when a quote and another `/` follow on the same line, and on a newline inside a quoted string - the quotes of two regex literals could pair up and hide an `agent()` call between them. A model name with two tier words (`opus-haiku`) is denied. The depth table is linear: a 1500-key (about 30 KB) schema took 17 seconds and outran the hook timeout, and a timed-out hook is skipped, not denied. `--self-test` 80 → 122 cases, with a 2-second timing case and a summary of the env values found in the `settings.json` next to the hook's folder.
- `subagent-model-guard`: a `model :` or `'effort' :` inside a ternary in another value (`label: c ? model : 'opus'`) was read as the key and overrode the real one - keys now count only after `{` or `,`. A quoted key with an escape sequence (`'eff\u006frt'`, which overrides `effort` at runtime) is a shape deny. A CRLF line continuation inside a string is no longer denied. When role marks are present but role mode is off, the deny names the env that turns it on.
- `lapbar doctor`: a third probe for the model guard (haiku without a tag is denied).
- Rules refreshed from the live harness: `subagent-review.md` (haiku role paragraph, the tier roles in one line), `lane.md` (the collect lane may use haiku in role mode - with the env set, and outside the `lane-workflow.js` template, which pins collect to sonnet), `zsh-pitfalls.md` (mawk has no IGNORECASE).

## 0.1.1 - 2026-10-26

- Hook messages, docstrings and comments are English (`subagent-model-guard`, `git-push-guard`, `memory-lint-hook`, `memory-lint`). Rule files and the lane procedures stay in Korean.
- `lapbar bundle` keeps assets marked `keep` in the manifest (hooks and the `scout-git` agent are authored in this repo, not extracted from a live `~/.claude`).

## 0.1.0 - 2026-10-26

First release, as `lapbar`, under the MIT license. The prototype was published on 2026-10-22 as `beacon` under PolyForm Noncommercial 1.0.0.

- `lapbar install` - copies hooks, rules, skills, workflow scripts and the `review-readonly` agent into `~/.claude` without overwriting user files; wires hooks into `settings.json` idempotently with an owner-only backup.
- `lapbar doctor` - checks assets and wiring, runs each hook's `--self-test`, feeds each guard sample inputs and checks the deny / allow answer, and verifies that every hook command in `settings.json` points at an existing, compilable file.
- `lapbar report` - prices your own Claude Code transcripts at list price and shows the counterfactual cost had every subagent run on the head model.
- `lapbar session` - prints one session's hook injection size by hook, loaded instruction files, subagent type x model x effort, and token usage on both sides, in under 40 lines (`--json` for the data).
- `lapbar bundle` - maintainer command that extracts and scrubs assets from a live `~/.claude`; `--check` is the CI gate.
- Hooks: `subagent-model-guard` (model and effort literals required, top tier needs an approval tag), `git-push-guard` (protected-branch push deny, fail-closed), `memory-lint-hook` (memory directory hygiene on session start), `git-readonly-guard` (agent-scoped allowlist for read-only git, declared in the agent's frontmatter rather than `settings.json`).
- Agents: `review-readonly` (Read, Grep, Glob only), `scout-git` (git history scout for planning; its Bash is limited to read-only git by `git-readonly-guard`).
