#!/usr/bin/env python3
"""memory-lint: memory 디렉토리 기계 점검 (링크, wikilink, frontmatter, 공인 IP, 비밀값 대입, 문체, 라우터 크기).
사용: python3 ~/.claude/scripts/memory-lint.py <memory 디렉토리>... [--days N] [--allow-ip 8.8.8.8] [--archive]
  --style       문체 (줄표 등) 카운트 출력 (기본 끔)
  --days N      문체, frontmatter 점검은 최근 N일 수정 파일만 (기본 2). 링크, IP, 비밀값은 항상 전수
  --allow-ip P  허용할 공인 IP 또는 CIDR (쉼표 구분, 공개 서비스 VIP 등)
  --archive     archive/ 하위도 IP, 비밀값 스캔 (기본은 live 만)
출력은 값을 가리고 (IP 첫 옥텟, 비밀값 길이만) 파일:줄 만 찍는다. 종료코드 1 = 깨진 링크, 공인 IP, 비밀값 대입 중 하나라도 있음."""
import re, os, sys, glob, time, argparse

ap = argparse.ArgumentParser()
ap.add_argument('dirs', nargs='+')
ap.add_argument('--days', type=int, default=2)
ap.add_argument('--allow-ip', default='8.8.8.8,8.8.4.4', help='정확한 IP 또는 CIDR (쉼표 구분). 접두 면제는 안 받는다')
ap.add_argument('--archive', action='store_true')
ap.add_argument('--style', action='store_true', help='줄표, 가운뎃점, 겹낫표 카운트도 출력 (옛 파일은 면제라 기본 끔)')
ap.add_argument('--desc-dates', action='store_true', help='description 의 최신 날짜가 본문 최신 날짜보다 오래되면 DESC-STALE (경고만)')
a = ap.parse_args()
import ipaddress
allow = [ipaddress.ip_network(p.strip(), strict=False) for p in a.allow_ip.split(',') if p.strip()]
def allowed(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return any(addr in n for n in allow)
PRIV = re.compile(r'^(10\.|127\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.|255\.|169\.254\.)')
IP = re.compile(r'(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])')
SEC = re.compile(r'(?i)(password|passwd|pgpassword|_pw\b|secret|token|api[_-]?key|access[_-]?key)\s*([=:])\s*[\'"]?([A-Za-z0-9!@#$%^&*._/+-]{6,})')
JWT = re.compile(r'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}')
hard = 0
now = time.time()
home = os.path.expanduser('~')
rel = lambda p: os.path.relpath(p, home)

DIRS = [os.path.abspath(os.path.expanduser(x)) for x in a.dirs]
names = set()
for d in DIRS:
    for f in glob.glob(d + '/*.md') + glob.glob(d + '/archive/*.md'):
        names.add(os.path.basename(f)[:-3])
        if '/archive/' in f: names.add('archive/' + os.path.basename(f)[:-3])
        m = re.search(r'^name:\s*(.+)$', open(f, encoding='utf-8', errors='replace').read(), re.M)
        if m: names.add(m.group(1).strip().strip('"'))
for d in DIRS:
    live = sorted(glob.glob(d + '/*.md'))
    arch = sorted(glob.glob(d + '/archive/*.md')) if a.archive else []
    print(f'### {rel(d)} (live {len(live)} 파일)')
    # 1. 라우터 링크
    router = os.path.join(d, 'MEMORY.md')
    if os.path.exists(router):
        t = open(router, encoding='utf-8').read()
        bad = [l for l in re.findall(r'\]\(([^)#\s]+)', t) if not l.startswith('http') and not os.path.exists(os.path.normpath(os.path.join(d, l)))]
        size = os.path.getsize(router)
        long_lines = [(i, len(l.encode())) for i, l in enumerate(t.split('\n'), 1) if len(l.encode()) > 300]
        if size > 24000: hard += 1
        print(f'- 라우터 {size:,}B {"⚠️ 24,000B 초과" if size > 24000 else ("⚠️ 22,000B 경고" if size > 22000 else "OK")}, 깨진 링크 {len(bad)}, 300B 초과 줄 {len(long_lines)}')
        for b in bad: print(f'  BROKEN {re.sub(r"[^A-Za-z0-9._/가-힣-]", "?", b)[:80]}'); hard += 1
        for i, n in long_lines: print(f'  LONG MEMORY.md:{i} {n}B')
    # 2. wikilink + frontmatter (최근 파일)
    recent = [f for f in live if now - os.path.getmtime(f) < a.days * 86400 and os.path.basename(f) != 'MEMORY.md']
    for f in recent:
        t = open(f, encoding='utf-8', errors='replace').read()
        for w in sorted(set(re.findall(r'\[\[([^\]|]+)', t))):
            if w.strip() and w.strip() not in names: print(f'  WIKI? {os.path.basename(f)} -> {w}')
        m = re.search(r'^---\n(.*?)\n---', t, re.S)
        if not m: print(f'  NO-FRONTMATTER {os.path.basename(f)}'); continue
        ty = re.search(r'^\s*type:\s*(\w+)', m.group(1), re.M); ty = ty.group(1) if ty else None
        pre = os.path.basename(f).split('_')[0]
        exp = {'feedback': 'feedback', 'project': 'project', 'reference': 'reference', 'todo': 'project'}.get(pre)
        if exp and ty != exp: print(f'  TYPE {os.path.basename(f)} type={ty} expected={exp}')
        if not re.search(r'^description:', m.group(1), re.M): print(f'  NO-DESC {os.path.basename(f)}')
        if a.desc_dates:
            dm = re.search(r'^description:\s*(.+)$', m.group(1), re.M)
            def _dates(x):
                out = [(int(mo), int(dd)) for _, mo, dd in re.findall(r'(2026)-(\d{2})-(\d{2})', x)]
                out += [(int(mo), int(dd)) for mo, dd in re.findall(r'(?<![\d/.:-])(\d{1,2})/(\d{1,2})(?![\d/%])', x) if 1 <= int(mo) <= 12 and 1 <= int(dd) <= 31]
                return out
            if dm:
                mt = time.localtime(os.path.getmtime(f)); mtd = (mt.tm_mon, mt.tm_mday)
                dd, bd = _dates(dm.group(1)), [x for x in _dates('\n'.join(l for l in t[m.end():].split('\n') if not l.lstrip().startswith('>'))) if x <= mtd]
                if dd and bd and max(bd) > max(dd): print(f'  DESC-STALE {os.path.basename(f)} description 최신 {max(dd)[0]}/{max(dd)[1]} < 본문 최신 {max(bd)[0]}/{max(bd)[1]}')
        c = {'줄표': t.count('—') + t.count('–'), '가운뎃점': t.count('·'), '겹낫표': t.count('「') + t.count('」')}
        c = {k: v for k, v in c.items() if v}
        if c and a.style: print(f'  STYLE {os.path.basename(f)} {c}')
    # 3. 공인 IP, 비밀값 (live 전수 + 옵션 archive)
    for f in live + arch:
        for i, l in enumerate(open(f, encoding='utf-8', errors='replace'), 1):
            for m in IP.finditer(l):
                ip = m.group(0); o = [int(x) for x in m.groups()]
                if any(v > 255 for v in o) or PRIV.match(ip) or allowed(ip): continue
                print(f'  IP {rel(f)}:{i} {o[0]}.x.x.x'); hard += 1
            for m in SEC.finditer(l):
                v = m.group(3)
                if re.search(r'[<$]|wrapper|masked|REDACTED|예시|placeholder', v, re.I) or v in ('changeit', 'postgres', 'password'): continue
                if m.group(2) == ':' and re.fullmatch(r'[a-z]+', v) and re.match(r'\s*[가-힣]', l[m.end():]): continue  # 산문 'token: 생성 중단' 류
                print(f'  SECRET? {rel(f)}:{i} {m.group(1)}=<len {len(v)}>'); hard += 1
            if JWT.search(l): print(f'  JWT {rel(f)}:{i}'); hard += 1
print(f'=== 결과: {"문제 " + str(hard) + "건 (링크/IP/비밀값)" if hard else "링크, IP, 비밀값 0건"} ===')
sys.exit(1 if hard else 0)
