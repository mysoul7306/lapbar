# Changelog

## 0.1.0 - 2026-10-22

First public release.

- `beacon install` - copies hooks, rules, skills, workflow scripts and the `review-readonly` agent into `~/.claude` without overwriting user files; wires hooks into `settings.json` idempotently with an owner-only backup.
- `beacon doctor` - checks assets, wiring and runs each hook's `--self-test`.
- `beacon report` - prices your own Claude Code transcripts at list price and shows the counterfactual cost had every subagent run on the head model.
- `beacon bundle` - maintainer command that extracts and scrubs assets from a live `~/.claude`; `--check` is the CI gate.
- Hooks: `subagent-model-guard` (model and effort literals required, top tier needs an approval tag), `git-push-guard` (protected-branch push deny, fail-closed), `memory-lint-hook` (memory directory hygiene on session start).
