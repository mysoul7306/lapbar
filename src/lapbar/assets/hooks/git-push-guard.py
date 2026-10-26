#!/usr/bin/env python3
"""git push guard (PreToolUse: Bash).

**Always denies** a `git push` that goes to develop / main / master. The rule that push belongs to the
owner (~/.claude/rules/git-workflow.md) broke twice when it was only written down (a workflow description
misread as a go-ahead, then a reply to a sign-off list misread as delegation). A matcher cannot catch it, so this hook blocks the push.

Judgement is conservative (fail-closed):
- An explicit refspec (`origin develop`, `HEAD:develop`, `+develop`, `refs/heads/main`, `develop:develop`)
  is judged by its destination (after `:`, or the whole spec if there is none). Deletes (`:develop`, `--delete develop`) are blocked too.
- With no refspec (`git push`, `git push origin`, `git push -u origin HEAD`) the current branch and `@{push}`
  (a feature branch whose upstream points at develop) are resolved with real git. If that fails, deny.
- `--all` and `--mirror` are denied. `--dry-run` gets no exception (the owner runs dry-runs).
- A git push inside quotes, `bash -c` or an ssh remote string is denied when it cannot be tokenized (the message says to move it out).
- A feature branch push passes untouched (no output).

`python3 git-push-guard.py --self-test` = regression cases (a temp repo, down to current branch and upstream resolution).
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

PROTECTED = ("develop", "main", "master")
GIT_GLOBAL_WITH_VALUE = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path")
# Only options that also take their value as a separate argument. --force-with-lease, --signed, --recurse-submodules take a value only via `=`,
# and --force-if-includes is a flag: listing it here would swallow the next argument (the remote) and shift the branch into the remote slot (2026-10-22 security review).
PUSH_OPT_WITH_VALUE = ("-o", "--push-option", "--repo", "--receive-pack", "--exec")
# Config overrides that can flip the verdict - fail-closed when they come with a push that has no refspec.
CONFIG_OVERRIDE_PREFIXES = ("push.", "remote.", "branch.", "alias.", "include.", "includeif.", "url.", "core.hookspath")
ALIAS_DEFINE = re.compile(r"(?<![\w.-])git\b[^\n;&|]*?(?:\s-c\s+alias\.|\s+config\b[^\n;&|]*\balias\.)", re.IGNORECASE)
# Every git config injection path - they can change alias and push settings from outside the command line, so fail-closed (2026-10-22 security review, round 2).
CONFIG_INJECT = re.compile(r"--config-env\b|(?<![\w-])(GIT_[A-Z_]+|HOME|XDG_CONFIG_HOME)=")
KNOWN_SUBCOMMANDS = frozenset("""add am apply archive bisect blame branch bundle cat-file check-ignore checkout cherry-pick
 clean clone commit config count-objects describe diff fetch filter-branch for-each-ref format-patch fsck gc grep help init
 log ls-files ls-remote ls-tree merge merge-base mv name-rev notes prune pull push rebase reflog remote repack replace
 reset restore rev-list rev-parse revert rm shortlog show show-ref stash status submodule switch symbolic-ref tag
 update-index update-ref var version worktree""".split())
GUIDE = ("Pushing develop / main is the owner's job - Claude pushes feature branches only, does the merge, then "
         "leaves it as \"push = owner\". (rule ~/.claude/rules/git-workflow.md)")
SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
# Allows `/` before `git` so /usr/bin/git and ./git are caught too (2026-10-22 security review, round 4).
RAW_PUSH = re.compile(r"(?<![\w.-])git\b(?:\s+-[^\s]+(?:\s+[^\s]+)?)*\s+push(?![\w./-])")


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason + " " + GUIDE}}, ensure_ascii=False))


def _git(args, cwd):
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def resolve_targets(cwd, include_upstream):
    """Returns the current branch name (plus, when there is no refspec at all, the remote branch that @{push}
    and @{upstream} point at - push.default=upstream sends it there). None when it cannot be resolved."""
    head = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if not head or head == "HEAD":
        return None
    targets = {head}
    if include_upstream:
        for ref in ("@{push}", "@{upstream}"):
            remote_ref = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", ref], cwd)
            if remote_ref and "/" in remote_ref:
                targets.add(remote_ref.split("/", 1)[1])
    return targets


def dst_of(refspec):
    """Destination branch name of a refspec. HEAD/@ mean 'the current branch', so they return None."""
    spec = refspec.lstrip("+")
    if ":" in spec:
        dst = spec.split(":", 1)[1]
    else:
        dst = spec
    if dst.startswith("refs/heads/"):
        dst = dst[len("refs/heads/"):]
    if dst in ("HEAD", "@", ""):
        return None
    return dst


def parse_push(tokens, start):
    """tokens[start] is git. Returns a dict if this is the push subcommand, otherwise None."""
    i = start + 1
    repo_dir = None
    override = False
    while i < len(tokens) and tokens[i].startswith("-"):
        t = tokens[i]
        if t in GIT_GLOBAL_WITH_VALUE:
            if t == "-C" and i + 1 < len(tokens):
                repo_dir = tokens[i + 1]
            if t == "-c" and i + 1 < len(tokens) and tokens[i + 1].lower().startswith(CONFIG_OVERRIDE_PREFIXES):
                override = True
            if t in ("--git-dir", "--work-tree", "--namespace"):
                override = True
            i += 2
        else:
            if t.startswith(("--git-dir=", "--work-tree=", "--namespace=")):
                override = True
            i += 1
    if i >= len(tokens) or tokens[i] != "push":
        return None
    i += 1
    opts, positional = [], []
    delete = all_or_mirror = False
    while i < len(tokens):
        t = tokens[i]
        if t in ("&&", "||", ";", "|"):
            break
        if t.startswith("-"):
            opts.append(t)
            if t in ("--all", "--mirror", "--branches"):
                all_or_mirror = True
            if t in ("-d", "--delete"):
                delete = True
            if t in PUSH_OPT_WITH_VALUE and "=" not in t:
                i += 2
                continue
        else:
            positional.append(t)
        i += 1
    return {"repo_dir": repo_dir, "opts": opts, "positional": positional,
            "delete": delete, "all_or_mirror": all_or_mirror, "override": override}


def judge(command, cwd):
    """A deny reason string, or None (pass)."""
    if CONFIG_INJECT.search(command) and _mentions_git(command):
        return "Blocked: a command with git config injection (--config-env, GIT_CONFIG_* env) cannot be judged for push."
    if ALIAS_DEFINE.search(command):
        return "Blocked: a command that defines a git alias or passes -c alias.* cannot be judged for push."
    alias_reason = alias_push_reason(command, cwd)
    if alias_reason:
        return alias_reason
    raw_hits = len(RAW_PUSH.findall(command))
    if raw_hits == 0:
        return None
    try:
        shlex.split(command, posix=True)
    except ValueError:
        return "Blocked: the quotes in a command containing git push do not match (cannot parse)."
    parsed = 0
    reasons = []
    for segment in SEGMENT_SPLIT.split(command):
        if not RAW_PUSH.search(segment):
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return "Blocked: could not split the command containing git push into arguments (mismatched quotes)."
        found = False
        for idx, tok in enumerate(tokens):
            if tok != "git" and not tok.endswith("/git"):
                continue
            push = parse_push(tokens, idx)
            if push is None:
                continue
            found = True
            parsed += 1
            reason = judge_push(push, cwd, segment.strip())
            if reason:
                reasons.append(reason)
        if not found:
            return ("Blocked: a git push inside quotes, bash -c or an ssh string cannot be judged for its target: "
                    "`%s`. Move the push out into a top-level command and retry." % segment.strip()[:120])
    if parsed < raw_hits:
        return "Blocked: git push appears %d times but only %d were judged - a hidden push is present." % (raw_hits, parsed)
    return reasons[0] if reasons else None


def _mentions_git(command):
    """Whether a git invocation is present, by shlex arguments (so quotes cannot hide it). Fail-closed True if unparsable."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return True
    return any(t == "git" or t.endswith("/git") for t in tokens)


