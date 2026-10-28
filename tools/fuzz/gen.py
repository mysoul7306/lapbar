"""Corpus generator for the differential fuzz. Usage: python3 -I gen.py <count> <seed> > corpus.jsonl

Each record: {"id", "kind", "script"}. kind = code (the bad call sits at a statement boundary - JS must see it),
hidden (the bad call text sits inside a string / comment / regex - JS must NOT see it), seed (known attack shapes).
"""
import json
import random
import sys

count, seed = int(sys.argv[1]), int(sys.argv[2])
rnd = random.Random(seed)

BAD_CALLS = [
    "agent('p', {model: 'haiku', effort: 'max'})",
    "await agent('p', {model: 'haiku', effort: 'max'})",
    "const r = await agent('p', {model: 'haiku', effort: 'max'})",
    "const r = await agent(`p`, {model: 'haiku', effort: 'max'})",
    "const r = await agent('p', {label: 'x', model: 'haiku', effort: 'max'})",
    "const r = await agent('p', {model: 'fable', effort: 'high'})",
    "const r = await agent('p', {effort: 'high'})",
    "const r = await agent('p', {model: m, effort: 'high'})",
    "const r = await agent('p', {model: 'opus'})",
    "const r = await parallel([() => agent('p', {model: 'haiku', effort: 'max'})])",
    "const t = `${await agent('p', {model: 'haiku', effort: 'max'})}`",
    "const t = `a${`${await agent('p', {model: 'haiku', effort: 'max'})}`}b`",
    "const r = await agent('p', {model: 'haiku', effort: 'max'}) / 2",
    "const r = x ? await agent('p', {model: 'haiku', effort: 'max'}) : 0",
]
GOOD_CALLS = [
    "const r = await agent('p', {model: 'opus', effort: 'high'})",
    "const r = await agent('p', {model: 'sonnet', effort: 'low'})",
]
STMTS = [
    "x = a / b", "x = a / b / c", "x = (a) / 2", "x = f(a) / 2", "x = arr[0] / 2", "x = 1/2/3", "x = a /b/ c",
    "x = a / b // c", "x = a / b /* c */ / d", "x = a / b / 'c'.length", "x = a / 2; y = 'q/'", "x = a /2; y = 'q/'",
    "if (a) /'/.test(s)", "if (a) /\"/.test(s)", "while (f(y)) /'/.test(s)", "for await (const q of qs) /\"/.test(q)",
    "for (const q of /'/.source) {}", "do /'/.test(s); while (0)", "switch (a) { case /'/.source: break }",
    "x = /'/.test(s)", "x = +/'/.source.length", "x = a +/'/.source", "x = i++ / 2", "x = i++ /'/", "x = typeof /'/",
    "x = void /'/.flags", "x = {a: /'/}", "x = [/'/]", "x = y ? /'/ : /\"/", "x = y ?? /'/", "x = () => /'/",
    "x = `${/'/.source}`", "x = f(/'/)", "x = /'/g.test(s)", "x = !/'/.test(s)", "x = ~/'/.lastIndex", "x = -/'/.lastIndex",
    "x = delete /'/.x", "x = await /'/", "x = a in /'/", "x = a instanceof /'/.constructor", "throw /'/", "x = a => /'/",
    "x = async () => /'/", "new /'/.constructor()", "x = { ['a/']: /'/ }", "({ f() { return /'/ } })", "x = a ? b : /'/",
    "x = /[/]/.test(s)", "x = /[']/", "x = /\\//", "x = /\\'/", "x = /`/.source", "x = /\\u0027/", "x = /a\\/'/",
    "x = 'a/b'", "x = \"a//b\"", "x = 'a/*b'", "x = '*/'", "x = '\\\\'", "x = \"it's\"", "x = `a'/b`", "x = \"//\"",
    "x = '/*'; y = '*/'", "x = '\\u0027'", "x = \"\\x2f\"", "x = `${a}/${b}`", "x = `a${'/'}b`", "x = `/${`'`}/`",
    "x = 'a\\\nb'", "x = 'a\\\r\nb'",
    "// comment with ' and /", "/* block ' */", "/* multi\nline ' */", "// a /* b", "/* a // b */",
    "label: for (;;) { break label }", "class A extends B {}", "class A extends /'/.constructor {}",
    "x = a\n/'/.test(s)", "x = a\n++b", "x = a\n/ b / c", "x = a\r\n/'/g", "x = a /\n2", "x = a--> b", "x = a-->b",
    "if (a) {} else /'/.test(s)", "if (a) {}\n/'/.test(s)", "x = f()\n/'/.test(s)", "x = arr[0]\n/'/.test(s)",
    "x = {}\n/'/.test(s)", "x = ({})\n/'/.test(s)", "x = function () {}\n/'/.test(s)", "x = class {}\n/'/.test(s)",
    "x = y++\n/'/.test(s)", "x = `${a}`\n/'/.test(s)", "x = 'z'\n/'/.test(s)", "x = 1\n/'/.test(s)",
    "x = a ? 1 : 2\n/'/.test(s)", "x = ...a", "f(...a / b)", "f(.../'/.source)", "x = [...a / b]", "x = {...a / b}",
    "x = a?.b / 2", "x = a?.[0] / 2", "x = a?.(1) / 2", "x = a ** /'/.lastIndex", "x = a ?? /'/", "x = a ||= /'/",
    "x = a <= /'/.lastIndex", "x = a >= /'/.lastIndex", "x = a << /'/.lastIndex", "x = a >>> /'/.lastIndex",
    "x = 0x1f / 2", "x = 1e3 / 2", "x = 1n / 2n", "x = .5 / 2", "x = a.b / 2", "x = a.if / 2", "x = a.return / 2",
    "x = a.default / 2", "x = a.extends / 2", "x = a.of / 2", "x = of / 2", "x = async / 2", "x = get / 2", "x = yield / 2",
    "let of = 1; x = of /'/.lastIndex", "x = \u2028", "x = 'a\u2028b'", "x = `a\u2028b`", "x = a /* \u2028 */ / 2",
    "x = a // \u2028 /'/\n", "x = #priv in o", "x = a.#b / 2", "x = `${a / b}`", "x = `${a}/${/'/.source}`",
    "x = `\\${a / b}`", "x = `\\`/`", "x = '\\'/'", "x = \"\\\"/\"", "x = /'/ / 2", "x = /'/ /'/", "x = a / /'/.lastIndex",
    "x = (/'/).test(s)", "x = [/'/][0]", "x = (a, /'/)", "x = {a: 1}.a / 2", "x = {a: 1}\n/'/.test(s)",
    "export const meta = {name: 'f', description: 'd'}", "import x from 'y/z'", "export default /'/",
    "x = a\n--> b", "<!-- x", "x = a <!-- b", "x = `a//b`", "x = `/*`", "x = `*/`", "x = ''", "x = \"\"", "x = ``",
    "x = ''/2", "x = \"\"/2", "x = ``/2", "x = '' / '/'", "x = 'a' + / /.source", "x = a/ /b/.source",
    "x = a / b + '%' // progress", "x = done / total + '%' // p", "x = a / b + ' of ' + c / d", "x = n / 2; const u = 'a/b'",
    "x = x/2//c", "x = x/2/*c*/", "x = Math.round(budget.remaining()/1000)", "x = budget.total ? Math.floor(budget.total / 100_000) : 5",
]
SEPS = [";\n", "\n", "; ", "\r\n", " ", ";\r\n", "\n\n", " ; "]
WRAPS = [
    lambda s: s,
    lambda s: "{\n" + s + "\n}",
    lambda s: "if (c) {\n" + s + "\n}",
    lambda s: "const g = async () => {\n" + s + "\n}",
    lambda s: "for (const i of items) {\n" + s + "\n}",
    lambda s: "try {\n" + s + "\n} catch (e) {}",
    lambda s: "(async () => {\n" + s + "\n})()",
    lambda s: "const f = async function () {\n" + s + "\n}",
    lambda s: "label: {\n" + s + "\n}",
    lambda s: "switch (a) { default:\n" + s + "\n}",
]
HIDERS = [
    lambda c: "x = '" + c.replace("'", "\\'") + "'",
    lambda c: "x = \"" + c.replace('"', '\\"') + "\"",
    lambda c: "x = `" + c.replace("`", "\\`").replace("${", "\\${") + "`",
    lambda c: "// " + c.replace("\n", " "),
    lambda c: "/* " + c.replace("*/", "* /") + " */",
    lambda c: "x = /" + c.replace("/", "\\/").replace("\n", " ") + "/",
    lambda c: "x = `${'" + c.replace("'", "\\'") + "'}`",
]
SEEDS = [
    ("S1 for-await header", "for await (const x of xs) /'/.test(x); " + BAD_CALLS[2] + "; for await (const y of ys) /'/.test(y)"),
    ("S2 unary plus", "const n = 1 + +/'/.source.length; " + BAD_CALLS[2] + "; const m = 1 + +/'/.source.length"),
    ("S4 extends", "class A extends /'/.constructor {}; " + BAD_CALLS[2] + "; class B extends /'/.constructor {}"),
    ("S5 break label", "lbl: for (;;) { break /'/.test(s) ? lbl : lbl }; " + BAD_CALLS[2] + "; lbl2: for (;;) { break /'/.test(s) ? lbl2 : lbl2 }"),
    ("S7 regex ending //", "const n = 1 + +/a//1; " + BAD_CALLS[2] + "\nreturn r"),
    ("S8 regex ending /*", "const n = 1 + +/a/*2\n" + BAD_CALLS[2] + "\nconst m = 1 + +/b*/.lastIndex"),
    ("S9 backtick regex", "const n = 1 + +/`/.source\n" + BAD_CALLS[2] + "\nconst m = 1 + +/`/.source"),
    ("C1 regex after )", "if (a) /'/.test(s); " + BAD_CALLS[2] + "; if (b) /'/.test(s)"),
    ("C2 regex after spread", "f(.../'/.source); " + BAD_CALLS[2] + "; f(.../'/.source)"),
    ("A1 ternary key", "const r = await agent('p', {label: c ? model : 'x', model: 'haiku', effort: 'max'})"),
    ("T1 call inside template interpolation", BAD_CALLS[10]),
    ("T2 nested template", BAD_CALLS[11]),
    ("D1 division then string with slash", "const n = total / 2; const u = 'a/b'\n" + GOOD_CALLS[0]),
    ("D2 glued comment", "const half = x/2//c\n" + GOOD_CALLS[0]),
    ("N1 newline regex pair", "x = a\n/'/.test(s)\n" + BAD_CALLS[2] + "\nx = a\n/'/.test(s)"),
    ("N2 ASI after }", "x = {}\n/'/.test(s)\n" + BAD_CALLS[2] + "\nx = {}\n/'/.test(s)"),
    ("N3 ASI after ++", "x = y++\n/'/.test(s)\n" + BAD_CALLS[2] + "\nx = y++\n/'/.test(s)"),
]

