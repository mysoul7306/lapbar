#!/usr/bin/env python3
"""memory-lint: mechanical check of memory directories (links, wikilinks, frontmatter, public IPs, secret assignments, style, router size).
Usage: python3 ~/.claude/scripts/memory-lint.py <memory dir>... [--days N] [--allow-ip 8.8.8.8] [--archive]
  --style       print style counts (em/en dash etc.) (off by default)
  --days N      style and frontmatter checks cover only files modified in the last N days (default 2). Links, IPs, secrets are always checked in full
  --allow-ip P  public IPs or CIDRs to allow (comma separated, e.g. public service VIPs)
  --archive     also scan archive/ for IPs and secrets (default is live files only)
Output hides values (first IP octet, secret length only) and prints file:line only. Exit code 1 = at least one broken link, public IP, or secret assignment."""
import re, os, sys, glob, time, argparse

ap = argparse.ArgumentParser()
ap.add_argument('dirs', nargs='+')
ap.add_argument('--days', type=int, default=2)
ap.add_argument('--allow-ip', default='8.8.8.8,8.8.4.4', help='exact IPs or CIDRs (comma separated). Prefix exemptions are not accepted')
ap.add_argument('--archive', action='store_true')
ap.add_argument('--style', action='store_true', help='also print em/en dash, middle dot, and corner bracket counts (off by default because old files are exempt)')
ap.add_argument('--desc-dates', action='store_true', help='DESC-STALE when the latest date in description is older than the latest date in the body (warning only)')
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
LINK_SAFE = r'[^A-Za-z0-9._/\uac00-\ud7a3-]'  # \uac00-\ud7a3 = the Hangul syllable block; kept outside the f-string (Python 3.11 forbids backslashes there)
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
    print(f'### {rel(d)} (live {len(live)} files)')
    # 1. router links
    router = os.path.join(d, 'MEMORY.md')
    if os.path.exists(router):
        t = open(router, encoding='utf-8').read()
        bad = [l for l in re.findall(r'\]\(([^)#\s]+)', t) if not l.startswith('http') and not os.path.exists(os.path.normpath(os.path.join(d, l)))]
        size = os.path.getsize(router)
        long_lines = [(i, len(l.encode())) for i, l in enumerate(t.split('\n'), 1) if len(l.encode()) > 300]
        if size > 24000: hard += 1
        print(f'- router {size:,}B {"WARN over 24,000B" if size > 24000 else ("WARN over 22,000B" if size > 22000 else "OK")}, broken links {len(bad)}, lines over 300B {len(long_lines)}')
        for b in bad: print(f'  BROKEN {re.sub(LINK_SAFE, "?", b)[:80]}'); hard += 1
        for i, n in long_lines: print(f'  LONG MEMORY.md:{i} {n}B')
    # 2. wikilinks + frontmatter (recent files)
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
                if dd and bd and max(bd) > max(dd): print(f'  DESC-STALE {os.path.basename(f)} description latest {max(dd)[0]}/{max(dd)[1]} < body latest {max(bd)[0]}/{max(bd)[1]}')
        c = {'dash': t.count('—') + t.count('–'), 'middle-dot': t.count('·'), 'corner-bracket': t.count('「') + t.count('」')}
        c = {k: v for k, v in c.items() if v}
        if c and a.style: print(f'  STYLE {os.path.basename(f)} {c}')
    # 3. public IPs, secrets (all live files + archive when asked)
    for f in live + arch:
        for i, l in enumerate(open(f, encoding='utf-8', errors='replace'), 1):
            for m in IP.finditer(l):
                ip = m.group(0); o = [int(x) for x in m.groups()]
                if any(v > 255 for v in o) or PRIV.match(ip) or allowed(ip): continue
                print(f'  IP {rel(f)}:{i} {o[0]}.x.x.x'); hard += 1
            for m in SEC.finditer(l):
                v = m.group(3)
                if re.search(r'[<$]|wrapper|masked|REDACTED|\uc608\uc2dc|placeholder', v, re.I) or v in ('changeit', 'postgres', 'password'): continue
                if m.group(2) == ':' and re.fullmatch(r'[a-z]+', v) and re.match(r'\s*[\uac00-\ud7a3]', l[m.end():]): continue  # prose: a lowercase word after the colon, then Korean text
                print(f'  SECRET? {rel(f)}:{i} {m.group(1)}=<len {len(v)}>'); hard += 1
            if JWT.search(l): print(f'  JWT {rel(f)}:{i}'); hard += 1
print(f'=== result: {str(hard) + " issue(s) (links/IP/secrets)" if hard else "links, IP, secrets: 0"} ===')
sys.exit(1 if hard else 0)