def alias_push_reason(command, cwd):
    """A deny reason when `git <alias>` expands to a push. Unknown subcommands are checked against the repo's alias config."""
    if "git" not in command:
        return None
    for segment in SEGMENT_SPLIT.split(command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        for idx, tok in enumerate(tokens):
            if tok != "git" and not tok.endswith("/git"):
                continue
            j, repo_dir = idx + 1, None
            while j < len(tokens) and tokens[j].startswith("-"):
                if tokens[j] in GIT_GLOBAL_WITH_VALUE:
                    if tokens[j] == "-C" and j + 1 < len(tokens):
                        repo_dir = tokens[j + 1]
                    j += 2
                else:
                    j += 1
            if j >= len(tokens) or tokens[j] in KNOWN_SUBCOMMANDS or not re.fullmatch(r"[A-Za-z][\w-]*", tokens[j]):
                continue
            expansion = _git_alias(repo_dir or cwd or os.getcwd(), tokens[j])
            if expansion is None:
                continue
            if expansion.startswith("!") or re.search(r"(?<![\w/.-])push(?![\w./-])", expansion):
                return "Blocked: `git %s` is an alias that can expand to a push (use git push directly instead of the alias)." % tokens[j]
    return None


def _git_alias(repo_dir, name):
    try:
        out = subprocess.run(["git", "-C", repo_dir, "config", "--get", "alias." + name],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def judge_push(push, cwd, segment):
    if push["all_or_mirror"]:
        return "Blocked: an `--all` / `--mirror` push also pushes develop and main: `%s`." % segment[:120]
    positional = push["positional"]
    refspecs = positional[1:] if positional else []
    if push["delete"] and positional:
        refspecs = positional[1:] if len(positional) > 1 else positional
    targets = set()
    no_refspec = not refspecs
    need_resolve = no_refspec
    for spec in refspecs:
        dst = dst_of(spec)
        if dst is None:
            need_resolve = True
        else:
            targets.add(dst)
    if need_resolve and push["override"]:
        return ("Blocked: a config override (-c push.*/remote.*/branch.*, --git-dir) with a push that has no refspec "
                "cannot be judged for its target: `%s`." % segment[:120])
    if need_resolve:
        repo_dir = push["repo_dir"] or cwd or os.getcwd()
        resolved = resolve_targets(repo_dir, include_upstream=no_refspec)
        if resolved is None:
            return ("Blocked: `%s` has no refspec and could not resolve the current branch (outside a repo or detached). "
                    "Name it explicitly: `git push origin <feature-branch>`." % segment[:120])
        targets |= resolved
    hit = sorted(t for t in targets if t in PROTECTED)
    if hit:
        return "Blocked: `%s` pushes to %s." % (segment[:120], ", ".join(hit))
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = payload.get("cwd") or os.getcwd()
    try:
        reason = judge(command, cwd)
    except Exception as e:  # fail-closed
        reason = "Blocked to be safe: git push guard internal error (%s)." % e.__class__.__name__
    if reason:
        deny(reason)
    return 0


# ---------------------------------------------------------------- self-test
def _make_repo(tmp):
    bare = os.path.join(tmp, "origin.git")
    work = os.path.join(tmp, "work")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    subprocess.run(["git", "init", "-q", "-b", "develop", work], check=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", work, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env={**os.environ, **env})
    subprocess.run(["git", "-C", work, "remote", "add", "origin", bare], check=True)
    subprocess.run(["git", "-C", work, "push", "-q", "-u", "origin", "develop"], check=True)
    subprocess.run(["git", "-C", work, "checkout", "-q", "-b", "feat/x"], check=True)
    return work


def self_test():
    results = []

    def case(name, command, cwd, expect_deny, needle=None):
        reason = judge(command, cwd)
        ok = (reason is not None) == expect_deny and (needle is None or (reason and needle in reason))
        results.append(ok)
        print("%s - %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else " → %r" % reason))

    with tempfile.TemporaryDirectory() as tmp:
        work = _make_repo(tmp)
        # --- explicit refspec
        case("origin develop → deny", "git push origin develop", work, True, "develop")
        case("origin main → deny", "git push -q origin main", work, True, "main")
        case("-u origin develop → deny", "git push -u origin develop", work, True)
        case("HEAD:develop → deny", "git push origin HEAD:develop", work, True)
        case("feat/x:develop → deny", "git push origin feat/x:develop", work, True)
        case("+develop (force refspec) → deny", "git push origin +develop", work, True)
        case("refs/heads/main → deny", "git push origin refs/heads/main", work, True)
        case("--force develop → deny", "git push --force origin develop", work, True)
        case("--force-with-lease=x develop → deny", "git push --force-with-lease=develop origin develop", work, True)
        case('--force-with-lease origin develop (no value form) → deny', 'git push --force-with-lease origin develop', work, True, "develop")
        case('--force-if-includes --force-with-lease origin main → deny', 'git push --force-if-includes --force-with-lease origin main', work, True)
        case('--signed origin main → deny', 'git push --signed origin main', work, True, "main")
        case('--force-with-lease origin feat/x → pass', 'git push --force-with-lease origin feat/x', work, False)
        case('-c alias.p=push p origin develop → deny', 'git -c alias.p=push p origin develop', work, True, "alias")
        case('git config alias definition → deny', "git config --global alias.p 'push origin develop'", work, True, "alias")
        case('-c push.default=x, no refspec → deny', 'git -c push.default=upstream push', work, True, "a config override")
        case('-c user.name=x origin feat/x → pass', 'git -c user.name=x push origin feat/x', work, False)
        case('-c Alias.p (uppercase) → deny', 'git -c Alias.p=push p origin develop', work, True, 'alias')
        case('-c PUSH.default, no refspec → deny', 'git -c PUSH.default=upstream push', work, True, 'cannot be judged')
        case('--config-env → deny', 'git --config-env=alias.p=X p origin develop', work, True, 'config injection')
        case('GIT_CONFIG_PARAMETERS env → deny', "GIT_CONFIG_PARAMETERS='alias.p=push' git p origin develop", work, True, 'config injection')
        case('GIT_CONFIG_COUNT env → deny', 'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p git p', work, True, 'config injection')
        case('GIT_DIR env + push → deny', 'GIT_DIR=/tmp/x/.git git pu' + 'sh', work, True, 'config injection')
        case('HOME env + git → deny', 'HOME=/tmp/h git pu' + 'sh origin feat/x', work, True, 'config injection')
        case('-c include.path, no refspec → deny', 'git -c include.path=/tmp/c pu' + 'sh', work, True, 'cannot be judged')
        case('HOME env but not git → pass', 'HOME=/tmp/h ls', work, False)
        case('/usr/bin/git absolute path push develop → deny', '/usr/bin/git push origin develop', work, True, 'develop')
        case('./git relative path push main → deny', './git push origin main', work, True)
        case('/usr/bin/git push feat/x → pass', '/usr/bin/git push origin feat/x', work, False)
        case(":develop (delete) → deny", "git push origin :develop", work, True)
        case("--delete develop → deny", "git push origin --delete develop", work, True)
        case("--all → deny", "git push --all origin", work, True, "--all")
        case("--mirror → deny", "git push --mirror origin", work, True)
        case("--dry-run develop → deny (no exception)", "git push --dry-run origin develop", work, True)
        case("origin feat/x → pass", "git push origin feat/x", work, False)
        case("-u origin feat/x → pass", "git push -u origin feat/x", work, False)
        case("origin develop:feat/x (destination is a feature branch) → pass", "git push origin develop:feat/x", work, False)
        case("origin feat/x:refs/heads/feat/x → pass", "git push origin feat/x:refs/heads/feat/x", work, False)
        case("-o ci.skip origin feat/x → pass", "git push -o ci.skip origin feat/x", work, False)
        case("develop inside a chain → deny", "git fetch -q && git merge --no-ff x && git push origin develop", work, True)
        case("feature inside a chain → pass", "git add -A && git commit -q -m x && git push -u origin feat/x", work, False)
        case("git without push → pass", "git push-check 2>/dev/null; git log --oneline -1", work, False)
        case("look-alike word such as pushd → pass", "pushd /tmp && git status && popd", work, False)
        # --- no refspec → resolve the current branch in a real repo
        case("no refspec, on feat/x (no upstream) → pass", "git push", work, False)
        case("origin only, on feat/x → pass", "git push origin", work, False)
        case("origin HEAD, on feat/x → pass", "git push origin HEAD", work, False)
        subprocess.run(["git", "-C", work, "branch", "-q", "--set-upstream-to=origin/develop", "feat/x"], check=True)
        case("no refspec, feat/x has origin/develop as upstream → deny", "git push", work, True, "develop")
        case("-u origin HEAD, on feat/x (HEAD ignores upstream) → pass", "git push -u origin HEAD", work, False)
        case("-C <repo> push (outside cwd) → deny", "git -C %s push" % work, "/", True)
        subprocess.run(["git", "-C", work, "branch", "-q", "--unset-upstream", "feat/x"], check=True)
        subprocess.run(["git", "-C", work, "checkout", "-q", "develop"], check=True)
        case("no refspec, on develop → deny", "git push", work, True, "develop")
        case("origin HEAD, on develop → deny", "git push origin HEAD", work, True)
        case("cwd is not a repo + no refspec → deny (blocked when unresolvable)", "git push", tmp, True, "could not resolve")
        subprocess.run(["git", "-C", work, "config", "alias.pp", "push origin develop"], check=True)
        subprocess.run(["git", "-C", work, "config", "alias.st", "status -s"], check=True)
        case("repo alias pp → expands to push, deny", "git pp", work, True, "alias")
        case("repo alias st (status) → pass", "git st", work, False)
        case("unknown subcommand (not an alias) → pass", "git whatchanged", work, False)
        # --- hidden push (fail-closed)
        case("push inside bash -c → deny", "bash -c 'git push origin develop'", work, True, "inside quotes, bash -c")
        case("push inside an ssh remote string → deny", "ssh gw 'cd repo && git push origin feat/x'", work, True)
        case("mismatched quotes → deny", "git push origin feat/x && echo \"x", work, True)
        # --- not git
        case("empty command → pass", "", work, False)
        case("ls → pass", "ls -la", work, False)
    n = len(results)
    print("self-test: %d/%d PASS" % (sum(results), n))
    return 0 if all(results) else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(main())