out = []
n = 0
for name, script in SEEDS:
    out.append({"id": "seed-%02d" % n, "kind": "seed", "name": name, "script": script}); n += 1
while len(out) < count:
    k = rnd.random()
    stmts = [rnd.choice(STMTS) for _ in range(rnd.randint(1, 5))]
    if k < 0.75:
        call = rnd.choice(BAD_CALLS)
        pos = rnd.randint(0, len(stmts))
        stmts.insert(pos, call)
        kind = "code"
    elif k < 0.9:
        call = rnd.choice(BAD_CALLS)
        hider = rnd.choice(HIDERS)
        stmts.insert(rnd.randint(0, len(stmts)), hider(call))
        if rnd.random() < 0.5:
            stmts.insert(rnd.randint(0, len(stmts)), rnd.choice(GOOD_CALLS))
        kind = "hidden"
    else:
        stmts.insert(rnd.randint(0, len(stmts)), rnd.choice(GOOD_CALLS))
        kind = "good"
    sep = rnd.choice(SEPS)
    body = sep.join(stmts)
    script = rnd.choice(WRAPS)(body)
    if rnd.random() < 0.3:
        script = "export const meta = {name: 'f', description: 'd'}\n" + script
    out.append({"id": "f-%06d" % n, "kind": kind, "script": script}); n += 1
for rec in out:
    sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
