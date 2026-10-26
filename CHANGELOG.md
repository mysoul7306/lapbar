# Changelog

## 0.1.0 - Unreleased

First release, as `lapbar`, under the MIT license. The prototype was published on 2026-10-22 as `beacon` under PolyForm Noncommercial 1.0.0.

- `lapbar install` - copies hooks, rules, skills, workflow scripts and the `review-readonly` agent into `~/.claude` without overwriting user files; wires hooks into `settings.json` idempotently with an owner-only backup.
- `lapbar doctor` - checks assets and wiring, runs each hook's `--self-test`, feeds each guard sample inputs and checks the deny / allow answer, and verifies that every hook command in `settings.json` points at an existing, compilable file.
- `lapbar report` - prices your own Claude Code transcripts at list price and shows the counterfactual cost had every subagent run on the head model.
- `lapbar session` - prints one session's hook injection size by hook, loaded instruction files, subagent type x model x effort, and token usage on both sides, in under 40 lines (`--json` for the data).
- `lapbar bundle` - maintainer command that extracts and scrubs assets from a live `~/.claude`; `--check` is the CI gate.
- Hooks: `subagent-model-guard` (model and effort literals required, top tier needs an approval tag), `git-push-guard` (protected-branch push deny, fail-closed), `memory-lint-hook` (memory directory hygiene on session start), `git-readonly-guard` (agent-scoped allowlist for read-only git, declared in the agent's frontmatter rather than `settings.json`).
- Agents: `review-readonly` (Read, Grep, Glob only), `scout-git` (git history scout for planning; its Bash is limited to read-only git by `git-readonly-guard`).
