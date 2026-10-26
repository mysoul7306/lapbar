#!/usr/bin/env python3
"""SessionStart hook: memory-lint 를 현재 프로젝트 Local memory + Global memory 에 돌려
깨진 링크, 공인 IP, 비밀값 대입, JWT, 라우터 24,000B 초과가 있을 때만 additionalContext 로 짧게 알린다.
- 같은 세션 (session_id) 에서 같은 결과는 한 번만 (resume, clear, compact 재기동 때 재주입 안 함)
- Local memory 는 git common dir 기준 (worktree 세션도 메인 체크아웃의 Local 을 본다)
- MEMORY_LINT_SKIP_DIRS 에 든 경로 (쉼표 목록, 기본 없음) 아래에선 Local 을 보지 않는다
- 자식 실패는 [검사 실패] 한 줄로 정상 무출력과 구분. 어떤 예외에도 exit 0
설정 = ~/.claude/memory-lint.json {"allow_ip": ["8.8.8.8"], "max_bytes": 1000}
손으로 시험: printf '{"cwd":"/path","session_id":"x"}' | python3 ~/.claude/hooks/memory-lint-hook.py"""
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
    d = os.path.join(HOME, '.claude', 'projects', re.sub(r'[^A-Za-z0-9]', '-', root), 'memory')  # Claude Code 인코딩: 영숫자 외 전부 '-' (user_name → user-name)
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
        emit('🧹 memory-lint (SessionStart): [검사 실패] timeout 4s - 손으로 python3 ~/.claude/scripts/memory-lint.py 실행'); return
    if r.returncode not in (0, 1):
        emit(f'🧹 memory-lint (SessionStart): [검사 실패] rc={r.returncode} {(r.stderr or "").strip().splitlines()[-1:] }'); return
    lines = r.stdout.split('\n')
    items = [l.strip() for l in lines if '24,000B 초과' in l] + [l.strip() for l in lines if l.startswith(HARD)]
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
    head = f'🧹 memory-lint (SessionStart): 문제 {len(items)}건 - 값은 wrapper 참조로 마스킹, 링크는 수정. 전체 = python3 ~/.claude/scripts/memory-lint.py <Local> ~/.claude/memory'
    out = [head]
    for it in items[:5]:
        if len(('\n'.join(out + [it])).encode()) > max_bytes:
            break
        out.append(it)
    more = len(items) - (len(out) - 1)
    if more > 0:
        out.append(f'(+{more}건 더)')
    emit('\n'.join(out))
if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'memory-lint-hook: skip ({type(e).__name__})', file=sys.stderr)
    sys.exit(0)
