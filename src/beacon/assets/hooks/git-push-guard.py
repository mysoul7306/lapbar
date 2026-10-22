#!/usr/bin/env python3
"""git push 가드 (PreToolUse: Bash).

develop / main / master 로 가는 `git push` 를 **무조건 deny** 한다. push 는 사용자 몫이라는 규칙
(~/.claude/rules/git-workflow.md) 이 문장만으로는 두 번 깨졌다 (2026-07-08 워크플로우 서술 오독,
2026-10-13 사인 대기 목록 응답 오독). 2026-10-13 사용자 요청: matcher 로는 못 잡으니 push 를 확실히 막는 hook 을 만든다.

판정 원칙 = 보수적 (fail-closed):
- 명시 refspec (`origin develop`, `HEAD:develop`, `+develop`, `refs/heads/main`, `develop:develop`) 은
  목적지 (`:` 뒤, 없으면 앞) 를 본다. 삭제 (`:develop`, `--delete develop`) 도 막는다.
- refspec 이 없으면 (`git push`, `git push origin`, `git push -u origin HEAD`) 현재 브랜치와 `@{push}`
  (upstream 이 develop 을 가리키는 feature 브랜치) 를 실제 git 으로 푼다. 못 풀면 deny.
- `--all`, `--mirror` 는 deny. `--dry-run` 도 예외 없음 (dry-run 이 필요하면 사용자가).
- 따옴표, `bash -c`, ssh 원격 문자열 안의 git push 는 토큰으로 못 풀면 deny (밖으로 꺼내 쓰라고 안내).
- feature 브랜치 push 는 그대로 통과 (출력 없음).

`python3 git-push-guard.py --self-test` = 회귀 케이스 (임시 저장소로 현재 브랜치, upstream 해석까지).
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
# 값을 별도 토큰으로도 받는 옵션만. --force-with-lease, --signed, --recurse-submodules 는 `=` 로만 값을 받고
# --force-if-includes 는 플래그라 여기 넣으면 다음 토큰 (remote) 을 삼켜 브랜치가 remote 자리로 밀린다 (2026-10-22 보안 리뷰).
PUSH_OPT_WITH_VALUE = ("-o", "--push-option", "--repo", "--receive-pack", "--exec")
# 판정을 뒤집을 수 있는 설정 덮어쓰기 — refspec 없는 push 와 같이 오면 fail-closed.
CONFIG_OVERRIDE_PREFIXES = ("push.", "remote.", "branch.", "alias.", "include.", "includeif.", "url.", "core.hookspath")
ALIAS_DEFINE = re.compile(r"(?<![\w/.-])git\b[^\n;&|]*?(?:\s-c\s+alias\.|\s+config\b[^\n;&|]*\balias\.)", re.IGNORECASE)
# git 설정 주입 경로 전부 — 명령줄 밖에서 alias, push 설정을 바꿀 수 있어 fail-closed (2026-10-22 보안 리뷰 2차).
CONFIG_INJECT = re.compile(r"--config-env\b|(?<![\w-])(GIT_[A-Z_]+|HOME|XDG_CONFIG_HOME)=")
KNOWN_SUBCOMMANDS = frozenset("""add am apply archive bisect blame branch bundle cat-file check-ignore checkout cherry-pick
 clean clone commit config count-objects describe diff fetch filter-branch for-each-ref format-patch fsck gc grep help init
 log ls-files ls-remote ls-tree merge merge-base mv name-rev notes prune pull push rebase reflog remote repack replace
 reset restore rev-list rev-parse revert rm shortlog show show-ref stash status submodule switch symbolic-ref tag
 update-index update-ref var version worktree""".split())
GUIDE = ("develop / main push 는 사용자 몫이야 — Claude 는 feature 브랜치만 push 하고, merge 까지 한 뒤 "
         "\"push = 사용자\" 으로 남겨 둬. (규칙 ~/.claude/rules/git-workflow.md)")
SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
RAW_PUSH = re.compile(r"(?<![\w/.-])git\b(?:\s+-[^\s]+(?:\s+[^\s]+)?)*\s+push(?![\w./-])")


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
    """현재 브랜치 이름 (+ refspec 이 아예 없을 땐 @{push}, @{upstream} 이 가리키는 원격 브랜치 —
    push.default=upstream 이면 그리로 간다) 을 돌려준다. 못 풀면 None."""
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
    """refspec 의 목적지 브랜치 이름. HEAD/@ 는 '현재 브랜치' 라는 뜻으로 None 을 돌려준다."""
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
    """tokens[start] 가 git. push 서브커맨드면 dict, 아니면 None."""
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
    """deny 사유 문자열 또는 None (통과)."""
    if CONFIG_INJECT.search(command) and re.search(r"(?<![\w/.-])git\b", command):
        return "git 설정 주입 (--config-env, GIT_CONFIG_* env) 이 든 명령은 push 여부를 판정할 수 없어 막았어."
    if ALIAS_DEFINE.search(command):
        return "git alias 를 정의하거나 -c alias.* 로 넘기는 명령은 push 여부를 판정할 수 없어 막았어."
    alias_reason = alias_push_reason(command, cwd)
    if alias_reason:
        return alias_reason
    raw_hits = len(RAW_PUSH.findall(command))
    if raw_hits == 0:
        return None
    try:
        shlex.split(command, posix=True)
    except ValueError:
        return "git push 가 든 명령의 따옴표가 안 맞아 (파싱 불가) 막았어."
    parsed = 0
    reasons = []
    for segment in SEGMENT_SPLIT.split(command):
        if not RAW_PUSH.search(segment):
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return "git push 가 든 명령을 토큰으로 못 풀었어 (따옴표 불일치)."
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
            return ("따옴표나 bash -c, ssh 문자열 안의 git push 는 대상을 판정할 수 없어 막았어: "
                    "`%s`. push 를 바깥 명령으로 꺼내서 다시." % segment.strip()[:120])
    if parsed < raw_hits:
        return "git push 가 %d곳인데 %d곳만 판정됐어 — 숨은 push 가 있어 막았어." % (raw_hits, parsed)
    return reasons[0] if reasons else None


def alias_push_reason(command, cwd):
    """`git <alias>` 가 push 로 풀리면 deny 사유. 모르는 서브커맨드는 저장소의 alias 설정으로 확인한다."""
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
                return "`git %s` 는 alias 라 push 로 풀릴 수 있어 막았어 (alias 대신 git push 를 직접 써줘)." % tokens[j]
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
        return "`--all` / `--mirror` push 는 develop, main 까지 밀어내서 막았어: `%s`." % segment[:120]
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
        return ("설정 덮어쓰기 (-c push.*/remote.*/branch.*, --git-dir) 와 refspec 없는 push 는 "
                "대상을 판정할 수 없어 막았어: `%s`." % segment[:120])
    if need_resolve:
        repo_dir = push["repo_dir"] or cwd or os.getcwd()
        resolved = resolve_targets(repo_dir, include_upstream=no_refspec)
        if resolved is None:
            return ("refspec 없이 `%s` — 현재 브랜치를 못 풀어서 막았어 (repo 밖이거나 detached). "
                    "`git push origin <feature-branch>` 로 명시해 줘." % segment[:120])
        targets |= resolved
    hit = sorted(t for t in targets if t in PROTECTED)
    if hit:
        return "`%s` 는 %s 로 가는 push 라 막았어." % (segment[:120], ", ".join(hit))
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
        reason = "git push 가드 내부 오류 (%s) — 안전하게 막았어." % e.__class__.__name__
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
        # --- 명시 refspec
        case("origin develop → deny", "git push origin develop", work, True, "develop")
        case("origin main → deny", "git push -q origin main", work, True, "main")
        case("-u origin develop → deny", "git push -u origin develop", work, True)
        case("HEAD:develop → deny", "git push origin HEAD:develop", work, True)
        case("feat/x:develop → deny", "git push origin feat/x:develop", work, True)
        case("+develop (force refspec) → deny", "git push origin +develop", work, True)
        case("refs/heads/main → deny", "git push origin refs/heads/main", work, True)
        case("--force develop → deny", "git push --force origin develop", work, True)
        case("--force-with-lease=x develop → deny", "git push --force-with-lease=develop origin develop", work, True)
        case('--force-with-lease origin develop (값 없는 꼴) → deny', 'git push --force-with-lease origin develop', work, True, "develop")
        case('--force-if-includes --force-with-lease origin main → deny', 'git push --force-if-includes --force-with-lease origin main', work, True)
        case('--signed origin main → deny', 'git push --signed origin main', work, True, "main")
        case('--force-with-lease origin feat/x → pass', 'git push --force-with-lease origin feat/x', work, False)
        case('-c alias.p=push p origin develop → deny', 'git -c alias.p=push p origin develop', work, True, "alias")
        case('git config alias 정의 → deny', "git config --global alias.p 'push origin develop'", work, True, "alias")
        case('-c push.default=x, refspec 없음 → deny', 'git -c push.default=upstream push', work, True, "판정할 수 없어")
        case('-c user.name=x origin feat/x → pass', 'git -c user.name=x push origin feat/x', work, False)
        case('-c Alias.p (대문자) → deny', 'git -c Alias.p=push p origin develop', work, True, 'alias')
        case('-c PUSH.default, refspec 없음 → deny', 'git -c PUSH.default=upstream push', work, True, '판정할 수 없어')
        case('--config-env → deny', 'git --config-env=alias.p=X p origin develop', work, True, '설정 주입')
        case('GIT_CONFIG_PARAMETERS env → deny', "GIT_CONFIG_PARAMETERS='alias.p=push' git p origin develop", work, True, '설정 주입')
        case('GIT_CONFIG_COUNT env → deny', 'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p git p', work, True, '설정 주입')
        case('GIT_DIR env + push → deny', 'GIT_DIR=/tmp/x/.git git pu' + 'sh', work, True, '설정 주입')
        case('HOME env + git → deny', 'HOME=/tmp/h git pu' + 'sh origin feat/x', work, True, '설정 주입')
        case('-c include.path, refspec 없음 → deny', 'git -c include.path=/tmp/c pu' + 'sh', work, True, '판정할 수 없어')
        case('HOME env 인데 git 아님 → pass', 'HOME=/tmp/h ls', work, False)
        case(":develop (삭제) → deny", "git push origin :develop", work, True)
        case("--delete develop → deny", "git push origin --delete develop", work, True)
        case("--all → deny", "git push --all origin", work, True, "--all")
        case("--mirror → deny", "git push --mirror origin", work, True)
        case("--dry-run develop → deny (예외 없음)", "git push --dry-run origin develop", work, True)
        case("origin feat/x → pass", "git push origin feat/x", work, False)
        case("-u origin feat/x → pass", "git push -u origin feat/x", work, False)
        case("origin develop:feat/x (목적지 feature) → pass", "git push origin develop:feat/x", work, False)
        case("origin feat/x:refs/heads/feat/x → pass", "git push origin feat/x:refs/heads/feat/x", work, False)
        case("-o ci.skip origin feat/x → pass", "git push -o ci.skip origin feat/x", work, False)
        case("체인 안 develop → deny", "git fetch -q && git merge --no-ff x && git push origin develop", work, True)
        case("체인 안 feature → pass", "git add -A && git commit -q -m x && git push -u origin feat/x", work, False)
        case("push 없는 git → pass", "git push-check 2>/dev/null; git log --oneline -1", work, False)
        case("pushd 같은 단어 → pass", "pushd /tmp && git status && popd", work, False)
        # --- refspec 없음 → 실제 저장소로 현재 브랜치 해석
        case("refspec 없음, 현재 feat/x (upstream 없음) → pass", "git push", work, False)
        case("origin 만, 현재 feat/x → pass", "git push origin", work, False)
        case("origin HEAD, 현재 feat/x → pass", "git push origin HEAD", work, False)
        subprocess.run(["git", "-C", work, "branch", "-q", "--set-upstream-to=origin/develop", "feat/x"], check=True)
        case("refspec 없음, feat/x 가 origin/develop 를 upstream 으로 → deny", "git push", work, True, "develop")
        case("-u origin HEAD, 현재 feat/x (HEAD 는 upstream 무관) → pass", "git push -u origin HEAD", work, False)
        case("-C <repo> push (cwd 밖) → deny", "git -C %s push" % work, "/", True)
        subprocess.run(["git", "-C", work, "branch", "-q", "--unset-upstream", "feat/x"], check=True)
        subprocess.run(["git", "-C", work, "checkout", "-q", "develop"], check=True)
        case("refspec 없음, 현재 develop → deny", "git push", work, True, "develop")
        case("origin HEAD, 현재 develop → deny", "git push origin HEAD", work, True)
        case("cwd 가 repo 아님 + refspec 없음 → deny (못 풀면 막음)", "git push", tmp, True, "못 풀어서")
        subprocess.run(["git", "-C", work, "config", "alias.pp", "push origin develop"], check=True)
        subprocess.run(["git", "-C", work, "config", "alias.st", "status -s"], check=True)
        case("저장소 alias pp → push 로 풀려 deny", "git pp", work, True, "alias")
        case("저장소 alias st (status) → pass", "git st", work, False)
        case("모르는 서브커맨드 (alias 아님) → pass", "git whatchanged", work, False)
        # --- 숨은 push (fail-closed)
        case("bash -c 안 push → deny", "bash -c 'git push origin develop'", work, True, "판정할 수 없어")
        case("ssh 원격 문자열 안 push → deny", "ssh gw 'cd repo && git push origin feat/x'", work, True)
        case("따옴표 불일치 → deny", "git push origin feat/x && echo \"x", work, True)
        # --- 비 git
        case("빈 명령 → pass", "", work, False)
        case("ls → pass", "ls -la", work, False)
    n = len(results)
    print("self-test: %d/%d PASS" % (sum(results), n))
    return 0 if all(results) else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(main())
