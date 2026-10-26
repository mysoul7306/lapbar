#!/usr/bin/env python3
"""Read-only git guard (PreToolUse: Bash), registered only in the scout-git agent frontmatter.

Allowlist: one git command whose global options, subcommand and options are in the tables below; anything
else, any shell metacharacter (even quoted) and any internal error is denied. An allowed command returns as
`updatedInput`, re-joined by shlex from the judged tokens with external-tool and lock opt-outs added;
`--self-test` runs the case table without git or network."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from functools import partial


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


ALLOWED_AGENTS = frozenset({"scout-git", "review-git"})
GUIDE = "scout-git may only run read-only git; use Read/Grep/Glob for files."
SHELL_META = ("&&", "||", "|", ";", "&", ">", "<", "$", "`", "(", ")", "{", "}", "#", "\n", "\r")
GLOB_CHARS = frozenset("*?[")
EXPANDING_WORD_STARTS = frozenset("~=")
NO_OPTIONAL_LOCKS = "--no-optional-locks"
GLOBAL_FLAGS = frozenset({"--no-pager", NO_OPTIONAL_LOCKS})
GLOBAL_DIR_OPTION = "-C"
KNOWN_GIT_PATHS = ("/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git")
GLOBAL_ONLY_REASON = ("--no-optional-locks is a global option; put it before the subcommand "
                      "(status gets it automatically).")
NO_EXTERNAL_TOOLS = ("--no-ext-diff", "--no-textconv")
LOG_OPT_OUTS = NO_EXTERNAL_TOOLS + ("--no-show-signature",)
OPT_OUTS = {"diff": NO_EXTERNAL_TOOLS, "blame": ("--no-textconv",),
            **dict.fromkeys(("log", "show", "stash list", "stash show", "reflog show"), LOG_OPT_OUTS)}
FORMAT_OPTIONS = frozenset({"--format", "--pretty"})
SIGNATURE_PLACEHOLDER = re.compile(r"%[-+ ]?G")
REF_PATTERN_OPTIONS = _words("--list -l --contains --no-contains --points-at --merged --no-merged")
CONFIG_READ_MODES = frozenset({"--get", "--get-all", "--get-regexp", "--list", "-l"})
CONFIG_KEY_MODES = frozenset({"--get", "--get-all", "--get-regexp"})


@dataclass(frozen=True)
class OptSpec:
    """Options a subcommand accepts. `valued` options take the next token as their value."""
    flags: frozenset[str]
    valued: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Parsed:
    reason: str | None
    positional: tuple[str, ...] = ()
    seen: frozenset[str] = frozenset()
    revisions: tuple[str, ...] = ()


DIFF_FLAGS = _words("""--stat --shortstat --numstat --name-only --name-status --summary --compact-summary --dirstat
 -p --patch -u -s --no-patch --raw --patch-with-stat --color --no-color --word-diff --color-words -w
 --ignore-all-space -b --ignore-space-change --ignore-blank-lines --diff-filter -M --find-renames --find-copies
 --no-renames --relative --abbrev --full-index --minimal --patience --histogram --unified --cached --staged
 --merge-base --check --exit-code --quiet -W --function-context --src-prefix --dst-prefix --no-prefix --stat-width
 --no-ext-diff --no-textconv -R --no-show-signature""")
LOG_FLAGS = DIFF_FLAGS | _words("""--oneline --graph --decorate --no-decorate --all --branches --tags --remotes
 --first-parent --no-merges --merges --reverse --follow --format --pretty --date --abbrev-commit --no-abbrev-commit
 --max-count --skip --left-right --cherry-pick --cherry-mark --topo-order --date-order --boundary --ancestry-path
 --simplify-by-decoration --full-history --source -i --regexp-ignore-case -E --extended-regexp -F --fixed-strings
 --all-match --invert-grep --pickaxe-all --pickaxe-regex --parents --children --no-notes --mailmap --use-mailmap""")
LOG_VALUED = _words("-n -L --author --committer --grep --since --until --after --before -S -G --skip --max-count")
LOG_SPEC = OptSpec(LOG_FLAGS, LOG_VALUED)
DIFF_SPEC = OptSpec(DIFF_FLAGS)
SUBCOMMAND_SPECS = {
    "log": LOG_SPEC, "show": LOG_SPEC, "diff": DIFF_SPEC,
    "blame": OptSpec(_words("""-w -M -s -l -t -n -f -b --porcelain --line-porcelain --incremental --root --date
 --abbrev --reverse --first-parent --show-email --show-number --show-name --no-textconv"""),
                     _words("-L --ignore-rev --since")),
    "ls-files": OptSpec(_words("""-c --cached -d --deleted -m --modified -o --others -i --ignored -s --stage -u
 --unmerged -t -v -f -z --full-name --abbrev --exclude-standard --directory --no-empty-directory --error-unmatch
 --deduplicate --format --eol"""), _words("-x --exclude")),
    "ls-tree": OptSpec(_words("""-d -r -t -l --long -z --name-only --name-status --object-only --full-name
 --full-tree --abbrev --format""")),
    "cat-file": OptSpec(_words("-t -s -p")),
    "rev-parse": OptSpec(_words("""--abbrev-ref --symbolic-full-name --symbolic --short --verify -q --quiet
 --show-toplevel --git-dir --absolute-git-dir --git-common-dir --is-inside-work-tree --is-bare-repository
 --show-prefix --show-cdup --all --branches --tags --remotes""")),
    "rev-list": OptSpec(LOG_FLAGS | _words("--count --left-only --right-only --objects"), LOG_VALUED),
    "status": OptSpec(_words("""-s --short -b --branch --porcelain --long -u --untracked-files --ignored
 --ignore-submodules -z --column --no-column --ahead-behind --no-ahead-behind --renames --no-renames --show-stash -v
 --verbose""")),
    "describe": OptSpec(_words("--tags --all --long --abbrev --always --contains --exact-match --first-parent"),
                        _words("--match --exclude --candidates")),
    "shortlog": OptSpec(_words("""-n --numbered -s --summary --format --group -c --committer --all --no-merges
 --merges --first-parent"""), _words("--since --until --after --before --author")),
    "name-rev": OptSpec(_words("--name-only --tags --refs --exclude --all --always --no-undefined")),
    "grep": OptSpec(_words("""-n --line-number -i --ignore-case -w --word-regexp -v --invert-match -l
 --files-with-matches -L --files-without-match -c --count -h -H --heading --break -E --extended-regexp -F
 --fixed-strings -G --basic-regexp -P --perl-regexp --and --or --not --all-match --cached --untracked -q --quiet -o
 --only-matching --column --full-name -I --exclude-standard --name-only -z"""),
                    _words("-e -A -B -C --context --after-context --before-context --max-depth")),
}
REF_VALUED = _words("--sort --format")
BRANCH_SPEC = OptSpec(REF_PATTERN_OPTIONS | _words("-a -r --all --remotes -v -vv --verbose --show-current"), REF_VALUED)
TAG_SPEC = OptSpec(REF_PATTERN_OPTIONS, REF_VALUED)
WORKTREE_LIST_SPEC = OptSpec(_words("--porcelain -v --verbose -z"))
CONFIG_SPEC = OptSpec(CONFIG_READ_MODES | _words("--show-origin --show-scope --null -z --name-only"))


def parse_options(args: tuple[str, ...], spec: OptSpec) -> Parsed:
    """Walk subcommand arguments against `spec`; everything after `--` is positional, not a revision."""
    positional: tuple[str, ...] = ()
    seen: frozenset[str] = frozenset()
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            return Parsed(None, positional + args[i + 1:], seen, positional)
        if not token.startswith("-"):
            positional, i = positional + (token,), i + 1
            continue
        key = token.split("=", 1)[0]
        if key in spec.valued and "=" not in token:
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return Parsed("option %s needs a value that does not start with '-'." % token)
            seen, i = seen | {key}, i + 2
            continue
        if key not in spec.flags and key not in spec.valued:
            unknown = "option %s is not on the read-only list." % token
            return Parsed(GLOBAL_ONLY_REASON if key == NO_OPTIONAL_LOCKS else unknown)
        seen, i = seen | {key}, i + 1
    return Parsed(None, positional, seen, positional)


def _check(spec: OptSpec, rules: tuple, args: tuple[str, ...]) -> Parsed:
    """Parse `args` against `spec`, then deny with the first (predicate, message) rule that holds."""
    parsed = parse_options(args, spec)
    failed = None if parsed.reason else next((message for test, message in rules if test(parsed)), None)
    return Parsed(failed) if failed else parsed


def _check_modes(modes: dict, empty_ok: bool, args: tuple[str, ...]) -> Parsed:
    """The first word picks a read-only mode (`stash list`, `worktree list`, `remote show`)."""
    if not args and empty_ok:
        return Parsed(None)
    if not args or args[0] not in modes:
        return Parsed("the read-only forms are %s%s." % (", ".join(modes), " or no arguments" if empty_ok else ""))
    return modes[args[0]](args[1:])


def _rules(spec: OptSpec, *rules: tuple):
    return partial(_check, spec, rules)


BARE_NAME_RULE = (lambda p: p.positional and not p.seen & REF_PATTERN_OPTIONS,
                  "a bare name would create a ref; add --list for a pattern.")
NO_NAMES_RULE = (lambda p: p.positional, "this mode takes no names.")
REMOTE_LOCAL_RULE = (lambda p: p.positional and "-n" not in p.seen,
                     "'git remote show <name>' contacts the remote; add -n to stay local.")
SHORTLOG_RULE = (lambda p: not p.revisions and "--all" not in p.seen,
                 "shortlog needs a revision such as HEAD (else it reads stdin).")
CONFIG_RULES = ((lambda p: not p.seen & CONFIG_READ_MODES, "use --get, --get-all, --get-regexp or --list."),
                (lambda p: p.positional and not p.seen & CONFIG_KEY_MODES, "--list takes no key."))
LIST_REMOTES = _rules(OptSpec(frozenset()), NO_NAMES_RULE)
CHECKERS = {
    **{name: _rules(spec) for name, spec in SUBCOMMAND_SPECS.items()},
    "shortlog": _rules(SUBCOMMAND_SPECS["shortlog"], SHORTLOG_RULE), "config": _rules(CONFIG_SPEC, *CONFIG_RULES),
    "branch": _rules(BRANCH_SPEC, BARE_NAME_RULE), "tag": _rules(TAG_SPEC, BARE_NAME_RULE),
    "stash": partial(_check_modes, {"list": _rules(LOG_SPEC), "show": _rules(DIFF_SPEC)}, False),
    "worktree": partial(_check_modes, {"list": _rules(WORKTREE_LIST_SPEC, NO_NAMES_RULE)}, False),
    "reflog": partial(_check_modes, {"show": _rules(LOG_SPEC)}, True),
    "remote": partial(_check_modes, {"-v": LIST_REMOTES, "--verbose": LIST_REMOTES,
                                     "show": _rules(OptSpec(_words("-n")), REMOTE_LOCAL_RULE)}, True),
}


def shell_hazard(command: str) -> str | None:
    """Metacharacters anywhere, or unquoted glob / tilde / equals expansion in the shell."""
    if (meta := next((m for m in SHELL_META if m in command), None)) is not None:
        return "shell metacharacter %r is not allowed (one plain git command only)." % meta
    quote, word_start, escaped = None, True, False
    for ch in command:
        if escaped:
            escaped, word_start = False, False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote, word_start = ch, False
        elif ch.isspace():
            word_start = True
        elif ch in GLOB_CHARS or (word_start and ch in EXPANDING_WORD_STARTS):
            return "unquoted %r would be expanded by the shell; quote it." % ch
        else:
            word_start = False
    return None


def _is_git(token: str) -> bool:
    """Bare `git`, or an absolute path that resolves to the git on PATH or a known install."""
    known = {os.path.realpath(path) for path in KNOWN_GIT_PATHS + tuple(filter(None, (shutil.which("git"),)))}
    return token == "git" or (token.startswith("/") and os.path.realpath(token) in known)


def _inside(path: str, base: str) -> bool:
    return os.path.commonpath([path, base]) == base


def _escapes(path: str, current: str, base: str) -> bool:
    """An absolute positional or one with a `..` segment must resolve inside the working directory."""
    if not path.startswith("/") and ".." not in path.split("/"):
        return False
    return not _inside(os.path.realpath(os.path.join(current, path)), base)


def _signature_format(args: tuple[str, ...]) -> bool:
    """A %G placeholder (also %+G, %-G, % G) in a --format/--pretty value makes git run gpg."""
    return any(SIGNATURE_PLACEHOLDER.search(token) and FORMAT_OPTIONS & {token.split("=", 1)[0], previous}
               for previous, token in zip(("",) + args, args))


def parse_globals(tokens: tuple[str, ...], cwd: str) -> tuple[str | None, int, str]:
    """Return (deny reason, index of the subcommand word, directory git will run in)."""
    base = os.path.realpath(cwd)
    current, i = base, 1
    while i < len(tokens) and tokens[i].startswith("-"):
        word = tokens[i]
        if word in GLOBAL_FLAGS:
            i += 1
            continue
        if word != GLOBAL_DIR_OPTION:
            return "global option %s is not allowed; only -C, --no-pager, --no-optional-locks." % word, i, current
        if i + 1 >= len(tokens):
            return "git -C needs a directory.", i, current
        current = os.path.realpath(os.path.join(current, tokens[i + 1]))
        if not _inside(current, base):
            return "git -C %s points outside the working directory." % tokens[i + 1], i, current
        i += 2
    if i >= len(tokens):
        return "no git subcommand given.", i, current
    return None, i, current


def judge(command: str, cwd: str) -> tuple[str | None, str]:
    """Return (deny reason or None, command to run)."""
    if hazard := shell_hazard(command):
        return hazard, command
    try:
        tokens = tuple(shlex.split(command))
    except ValueError:
        return "the command could not be tokenized (unclosed quote).", command
    if not tokens or not _is_git(tokens[0]):
        return "only git (bare, or the absolute path of the installed git) may run here.", command
    if (equals := next((token for token in tokens if token.startswith("=")), None)) is not None:
        return "argument %r would be expanded by zsh as =command; drop the leading '='." % equals, command
    reason, sub, current = parse_globals(tokens, cwd)
    if reason:
        return reason, command
    if reason := _subcommand_reason(tokens[sub], tokens[sub + 1:], current, os.path.realpath(cwd)):
        return "git %s: %s" % (tokens[sub], reason), command
    return None, rewrite(tokens, sub)


def _subcommand_reason(name: str, args: tuple[str, ...], current: str, base: str) -> str | None:
    checker = CHECKERS.get(name)
    if checker is None:
        return "not a read-only subcommand."
    parsed = checker(args)
    if parsed.reason:
        return parsed.reason
    pattern_first = name == "grep" and parsed.revisions and "-e" not in parsed.seen
    paths = parsed.positional[1:] if pattern_first else parsed.positional
    outside = next((path for path in paths if _escapes(path, current, base)), None)
    if outside is not None:
        return "path %s is outside the working directory." % outside
    if _signature_format(args):
        return "%G format placeholders run gpg; leave them out."
    return None


def rewrite(tokens: tuple[str, ...], sub: int) -> str:
    """Re-join the judged tokens; add lock and external-tool opt-outs where they apply."""
    head = tokens[:sub]
    if tokens[sub] == "status" and NO_OPTIONAL_LOCKS not in head:
        head = head[:1] + (NO_OPTIONAL_LOCKS,) + head[1:]
    words = tokens[sub:sub + 2] if " ".join(tokens[sub:sub + 2]) in OPT_OUTS else tokens[sub:sub + 1]
    rest = tokens[sub + len(words):]
    options = rest[:rest.index("--")] if "--" in rest else rest
    added = tuple(flag for flag in OPT_OUTS.get(" ".join(words), ()) if flag not in options)
    return shlex.join(head + words + added + rest)


def _output(decision: str, reason: str, updated: dict | None = None) -> dict:
    specific = {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}
    return {"hookSpecificOutput": specific if updated is None else {**specific, "updatedInput": updated}}


def deny(reason: str) -> dict:
    return _output("deny", "%s %s" % (reason, GUIDE))


def evaluate(payload: dict) -> dict | None:
    """Hook decision for one PreToolUse payload; None means not ours (no output)."""
    try:
        if payload.get("tool_name") != "Bash":
            return None
        if payload.get("agent_type") not in ALLOWED_AGENTS:
            return deny("This guard is agent-scoped; it is wired for the wrong place.")
        tool_input, command = payload["tool_input"], payload["tool_input"]["command"]
        if not isinstance(command, str):
            return deny("tool_input.command is not a string.")
        reason, final = judge(command, payload.get("cwd") or os.getcwd())
        if reason:
            return deny(reason)
        return _output("allow", "read-only git", {**tool_input, "command": final})
    except Exception as exc:  # fail-closed
        return deny("Guard error (%s)." % exc.__class__.__name__)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        output = evaluate(payload) if isinstance(payload, dict) else deny("Hook input is not an object.")
    except Exception as exc:  # fail-closed
        output = deny("Unreadable hook input (%s)." % exc.__class__.__name__)
    if output is not None:
        print(json.dumps(output))
    return 0


# ---------------------------------------------------------------- self-test
SELF_TEST_ALLOW = (
    "git log -n 20 --oneline", "git show HEAD~2 -- src/a.py", "git blame -L 10,20 f", "git diff main...HEAD --stat",
    "git -C sub log", "git log --format='%H %s' -n 5", "git log main..HEAD", "git branch --list 'feat/*'",
    "git stash list", "git worktree list", "git config --get user.name", "git --no-pager log -n 3",
    "git grep -n TODO -- src", "/usr/bin/git status --short", "git --no-optional-locks status",
    "git shortlog -s -n HEAD", "git grep -n '/api/v1' -- src", "git grep -n ../x -- src",
    "git shortlog -s -n --all", "git tag --no-contains HEAD",
)
SELF_TEST_DENY = (
    "git diff | cat", "git log > /tmp/x", "git log -n 3 ; rm -rf x", "git diff --output=/tmp/x",
    "git -c core.pager=less log", "git push origin main", "git stash pop", "git config user.name x",
    "git -C .. log", "git -C /etc log", "ls", "git log $(cat x)", 'git log "a | b"', "git branch foo", "git tag v1",
    "git show --ext-diff HEAD", "git log -p --exec x", "git log 'unclosed", "git diff --output-indicator-new=x",
    "git diff --textconv", "git grep -O TODO", "git grep --open-files-in-pager TODO", "git config -e",
    "git config --edit", "git branch -d x", "git branch -D x", "git branch -m x", "git branch -f x",
    "git branch --delete x", "git branch --force x", "git stash show --push", "git branch --set-upstream x",
    "git -C ~ log", "git log # x", 'git reflog show --grep "x\\"" * "README.md"', "git log =x",
    "git log --grep '=x'", "/tmp/nowhere/git log -n 3", "git diff --stat /etc/hosts /dev/null",
    "git log -- ../other", "git log -n 1 --format=%GK", "git log --pretty='format:%G?'", "git shortlog -s -n",
    "git log -n 1 --format=%+GK", "git log -n 1 --format='% G?'", "git log --pretty=format:%-GS",
    "git status --no-optional-locks",
)
SELF_TEST_REWRITE = (
    ("git diff --stat", "git diff --no-ext-diff --no-textconv --stat"),
    ("git log --no-ext-diff -n 1", "git log --no-textconv --no-show-signature --no-ext-diff -n 1"),
    ("git stash list -p", "git stash list --no-ext-diff --no-textconv --no-show-signature -p"),
    ("git blame -L 1,2 f", "git blame --no-textconv -L 1,2 f"),
    ("git status", "git --no-optional-locks status"),
    ('git ls-files -- "*.py"', "git ls-files -- '*.py'"),
)


def _decision(output: dict | None) -> str | None:
    return None if output is None else output["hookSpecificOutput"]["permissionDecision"]


def _bash(cwd: str, command: str, agent: str | None = "scout-git", **extra: object) -> dict:
    base = {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command, **extra}}
    return base if agent is None else {**base, "agent_type": agent}


def _self_test_results(cwd: str) -> tuple[tuple[str, bool], ...]:
    cases = (tuple(("allow: " + c, _bash(cwd, c), "allow") for c in SELF_TEST_ALLOW)
             + tuple(("deny: " + c, _bash(cwd, c), "deny") for c in SELF_TEST_DENY)
             + (("deny: no agent_type", _bash(cwd, "git log", None), "deny"),
                ("deny: agent_type general-purpose", _bash(cwd, "git log", "general-purpose"), "deny"),
                ("ignore: tool_name Read", {**_bash(cwd, "git log"), "tool_name": "Read"}, None)))
    extra = {"description": "d", "timeout": 1000}
    rewrites = tuple(("rewrite: %s -> %s, other keys kept" % (old, new),
                      (evaluate(_bash(cwd, old, **extra)) or {}).get("hookSpecificOutput", {}).get("updatedInput")
                      == {"command": new, **extra}) for old, new in SELF_TEST_REWRITE)
    return tuple((name, _decision(evaluate(payload)) == expect) for name, payload, expect in cases) + rewrites


def self_test() -> int:
    with tempfile.TemporaryDirectory() as cwd:
        os.mkdir(os.path.join(cwd, "sub"))
        results = _self_test_results(cwd)
    print("\n".join("%s - %s" % ("PASS" if ok else "FAIL", name) for name, ok in results))
    passed = sum(1 for _, ok in results if ok)
    print("self-test: %d/%d PASS" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else main())
