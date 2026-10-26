#!/usr/bin/env python3
"""SessionStart hook: runs memory-lint on the current project's Local memory + Global memory and reports briefly
via additionalContext only when there are broken links, public IPs, secret assignments, JWTs, or a router over 24,000B.
- The same result is reported once per session (session_id) (not re-injected on resume, clear, or compact restarts)
- Local memory is located by the git common dir (a worktree session sees the main checkout's Local)
- Under paths listed in MEMORY_LINT_SKIP_DIRS (comma list, empty by default) Local is not checked
- A child failure is one [check failed] line, distinct from the normal silent result. Exit 0 on any exception
Config = ~/.claude/memory-lint.json {"allow_ip": ["8.8.8.8"], "max_bytes": 1000}
Manual test: printf '{"cwd":"/path","session_id":"x"}' | python3 ~/.claude/hooks/memory-lint-hook.py"""
import json, os, re, sys, subprocess, hashlib
HOME = os.path.expanduser('~')
LINT = os.path.join(HOME, '.claude', 'scripts', 'memory-lint.py')
CFG = os.path.join(HOME, '.claude', 'memory-lint.json')
SKIP_DIRS = [d for d in os.environ.get('MEMORY_LINT_SKIP_DIRS', '').split(',') if d]
STATE = os.path.join(HOME, '.claude', 'logs', 'memory-lint-hook.state')
HARD = ('  BROKEN ', '  IP ', '  SECRET? ', '  JWT ')
def emit(msg):
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': msg}}, ensure_ascii=False))
def local_dir(cwd):
    if any(d in cwd + '/' for d in SKIP_DIRS):
        return None
    root = cwd
    try:
        r = subprocess.run(['git', '-C', cwd, 'rev-parse', '--git-common-dir'], capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            common = r.stdout.strip()
            if not os.path.isabs(common):
                common = os.path.normpath(os.path.join(cwd, common))
            root = os.path.dirname(common) if os.path.basename(common) == '.git' else root
    except Exception:
        pass
    d = os.path.join(HOME, '.claude', 'projects', re.sub(r'[^A-Za-z0-9]', '-', root), 'memory')  # Claude Code encoding: every non-alphanumeric becomes '-' (user_name → user-name)
    return d if os.path.isdir(d) else None
def main():
    try:
        payload = json.loads(sys.stdin.read() or '{}')
    except Exception:
        payload = {}
    cwd = (payload.get('cwd') or os.getcwd()).rstrip('/')
    sid = str(payload.get('session_id') or 'nosession')
    cfg = {}
    try:
        cfg = json.load(open(CFG))
    except Exception:
        pass
    allow = ','.join(cfg.get('allow_ip', ['8.8.8.8']))
    max_bytes = int(cfg.get('max_bytes', 1000))
    dirs = [d for d in (local_dir(cwd), os.path.join(HOME, '.claude', 'memory')) if d and os.path.isdir(d)]
    if not dirs or not os.path.exists(LINT):
        return
    try:
        r = subprocess.run([sys.executable, LINT, *dirs, '--allow-ip', allow, '--days', '1'], capture_output=True, text=True, timeout=4)
    except subprocess.TimeoutExpired:
        emit('memory-lint (SessionStart): [check failed] timeout 4s - run python3 ~/.claude/scripts/memory-lint.py by hand'); return
    if r.returncode not in (0, 1):
        emit(f'memory-lint (SessionStart): [check failed] rc={r.returncode} {(r.stderr or "").strip().splitlines()[-1:] }'); return
    lines = r.stdout.split('\n')
    items = [l.strip() for l in lines if 'over 24,000B' in l] + [l.strip() for l in lines if l.startswith(HARD)]
    if not items:
        return
    digest = hashlib.sha256('\n'.join(items).encode()).hexdigest()[:16]
    key = f'{sid}:{digest}'
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        seen = open(STATE).read().split('\n') if os.path.exists(STATE) else []
        if key in seen:
            return
        keep = [s for s in seen if s][-200:] + [key]
        open(STATE, 'w').write('\n'.join(keep) + '\n')
    except Exception:
        pass
    head = f'memory-lint (SessionStart): {len(items)} issue(s) - mask values with a wrapper reference, fix links. Full report = python3 ~/.claude/scripts/memory-lint.py <Local> ~/.claude/memory'
    out = [head]
    for it in items[:5]:
        if len(('\n'.join(out + [it])).encode()) > max_bytes:
            break
        out.append(it)
    more = len(items) - (len(out) - 1)
    if more > 0:
        out.append(f'(+{more} more)')
    emit('\n'.join(out))
if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'memory-lint-hook: skip ({type(e).__name__})', file=sys.stderr)
    sys.exit(0)
