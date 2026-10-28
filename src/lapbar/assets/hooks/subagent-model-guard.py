#!/usr/bin/env python3
"""Subagent model guard (PreToolUse: Task | Agent | Workflow).

Background: a subagent that omits model inherits the parent (session) model as is. Model tier rules kept
recurring when they were only advice, so this blocks it at the harness level.

What it blocks is **omitted or unclear values** and **upper/lower tiers without owner approval** - a literal opus / sonnet
model passes, fable / haiku only together with an approval mark inside that call (`[<owner>-approved: fable]`,
`[<owner>-approved: haiku]`, where `<owner>` is env SUBAGENT_GUARD_OWNER and defaults to `owner`), or haiku in role mode (rule 9).

Check rules:
  1. model and effort count only as **string literals at the top level of opts** - variables, ternaries, parenthesized
     expressions, nested objects (metadata.model), and values inside nested agent() calls are all denied. Values are read
     only at key positions found in the code with strings and comments blanked out (the text "model: 'opus'" inside a
     prompt string does not fool it). A comment before the value (`effort: /*j*/ 'high'`) is read as a literal.
  2. inherit and names outside the allow list are denied. Full model IDs (claude-haiku-4-5 and similar) are normalized
     to their family and get the same rules.
  3. Up to FABLE_MAX fable agent() **call sites** per Workflow script. One site inside a loop that runs N times cannot be
     counted statically - the run-count limit is left to the rule (rules/subagent-review.md) and to announcing the scale.
  4. fable and haiku only with a mark **inside that call** (prompt for Agent, inside the parentheses for agent(); a comment
     inside the parentheses counts too). The model could write the mark itself; that limit is known, and writing a mark
     without owner approval is treated as a rule violation.
  5. effort is a lowercase literal from EFFORTS. Levels above the cap EFFORT_CAP (default xhigh, set with env
     SUBAGENT_EFFORT_CAP - put it in settings.json env to cover every session) need a `[<owner>-approved: <level>]` mark
     inside that call.
  6. opts must be **one flat object literal** - an object chosen by a ternary (`c ? {…} : {…}`), spread (`...`), computed
     keys (`['effort']`), get/set, and identifier escapes get a shape deny (bypasses where a later value overrides the
     earlier literal). Shorthand (`{effort}`) is reported as [a variable].
  7. fail-closed: a regex literal (a `/` where JS reads one: after an operator, an opening bracket, `}`, a spread, the
     start of the script, a keyword such as return, or the `)` that closes an if / while / for / with header - a quote or
     a comment opener inside the regex would pair up with later text and hide an agent() in between; division after an
     identifier, a number, `]` or any other `)` is read as division; as a safety net for any context the hook misreads,
     a `/` read as division is still denied when the text up to the next `/` on the same line holds an odd number of
     quotes, or when that next `/` starts a comment glued to a word (`a//`, `a/*`) - the two shapes that let a regex hide
     code), a backslash in code position (a regex body with
     escaped slashes would open a comment; an identifier escape renames a key), a newline inside a quoted string (a JS syntax
     error; the same hiding trick across lines), a unicode line separator (U+2028 / U+2029) anywhere and an HTML-style
     comment (`<!--`, line-leading `-->`) in code position (JS treats them as line ends and comments, the hook would not),
     an unclosed string or comment, an unreadable scriptPath, and internal hook exceptions are all denied. A call by saved workflow name looks up `~/.claude/workflows/<name>.js`
     and `<cwd>/.claude/workflows/<name>.js` and checks it; if neither exists (built in) it passes.
  8. deny text = reason + where to fix + one short GUIDE line. Detailed rules live in rules/subagent-review.md.
  9. haiku role mode (env SUBAGENT_HAIKU_ROLE_MODE=role, default off = the mark rule only): haiku passes without an
     approval mark when that call carries both a `[haiku role: <mechanical job>]` mark and a `[verify: <mechanical check
     the parent will run>]` mark and its effort is medium or high (low, xhigh and max are denied - a job that needs that
     much thinking is not a haiku job). The approval mark keeps working in either mode.
 10. The owner name inside the marks comes from env SUBAGENT_GUARD_OWNER (default `owner`, so the default mark is
     `[owner-approved: fable]`); a value left as a `<placeholder>` counts as unset. Put it in settings.json env so it
     reaches every session.
 11. The checks are linear in the script size (one prefix depth table per text); the self-test asserts that a 1500-key
     schema passes in under 2 seconds, because a hook that outruns the harness timeout is skipped, not denied.

`python3 subagent-model-guard.py --self-test` = regression cases (must_contain in the self-test is reason-only text that is not in GUIDE).
"""
import json
import os
import re
import sys

GUIDE = (
    "Rules: model is an opus / sonnet literal (no omission, inherit, or variables). "
    "Single Agent = opus, Workflow agent() = opus, mechanical collection/parsing = sonnet. "
    "fable only when the owner approved it for that task - a '[<owner>-approved: fable]' mark inside that call (up to 2 sites per workflow), "
    "haiku also only with a '[<owner>-approved: haiku]' mark (role mode, env SUBAGENT_HAIKU_ROLE_MODE=role: a '[haiku role: …]' plus a '[verify: …]' mark, effort medium/high). "
    "effort is a literal too (low/medium/high/xhigh, max needs a '[<owner>-approved: max]' mark - work agents high, mechanical collection low, judge/critic xhigh. omitted = inherits the session effort). "
    "<owner> = env SUBAGENT_GUARD_OWNER, default 'owner'. "
    "Limits differ by plan, so check /usage first. Details = ~/.claude/rules/subagent-review.md"
)

TIERS = ("fable", "opus", "sonnet", "haiku")
FABLE_MAX = 2                      # cap on fable agent() call sites in one Workflow script
OWNER_ENV = "SUBAGENT_GUARD_OWNER"   # name inside the approval marks ([<owner>-approved: …]). A team member puts their own name in settings.json env
OWNER_DEFAULT = "owner"
EFFORTS = ("low", "medium", "high", "xhigh", "max")   # lowest first. Agent/Task effort and workflow agent() effort: must be one of these as a literal
EFFORT_CAP_DEFAULT = "xhigh"       # levels above this (max) need the mark ([owner-approved: max]) inside that call. Set with env SUBAGENT_EFFORT_CAP
HAIKU_ROLE_MODE_ENV = "SUBAGENT_HAIKU_ROLE_MODE"   # "role" turns on haiku role mode; anything else = the mark rule only
HAIKU_ROLE_RE = re.compile(r"\[haiku role:\s*[^\]\s][^\]]*\]")   # [haiku role: lint queue cleanup] - which mechanical job
HAIKU_VERIFY_RE = re.compile(r"\[verify:\s*[^\]\s][^\]]*\]")       # [verify: memory-lint rerun, 0 findings] - the mechanical check the parent will run
HAIKU_EFFORT_MIN = "medium"        # lowest effort for a haiku role (low is denied)
HAIKU_EFFORT_MAX = "high"          # highest effort for a haiku role (a job that needs more thinking is not a haiku job)

AGENT_CALL = re.compile(r'(?<![\w$])agent\s*\(')  # x.agent( also counts as a call and gets the same checks
# agent used outside a call (alias assignment, argument, property access) hides call sites, so deny (2026-10-22 security review).
AGENT_ALIAS = re.compile(r'(?<![\w$])agent\b(?!\s*\()')
# Dynamic access (eval, Function, globalThis, computed property) hides call sites, so deny.
DYNAMIC_ACCESS = re.compile(r'(?<![\w$.])(eval|Function|globalThis)\s*[(\[.]|\[\s*[\'"`]?agent[\'"`]?\s*\]')
LITERAL = re.compile(r"""\s*(['"`])([A-Za-z0-9_.\-]+)\1""")         # string literal in a value position
REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^.")                           # a `/` after one of these is a regex literal, not division (`.` = after a spread)
REGEX_KEYWORDS = {"return", "typeof", "case", "in", "of", "void", "delete", "instanceof", "new", "throw", "yield", "await", "else", "do", "default",
                  "extends", "break", "continue"}                  # a `/` after one of these words is a regex too (not after `.word`, which is a property)
CONTROL_WORDS = {"if", "while", "for", "with"}                      # a `/` after the `)` that closes one of these headers is a regex, after any other `)` division
LINE_SEPARATORS = (" ", " ")                              # JS line terminators besides LF and CR - never needed in a script, so fail-closed
OPT_KEYS = ("model", "effort")                                       # keys that must be literals at the top level of agent() opts
QUOTED_KEY = re.compile(r"""(['"`])([ \r\n]*)\1\s*:""")              # a quoted key in the blanked code (its content is blanks of the same length; a line continuation leaves a newline)


def owner():
    """Name inside the approval marks - env SUBAGENT_GUARD_OWNER, or the default when it is empty or still a <placeholder>."""
    v = os.environ.get(OWNER_ENV, "").strip()
    if not v or (v.startswith("<") and v.endswith(">")):
        return OWNER_DEFAULT
    return v


def haiku_mark():
    return "[%s-approved: haiku]" % owner()   # haiku approval mark - must be inside that call (prompt for Agent)


def fable_mark():
    return "[%s-approved: fable]" % owner()   # fable approval mark - same rule as haiku


def effort_mark(level):
    return "[%s-approved: %s]" % (owner(), level)   # approval mark for levels above the effort cap (same form as the fable and haiku marks)


def deny(reason):
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, sys.stdout)
    sys.exit(0)


# ----------------------------------------------------------------------------- reading the source

def read_script(tool_input, cwd=None):
    """(script, source, error) - script None means nothing to check (an error, if present, is the deny reason). cwd = the session cwd from the payload."""
    script = tool_input.get("script")
    if script:
        return script, "script", None
    path = tool_input.get("scriptPath")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read(), "scriptPath", None
        except (OSError, UnicodeDecodeError) as e:
            return None, "scriptPath", "cannot read scriptPath (%s): %s" % (e.__class__.__name__, path)
    name = tool_input.get("name")
    if name:
        for base in (os.path.expanduser("~/.claude/workflows"), os.path.join(cwd or os.getcwd(), ".claude", "workflows")):
            p = os.path.join(base, name + ".js")
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as fh:
                        return fh.read(), "name:" + p, None
                except (OSError, UnicodeDecodeError) as e:
                    return None, "name", "cannot read saved workflow %s (%s)" % (p, e.__class__.__name__)
    return None, None, None   # built-in workflows etc. - if the source is unknown, nothing is checked


# ----------------------------------------------------------------------------- extracting the code region

def blank_strings_and_comments(src):
    """Return (text of the same length with string and comment contents replaced by spaces, end state).

    Newlines are kept so line numbers and offsets match the original 1:1. Inside a template literal, ${ … } is code
    and stays as is (nested braces are tracked). Regex literals are not parsed: a `/` where JS reads a regex (after an
    operator, an opening bracket, `}`, a spread, the start of the script, a keyword, or the `)` closing an if / while /
    for / with header - a paren stack remembers which `(` followed such a word) raises ValueError (fail-closed), because
    a quote or comment opener inside the regex would pair up with later text and hide the code in between. A `/` after
    an identifier, a number, `]` or any other `)` is division, as in JS. Because the context can still be misread (the
    hook is not a parser), a division is also refused when the text up to the next `/` on the same line has an odd
    number of quotes or when that `/` opens a comment glued to a word - without a quote or a comment opener a misread
    regex body cannot hide anything. A newline inside a ' or " string is a JS syntax error and raises ValueError as
    well; U+2028 / U+2029 anywhere and HTML-style comments in code position raise too.
    """
    out = []
    n = len(src)
    i = 0
    state = "code"
    stack = []            # brace depth of each code level entered through ${ inside a template
    last = None           # last non-blank character seen in code state - decides whether a `/` is division or a regex
    prev = None           # the character before that (`++ /` and `-- /` are division)
    word = ""             # the identifier word just before (a `/` after return, typeof, … is a regex)
    word_open = False     # still appending to that word (a blank closes it)
    prev_word = ""        # the word before that (`for await (` is a control header)
    kw_ok = True          # the current word is not a property name (`cfg.default / 2` is division)
    parens = []           # per open `(`: did an if / while / for / with precede it (then a `/` after its `)` is a regex)
    ctrl_close = False    # did the last `)` close such a header
    if any(sep in src for sep in LINE_SEPARATORS):
        raise ValueError("a unicode line separator (U+2028 / U+2029) ends a line in JS but not here - remove it")
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                state = "line"; out.append("  "); i += 2; continue
            if c == "/" and nxt == "*":
                state = "block"; out.append("  "); i += 2; continue
            is_regex = (last is None or (last in REGEX_PREV and not (last in "+-" and prev == last)) or (word in REGEX_KEYWORDS and kw_ok)
                        or (last == ")" and ctrl_close))
            if c == "/" and is_regex:
                # start of a regex literal - a quote or a comment opener inside it would open string or comment state, and
                # when later text closes that state again, every agent() in between is blanked and escapes the check
                raise ValueError("a regex literal (/…/) cannot be read by the hook - switch to new RegExp('…')")
            if c == "/":
                # division as far as the hook can tell. Safety net for a misread context: a regex body hides code only
                # through an unpaired quote or a comment opener glued to its closing slash, so refuse exactly those shapes
                j = i + 1
                while j < n and src[j] not in "/\n\r":
                    j += 1
                if j < n and src[j] == "/":
                    seg = src[i + 1:j]
                    if any(seg.count(q) % 2 for q in ("'", '"', "`")):
                        raise ValueError("a `/` read as division is followed on the same line by an unpaired quote and another `/` - "
                                         "if that is a regex literal switch to new RegExp('…'); if it is division, move the string or the second `/` to its own line")
                    if src[j + 1:j + 2] in ("/", "*") and seg and (seg[-1].isalnum() or seg[-1] in "_$."):
                        raise ValueError("a comment opener (// or /*) glued to a word after a division on the same line looks like a regex closing - "
                                         "put a space before the comment, or switch to new RegExp('…')")
            if c == "\\":
                # legal JS has no backslash in code position: it is a regex body (`/a\/\//` - the escaped slashes would
                # open a line comment and hide the rest of the line) or an identifier escape (`\\u006d`); both are unreadable here
                raise ValueError("a backslash outside strings and comments (a regex literal with an escaped slash, or an identifier escape) cannot be read - use new RegExp('…')")
            if c == "<" and src.startswith("<!--", i):
                raise ValueError("an HTML-style comment (<!--) in code position cannot be read - remove it")
            if c == "-" and src.startswith("-->", i):
                raise ValueError("an HTML-style comment (-->) in code position cannot be read - remove it (write `a-- > b` with the operator spaced)")
            if c in ("'", '"', "`"):
                state = c; out.append(c); prev, last, word = last, c, ""; i += 1; continue        # keep the quote itself (to track literal positions)
            if stack:
                if c == "{":
                    stack[-1] += 1
                elif c == "}":
                    if stack[-1] == 0:
                        stack.pop(); state = "`"; out.append("}"); prev, last, word = last, "}", ""; i += 1; continue
                    stack[-1] -= 1
            if c == "(":
                parens.append((word in CONTROL_WORDS and kw_ok) or (word == "await" and prev_word == "for"))
            elif c == ")":
                ctrl_close = parens.pop() if parens else False
            if c not in " \t\r\n":
                prev, last = last, c
                if c.isalnum() or c in "_$":
                    if not word_open:
                        prev_word, kw_ok = word, prev != "."   # a new word: remember the one before it, and whether this one follows a dot
                    word = (word + c) if word_open else c
                    word_open = True
                else:
                    word, word_open = "", False
            else:
                word_open = False                # an identifier after a blank is a new word; the previous word stays until the `/` decision
            out.append(c); i += 1; continue
        if state == "line":
            if c in "\n\r":                          # JS ends a line comment at CR too
                state = "code"; out.append(c)
            else:
                out.append(" ")
            i += 1; continue
        if state == "block":
            if c == "*" and nxt == "/":
                state = "code"; out.append("  "); i += 2; continue
            out.append(c if c == "\n" else " "); i += 1; continue
        # string states (' " `)
        if c == "\\":
            if nxt == "\r" and src[i + 2:i + 3] == "\n":
                out.append("  \n"); i += 3; continue                       # CRLF line continuation inside a string
            out.append(" "); out.append("\n" if nxt == "\n" else " "); i += 2; continue
        if state == "`" and c == "$" and nxt == "{":
            stack.append(0); state = "code"; out.append("${"); prev, last, word = last, "{", ""; i += 2; continue
        if state in ("'", '"') and c in "\n\r":
            # a JS syntax error - a quote inside a regex literal that opened string state would carry it across lines and hide an agent()
            raise ValueError("newline inside a quoted string - an unclosed string, or a quote inside a regex literal (/…/); switch to new RegExp('…')")
        if c == state:
            state = "code"; out.append(c); last = c; i += 1; continue
        out.append(c if c == "\n" else " "); i += 1; continue
    return "".join(out), state


_DEPTH_CACHE = {}   # text -> (brace, paren, bracket) depth per prefix length. Recounting per character is quadratic: a 1500-key (about 30 KB) schema took 17 seconds


def _depth_table(text):
    t = _DEPTH_CACHE.get(text)
    if t is None:
        t = [(0, 0, 0)]
        b = p = k = 0
        for ch in text:
            if ch == "{": b += 1
            elif ch == "}": b -= 1
            elif ch == "(": p += 1
            elif ch == ")": p -= 1
            elif ch == "[": k += 1
            elif ch == "]": k -= 1
            t.append((b, p, k))
        _DEPTH_CACHE[text] = t
    return t


def _depths_at(text, idx):
    """(brace, paren, bracket) depth of text[:idx] - one table per text, then a lookup."""
    return _depth_table(text)[idx]


def _literal_value(code, raw, pos):
    """If the value starting at pos (after the colon of model:) is **a single string literal**, return it, else None.

    Only , } or the end may follow the literal - an expression like 'opus' ? 'haiku' : 'sonnet' is not a literal.
    Whitespace and comments before the value are skipped in the blanked code (comment = spaces), and the value is read
    from the original starting at the quote (effort: /*j*/ 'high' is a literal too).
    """
    q = pos
    while q < len(code) and code[q] in " \t\r\n":
        q += 1
    m = LITERAL.match(raw, q)
    if not m:
        return None
    tail = code[m.end():].lstrip()
    if tail and tail[0] not in ",}":
        return None
    return m.group(2)


def _after_separator(text, idx):
    """True when the first non-blank character before text[idx] is `{` or `,` - a key position. `c ? model : 'x'` is not one."""
    j = idx - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    return j >= 0 and text[j] in "{,"


def _opt_literal(code, script, start, end, key):
    """Value of the **top-level** `key:` (or 'key':) of opts inside the agent() parentheses (code[start:end]) -> (status, value), status in missing | expr | ok.

    Top level = brace depth 1, paren and bracket depth 0. The same key in nested objects or nested calls is ignored (= missing). If the key repeats, the last one wins, as in JS.
    A quoted key looks like `'     ':` (spaces as long as the key) in the blanked code, so the name is confirmed in the original.
    """
    args = code[start:end]
    bare = re.compile(r"(?<![\w.$])%s\s*:" % key)                 # $effort: and o.effort : are not this key
    quoted = re.compile(r"""(['"`])\s{%d}\1\s*:""" % len(key))
    shorthand = re.compile(r"(?<![\w.$])%s\s*[,}]" % key)          # {effort} = the variable effort passed under its own name
    ends = []
    for km in bare.finditer(args):
        if _depths_at(args, km.start()) == (1, 0, 0) and _after_separator(args, km.start()):
            ends.append(km.end())
    for qm in quoted.finditer(args):
        if (script[start + qm.start() + 1: start + qm.start() + 1 + len(key)] == key and _depths_at(args, qm.start()) == (1, 0, 0)
                and _after_separator(args, qm.start())):
            ends.append(qm.end())
    if not ends:
        for sm in shorthand.finditer(args):
            if _depths_at(args, sm.start()) == (1, 0, 0):
                return "shorthand", None
        return "missing", None
    val = _literal_value(code, script, start + max(ends))
    if val is None:
        return "expr", None
    return "ok", val


def _opts_shape(code, script, start, end):
    """If the opts inside the agent() parentheses is not **one flat object literal**, return the reason, else None.

    Every form where the runtime value could differ from the literal the hook read is rejected (fail-closed): more than one top-level object literal (c ? {…} : {…}),
    another expression before or after the object (c ? X : {…}, {…}.x), spread (...), computed keys (['effort']), get/set methods, identifier escapes (backslash).
    `${` (an expression in a prompt template literal) is not an object. If there is no object at all (opts omitted, a variable), it is left here and missing reports it.
    """
    args = code[start:end]
    opens = [i for i, ch in enumerate(args) if ch == "{" and not (i > 0 and args[i - 1] == "$") and _depths_at(args, i) == (0, 0, 0)]
    if not opens:
        return None
    if len(opens) > 1:
        return "%d object literals in the opts position (chosen by a ternary) - use only one" % len(opens)
    o = opens[0]
    depth = 0
    close = None
    for i in range(o, len(args)):
        if args[i] == "{":
            depth += 1
        elif args[i] == "}":
            depth -= 1
            if depth == 0:
                close = i
                break
    if close is None:
        return "the opts object is not closed"
    before = args[:o]
    commas = [i for i, ch in enumerate(before) if ch == "," and _depths_at(before, i) == (0, 0, 0)]
    if before[(commas[-1] + 1) if commas else 0:].strip():
        return "another expression before the opts object (c ? X : {…} form)"
    if args[close + 1:].strip().rstrip(",").strip():
        return "another expression after the opts object"
    body = args[o + 1:close]
    for i, ch in enumerate(body):
        if _depths_at(body, i) != (0, 0, 0):
            continue
        if ch == "\\":
            return "identifier escape (backslash) in a key"
        if ch == "." and body[i:i + 3] == "...":
            return "spread (...) lets a later value override the earlier literal"
        if ch == "[":
            prev = body[:i].rstrip()
            if not prev or prev[-1] in "{,":
                return "a computed key (['effort']) cannot be read"
    if re.search(r"(?<![\w.$])(get|set)\s+[\w$]+\s*\(", body):
        return "get/set methods cannot be read"
    raw_body = script[start + o + 1: start + close]
    for qm in QUOTED_KEY.finditer(body):
        if _depths_at(body, qm.start()) != (0, 0, 0):
            continue
        if "\\" in raw_body[qm.start() + 1: qm.start() + 1 + len(qm.group(2))]:
            return "an escape sequence inside a quoted key ('eff\\u006frt') cannot be read"
    return None


def agent_calls(script):
    """For each agent( call, {"line", "model", "status", "effort", "effort_status", "shape", "args"} - status in missing | shorthand | expr | ok.

    model and effort are read only from the **top-level** keys of opts (the second argument object) (_opt_literal). shape is the rejection reason from _opts_shape (None if none).
    """
    code, end_state = blank_strings_and_comments(script)
    if end_state != "code":
        raise ValueError("unclosed string or comment - if it is a regex literal (/[/*]/ and similar), switch to new RegExp('…')")
    dyn = DYNAMIC_ACCESS.search(code)
    if dyn:
        raise ValueError("a script that reaches agent through eval, Function, globalThis, or a computed property cannot be judged (script, line %d)"
                         % (code.count("\n", 0, dyn.start()) + 1))
    alias = AGENT_ALIAS.search(code)
    if alias:
        raise ValueError("agent is used somewhere other than a call (alias, argument, property) (script, line %d) - write agent(...) directly for each call"
                         % (code.count("\n", 0, alias.start()) + 1))
    calls = []
    for m in AGENT_CALL.finditer(code):
        start = m.end()           # right after '('
        depth = 1
        j = start
        while j < len(code) and depth:
            ch = code[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            j += 1
        end = j - 1 if depth == 0 else len(code)
        line = code.count("\n", 0, m.start()) + 1
        ms, mv = _opt_literal(code, script, start, end, "model")
        es, ev = _opt_literal(code, script, start, end, "effort")
        calls.append({"line": line, "model": mv, "status": ms, "effort": ev, "effort_status": es,
                      "shape": _opts_shape(code, script, start, end), "args": script[start:end]})
    return calls


def agent_calls_without_model(script):
    """(total call count, line numbers of calls without model) - kept for the old interface."""
    calls = agent_calls(script)
    return len(calls), [c["line"] for c in calls if c["status"] == "missing"]


# ----------------------------------------------------------------------------- decision

def classify(model):
    """Model value -> (tier, error). Full IDs (claude-haiku-4-5) are normalized to their family too."""
    v = str(model).strip().lower()
    if not v or v == "inherit":
        return None, "model '%s' is not allowed - to inherit, write the current model name as is" % v
    hits = [t for t in TIERS if t in v]
    if len(hits) > 1:
        return None, "model '%s' names more than one tier (%s) - use one" % (v, "/".join(hits))
    if hits:
        return hits[0], None
    return None, "model '%s' is not in the allow list (fable / opus / sonnet / haiku)" % v


def _lines(nums):
    return ", ".join(str(n) for n in nums)


def effort_cap():
    """effort cap. env SUBAGENT_EFFORT_CAP (a value in EFFORTS) if set, otherwise the default (xhigh)."""
    v = os.environ.get("SUBAGENT_EFFORT_CAP", "").strip().lower()
    return v if v in EFFORTS else EFFORT_CAP_DEFAULT


def haiku_role_mode():
    """True when env SUBAGENT_HAIKU_ROLE_MODE is "role" (default off = the mark rule only)."""
    return os.environ.get(HAIKU_ROLE_MODE_ENV, "").strip().lower() == "role"


def haiku_error(text, effort, where, fallback):
    """Rejection reason for a haiku call or None. text = where to look for marks (prompt, inside the parentheses), fallback = the mark-rule reason.

    Role mode = both a `[haiku role: …]` and a `[verify: …]` mark, and effort within HAIKU_EFFORT_MIN..HAIKU_EFFORT_MAX. The approval mark passes in either mode.
    """
    text = text or ""
    if haiku_mark() in text:
        return None
    if not haiku_role_mode():
        if HAIKU_ROLE_RE.search(text) or HAIKU_VERIFY_RE.search(text):
            return fallback + " (role marks found, but role mode is off - set env %s=role in settings.json to use them)" % HAIKU_ROLE_MODE_ENV
        return fallback
    missing = []
    if not HAIKU_ROLE_RE.search(text):
        missing.append("a '[haiku role: <mechanical job - lint cleanup, result aggregation, grep summary, md5 comparison>]' mark")
    if not HAIKU_VERIFY_RE.search(text):
        missing.append("a '[verify: <mechanical check the parent will run - lint rerun, grep 0 hits, diff>]' mark")
    if missing:
        return (where + ": haiku is only for roles with a mechanical check. Put " + " and ".join(missing)
                + " inside that call; otherwise use sonnet")
    v = str(effort or "").strip().lower()
    if v in EFFORTS and EFFORTS.index(v) < EFFORTS.index(HAIKU_EFFORT_MIN):
        return where + ": a haiku role needs effort medium or higher - use 'medium' instead of '%s'" % v
    if v in EFFORTS and EFFORTS.index(v) > EFFORTS.index(HAIKU_EFFORT_MAX):
        return (where + ": effort '%s' does not fit a haiku role - haiku goes up to medium/high; a job that needs more thinking belongs to sonnet/opus"
                % v)
    return None


def effort_error(effort, text, where):
    """Rejection reason for an effort value (Agent tool_input.effort, agent() effort: literal) or None. text = where to look for the mark (prompt, inside the parentheses)."""
    if effort is None or str(effort).strip() == "":
        return (where + " has no effort. The session effort would be inherited as is; call again with effort set "
                "(work agent high, mechanical collection low, judge/critic xhigh)")
    v = str(effort).strip()
    if v not in EFFORTS:
        if v.lower() in EFFORTS:
            return where + ": write effort '%s' in lowercase ('%s') - the runtime may not accept uppercase, so it is not sent as is" % (v, v.lower())
        return where + ": effort '%s' is not in the allow list (%s)" % (v, "/".join(EFFORTS))
    cap = effort_cap()
    if EFFORTS.index(v) > EFFORTS.index(cap) and effort_mark(v) not in (text or ""):
        return (where + ": effort '%s' is above the cap '%s'. If it carries %s approval for that task, put a '%s' mark inside that call; otherwise use '%s' or lower. "
                "(set the cap with env SUBAGENT_EFFORT_CAP)" % (v, cap, owner(), effort_mark(v), cap))
    return None


def decide(payload):
    """Rejection reason string or None."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool in ("Agent", "Task"):
        model = tool_input.get("model")
        if not model:
            return tool + " call has no model. The parent model would be inherited as is; call again with model set. " + GUIDE
        tier, err = classify(model)
        if err:
            return tool + ": " + err + ". " + GUIDE
        if tier == "haiku":
            err = haiku_error(tool_input.get("prompt"), tool_input.get("effort"), tool + " call",
                              tool + " needs %s approval for that task to use haiku. If approved, put a %s mark in prompt; otherwise use sonnet."
                              % (owner(), haiku_mark()))
            if err:
                return err + " " + GUIDE
        if tier == "fable" and fable_mark() not in (tool_input.get("prompt") or ""):
            return (tool + " needs %s approval for that task to use fable. If approved, put a %s mark in prompt; otherwise use opus. "
                    % (owner(), fable_mark()) + GUIDE)
        err = effort_error(tool_input.get("effort"), tool_input.get("prompt") or "", tool + " call")
        if err:
            return err + " " + GUIDE
        return None

    if tool == "Workflow":
        script, source, err = read_script(tool_input, payload.get("cwd"))
        if err:
            return "Workflow " + err + ". Check the path and call again. " + GUIDE
        if not script:
            return None  # built-in workflows etc. - nothing to check
        try:
            calls = agent_calls(script)
        except ValueError as e:
            return "Workflow script cannot be parsed (%s): %s. " % (source, e) + GUIDE
        shapes = [(c["line"], c["shape"]) for c in calls if c["shape"]]
        if shapes:
            return ("Workflow agent() opts is not one flat object literal (line %s: %s) - blocked because the runtime value could differ from what the hook read. "
                    "Write {model: 'opus', effort: 'high', …} directly in that spot. " % (_lines([l for l, _ in shapes]), shapes[0][1]) + GUIDE)
        short = [c["line"] for c in calls if c["status"] == "shorthand" or c["effort_status"] == "shorthand"]
        if short:
            return ("Workflow agent() model or effort is shorthand ({effort}) (line %s) - it is a variable and cannot be read; write a literal like 'high'. "
                    % _lines(short) + GUIDE)
        missing = [c["line"] for c in calls if c["status"] == "missing"]
        if missing:
            return ("Workflow agent(): of %d calls, %d have no model (%s, line %s). Put model at the top level of opts and run again "
                    "(model in meta.phases or inside a nested object does not count). " % (len(calls), len(missing), source, _lines(missing)) + GUIDE)
        expr = [c["line"] for c in calls if c["status"] == "expr"]
        if expr:
            return ("Workflow agent() model is not a string literal (line %s) - write 'opus' / 'sonnet' directly, "
                    "not a variable, ternary, or parenthesized expression. " % _lines(expr) + GUIDE)
        tiers = {}
        for c in calls:
            tier, err = classify(c["model"])
            if err:
                return "Workflow agent() line %d: %s. " % (c["line"], err) + GUIDE
            tiers.setdefault(tier, []).append(c)
        fable_unmarked = [c["line"] for c in tiers.get("fable", []) if fable_mark() not in c["args"]]
        if fable_unmarked:
            return ("Workflow fable agent() (line %s) is allowed only with %s approval for that task. If approved, put a %s mark "
                    "inside that agent() call's parentheses (label or prompt); otherwise use opus. " % (_lines(fable_unmarked), owner(), fable_mark()) + GUIDE)
        fable = [c["line"] for c in tiers.get("fable", [])]
        if len(fable) > FABLE_MAX:
            return ("Workflow has fable agent() at %d sites (line %s) - fable is for judge/critic seats, up to %d sites; use opus for the rest. "
                    % (len(fable), _lines(fable), FABLE_MAX) + GUIDE)
        for c in tiers.get("haiku", []):
            where = "Workflow haiku agent() (line %d)" % c["line"]
            err = haiku_error(c["args"], c["effort"], where,
                              where + " is allowed only with %s approval for that task. If approved, put a %s mark "
                              "inside that agent() call's parentheses (label or prompt); otherwise use sonnet." % (owner(), haiku_mark()))
            if err:
                return err + " " + GUIDE
        emissing = [c["line"] for c in calls if c["effort_status"] == "missing"]
        if emissing:
            return ("Workflow agent(): of %d calls, %d have no effort (%s, line %s). The session effort is inherited; put effort at the top level of opts "
                    "(work high, mechanical collection low, judge/critic xhigh). " % (len(calls), len(emissing), source, _lines(emissing)) + GUIDE)
        eexpr = [c["line"] for c in calls if c["effort_status"] == "expr"]
        if eexpr:
            return ("Workflow agent() effort is not a string literal (line %s) - write it directly like 'high', not a variable or ternary. "
                    % _lines(eexpr) + GUIDE)
        for c in calls:
            err = effort_error(c["effort"], c["args"], "Workflow agent() line %d" % c["line"])
            if err:
                return err + " " + GUIDE
        return None

    return None


# ----------------------------------------------------------------------------- self-test

def _self_test():
    import tempfile

    def wf(script):
        return {"tool_name": "Workflow", "tool_input": {"script": script}}

    cases = [
        # --- 2026-09-24 regressions (16)
        ("Agent without model → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "effort": "high"}}, True, "no model"),
        ("Agent with model (opus) → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "high"}}, False, None),
        ("Task (old name) without model → deny",
         {"tool_name": "Task", "tool_input": {"prompt": "x", "effort": "high"}}, True, "Task call has no model"),
        ("Task with model → pass",
         {"tool_name": "Task", "tool_input": {"prompt": "x", "model": "sonnet", "effort": "low"}}, False, None),
        ("Workflow with model on both calls → pass",
         wf("const a = await agent('p', {model: 'opus', effort: 'high'})\nconst b = await agent('q', { label: 'x', model: 'sonnet', effort: 'low' })"),
         False, None),
        ("Workflow agent alias assignment → deny",
         wf("const run = agent\nconst r = await run('p', {model: 'opus', effort: 'high'})"),
         True, "alias"),
        ("Workflow agent passed as an argument → deny",
         wf("const r = await helper(agent, ITEMS)"),
         True, "alias"),
        ("Workflow x.agent( also gets the model check → deny",
         wf("const r = await ctx.agent('p', { effort: 'high' })"),
         True, "no model"),
        ("Workflow globalThis access → deny",
         wf("const a = globalThis['ag' + 'ent']\nreturn 1"),
         True, "cannot be judged"),
        ("Workflow eval → deny",
         wf("eval('agent')\nreturn 1"),
         True, "cannot be judged"),
        ("Workflow agentType key is not an alias → pass",
         wf("const r = await agent('p', { agentType: 'review-readonly', model: 'opus', effort: 'high' })"),
         False, None),
        ("Workflow one of two missing → deny (line 2)",
         wf("const a = await agent('p', {model: 'opus', effort: 'high'})\nconst b = await agent('q', { label: 'x', effort: 'high' })"),
         True, "have no model (script, line 2)"),
        ("model only in meta.phases, not on agent → deny (old method miss regression)",
         wf("export const meta = { name: 'x', phases: [{ title: 'a', model: 'opus' }] }\nconst r = await agent('do', { label: 'one', effort: 'high' })\nreturn r"),
         True, "have no model (script, line 2)"),
        ("text 'agent() call' inside a prompt string is not counted → pass (old method false positive regression)",
         wf("const P = `of the 2 agent() calls in this script, model set...`\nconst r = await agent(P, {model: 'opus', effort: 'high'})"),
         False, None),
        ("agent( only inside comments → pass (0 calls)",
         wf("// agent('x')\n/* agent('y') */\nreturn 1"), False, None),
        ("call inside template ${} is checked too → deny",
         wf("const s = `v=${await agent('p', {label: 'l', effort: 'high'})}`"), True, "have no model (script, line 1)"),
        ("call inside template ${} with model → pass",
         wf("const s = `v=${await agent('p', {label: 'l', model: 'opus', effort: 'medium'})}`"), False, None),
        ("multi-line opts → pass",
         wf("const r = await agent(\n  'p',\n  {\n    label: 'x',\n    schema: S,\n    model: 'opus',\n    effort: 'high',\n  },\n)"), False, None),
        ("call missing inside a pipeline callback → deny (line 3)",
         wf("const r = await pipeline(\n  ITEMS,\n  it => agent(it.p, { label: it.k, schema: S, effort: 'high' }),\n  (a, it) => agent('v', { model: 'opus', effort: 'high' }),\n)"),
         True, "have no model (script, line 3)"),
        ("agentType: key is not seen as a call → pass",
         wf("const r = await agent('p', { agentType: 'general-purpose', model: 'sonnet', effort: 'low' })"), False, None),
        ("saved workflow name (built in, no source) → pass",
         {"tool_name": "Workflow", "tool_input": {"name": "review-changes-builtin-nonexistent-zz"}}, False, None),
        ("quoted key {'model': 'opus'} → pass",
         wf("const r = await agent('p', { 'model': 'opus', label: 'x', 'effort': 'high' })"), False, None),
        # --- 2026-10-11 fable cap, haiku mark
        ("Workflow fable marks at 2 sites → pass (within cap)",
         wf("const a = await agent('p', {label: '[owner-approved: fable] judge', model: 'fable', effort: 'xhigh'})\nconst b = await agent('q', {label: '[owner-approved: fable] critic', model: 'fable', effort: 'xhigh'})\nconst c = await agent('r', {model: 'opus', effort: 'high'})"),
         False, None),
        ("Workflow fable marks at 3 sites → deny (line 1, 2, 3)",
         wf("const a = await agent('p', {label: '[owner-approved: fable]', model: 'fable', effort: 'high'})\nconst b = await agent('q', {label: '[owner-approved: fable]', model: \"fable\", effort: 'high'})\nconst c = await agent('r', { label: '[owner-approved: fable] x', model: 'fable', effort: 'high' })"),
         True, "fable agent() at 3 sites (line 1, 2, 3)"),
        # --- 2026-10-13 fable mark
        ("Agent fable without mark → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "fable", "effort": "high"}}, True, "to use fable"),
        ("Agent fable with mark → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: fable] subtle design judgment", "model": "fable", "effort": "xhigh"}}, False, None),
        ("Workflow fable without mark → deny (regression: an unmarked fable call in a parallel lane)",
         wf("const r = await parallel(G.map(g => () => agent(g.p, { label: 'verify:' + g.key, model: 'fable', effort: 'high' })))"), True, "fable agent() (line 1) is allowed only"),
        ("Workflow fable mark only in a comment outside the call → deny",
         wf("// [owner-approved: fable]\nconst a = await agent('p', {model: 'fable', effort: 'high'})"), True, "fable agent() (line 2) is allowed only"),
        ("Workflow fable mark at 1 site + opus → pass",
         wf("const a = await agent('p', {label: '[owner-approved: fable] judge', model: 'fable', effort: 'xhigh'})\nconst b = await agent('q', {model: 'opus', effort: 'high'})"), False, None),
        ("Workflow haiku without mark → deny",
         wf("const a = await agent('p', {model: 'haiku', effort: 'low'})"), True, "haiku agent() (line 1) is allowed only"),
        ("Workflow haiku mark inside that call (label) → pass",
         wf("const a = await agent('p', {label: '[owner-approved: haiku] parsing', model: 'haiku', effort: 'low'})"), False, None),
        ("Workflow haiku mark only in a comment outside the call → deny (narrowed scope)",
         wf("// [owner-approved: haiku]\nconst a = await agent('p', {model: 'haiku', effort: 'low'})"), True, "haiku agent() (line 2) is allowed only"),
        ("Workflow full ID claude-haiku-4-5 without mark → deny",
         wf("const a = await agent('p', {model: 'claude-haiku-4-5', effort: 'low'})"), True, "haiku agent() (line 1) is allowed only"),
        ("Workflow full ID claude-opus-5-5 → pass",
         wf("const a = await agent('p', {model: 'claude-opus-5-5', effort: 'high'})"), False, None),
        # --- 2026-10-11 external review findings: where values are read, nesting, non-literals, inherit, parse failure
        ("text model: 'opus' inside a prompt string does not fool it → deny (actually haiku)",
         wf("const a = await agent(\"use model: 'opus'\", {model: 'haiku', effort: 'low'})"), True, "haiku agent() (line 1) is allowed only"),
        ("only nested object metadata.model → deny (missing)",
         wf("const a = await agent('p', {metadata: {model: 'opus'}, label: 'x', effort: 'high'})"), True, "have no model (script, line 1)"),
        ("model of a nested agent() does not hide the outer one missing → deny (line 1)",
         wf("const a = await agent(await agent('inner', {model: 'opus', effort: 'high'}), {label: 'outer', effort: 'high'})"), True, "have no model (script, line 1)"),
        ("model is a variable → deny (not a literal)",
         wf("const M = pick()\nconst a = await agent('p', {model: M, effort: 'high'})"), True, "model is not a string literal"),
        ("model is a ternary 'opus' ? 'haiku' : 'sonnet' → deny",
         wf("const a = await agent('p', {model: 'opus' ? 'haiku' : 'sonnet', effort: 'high'})"), True, "model is not a string literal"),
        ("model is a parenthesized expression ('haiku') → deny",
         wf("const a = await agent('p', {model: ('haiku'), effort: 'high'})"), True, "model is not a string literal"),
        ("Workflow model: 'inherit' → deny",
         wf("const a = await agent('p', {model: 'inherit', effort: 'high'})"), True, "is not allowed - to inherit"),
        ("Agent model inherit → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "inherit", "effort": "high"}}, True, "is not allowed - to inherit"),
        ("Agent model name outside the allow list → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "gpt-6", "effort": "high"}}, True, "not in the allow list (fable / opus / sonnet / haiku)"),
        ("Agent haiku without mark → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "haiku", "effort": "low"}}, True, "to use haiku"),
        ("Agent haiku with mark → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: haiku] log parsing", "model": "haiku", "effort": "low"}}, False, None),
        ("regex literal /[/*]/ → deny (fail-closed, reported as a regex literal)",
         wf("const r = /[/*]/\nconst a = await agent('p', {model: 'haiku', effort: 'low'})"), True, "regex literal"),
        # --- 2026-10-13 evening effort
        ("Agent without effort → deny (inherits the session effort)",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus"}}, True, "no effort"),
        ("Agent effort value outside the allow list → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "ultra"}}, True, "effort 'ultra' is not in the allow list"),
        ("Agent effort max without mark → deny (cap xhigh)",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "max"}}, True, "above the cap"),
        ("Agent effort max with mark → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: max] final judgment", "model": "opus", "effort": "max"}}, False, None),
        ("Agent effort xhigh (within the default cap) → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "xhigh"}}, False, None),
        ("Workflow effort missing on one of two → deny (line 2)",
         wf("const a = await agent('p', {model: 'opus', effort: 'high'})\nconst b = await agent('q', {model: 'opus'})"), True, "have no effort (script, line 2)"),
        ("Workflow effort variable → deny (not a literal)",
         wf("const E = 'high'\nconst a = await agent('p', {model: 'opus', effort: E})"), True, "effort is not a string literal"),
        ("Workflow effort ternary → deny",
         wf("const a = await agent('p', {model: 'opus', effort: big ? 'xhigh' : 'low'})"), True, "effort is not a string literal"),
        ("Workflow effort max mark inside that call → pass",
         wf("const a = await agent('p', {label: '[owner-approved: max] judge', model: 'opus', effort: 'max'})"), False, None),
        ("Workflow effort max mark only in a comment outside the call → deny",
         wf("// [owner-approved: max]\nconst a = await agent('p', {model: 'opus', effort: 'max'})"), True, "above the cap"),
        ("Workflow effort only inside a nested object → deny (missing)",
         wf("const a = await agent('p', {model: 'opus', metadata: {effort: 'high'}})"), True, "no effort"),
        ("text effort: 'low' inside a prompt string does not fool it → deny (actually omitted)",
         wf("const a = await agent(\"use effort: 'low'\", {model: 'opus'})"), True, "no effort"),
        # --- 2026-10-13 evening refutation review: opts shape, key boundaries, comment before value, shorthand, lowercase
        ("opts ternary (two objects) → shape deny (bypass where the first branch max passed without a mark)",
         wf("const a = await agent('p', big ? {model: 'opus', effort: 'max'} : {model: 'opus', effort: 'high'})"), True, "2 object literals"),
        ("opts ternary (variable : object) → shape deny",
         wf("const a = await agent('p', big ? OPTS : {model: 'opus', effort: 'high'})"), True, "expression before the opts object"),
        ("opts spread → shape deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'high', ...(c ? {effort: 'max'} : {})})"), True, "spread"),
        ("opts computed key ['effort'] → shape deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'high', ['effort']: 'max'})"), True, "computed key"),
        ("opts getter → shape deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'high', get effort() { return 'max' }})"), True, "get/set"),
        ("opts followed by .x → shape deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'high'}.x)"), True, "expression after the opts object"),
        ("$effort: is not the key → no effort deny",
         wf("const a = await agent('p', {model: 'opus', $effort: 'high'})"), True, "no effort"),
        ("o.effort : inside a label value is not the key → pass",
         wf("const a = await agent('p', {model: 'opus', effort: 'high', label: big ? cfg.effort : 'judge'})"), False, None),
        ("shorthand {effort} → deny, reported as a variable",
         wf("const effort = 'high'\nconst a = await agent('p', {model: 'opus', effort})"), True, "shorthand"),
        ("comment before the value effort: /*j*/ 'high' → pass",
         wf("const a = await agent('p', {model: 'opus', effort: /*judge*/ 'high'})"), False, None),
        ("effort 'High' (uppercase) → deny, asks for lowercase",
         wf("const a = await agent('p', {model: 'opus', effort: 'High'})"), True, "in lowercase"),
        ("${} in the prompt template, array values, and trailing commas are not shape violations → pass",
         wf("const a = await agent(`k=${key} v=${f(x)}`, {model: 'opus', effort: 'high', files: [a, b], schema: {type: 'object'},},)"), False, None),
        ("Agent effort 'XHIGH' (uppercase) → deny, asks for lowercase",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "XHIGH"}}, True, "in lowercase"),
    ]
    # lowering the cap through env makes levels above it deny without a mark (put it in settings.json env to cover every session)
    cap_cases = [
        ("SUBAGENT_EFFORT_CAP=high: Agent xhigh without mark → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "xhigh"}}, True, "the cap 'high'"),
        ("SUBAGENT_EFFORT_CAP=high: Agent xhigh with mark → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: xhigh] judge", "model": "opus", "effort": "xhigh"}}, False, None),
        ("SUBAGENT_EFFORT_CAP=high: Workflow high → pass",
         wf("const a = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("SUBAGENT_EFFORT_CAP=bogus: falls back to the default cap (xhigh) → max deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "max"}}, True, "the cap 'xhigh'"),
        ("SUBAGENT_EFFORT_CAP=high: Workflow xhigh without mark → deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'xhigh'})"), True, "the cap 'high'"),
        ("SUBAGENT_EFFORT_CAP=high: Workflow xhigh mark inside that call → pass",
         wf("const a = await agent('p', {label: '[owner-approved: xhigh] judge', model: 'opus', effort: 'xhigh'})"), False, None),
        ("SUBAGENT_EFFORT_CAP=max: Agent max without mark → pass (cap is max)",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "max"}}, False, None),
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write("const a = await agent('p', { label: 'x', effort: 'high' })\n")
        tmp = fh.name
    cases.append(("scriptPath missing model → deny (scriptPath)",
                  {"tool_name": "Workflow", "tool_input": {"scriptPath": tmp}}, True, "scriptPath"))
    cases.append(("scriptPath unreadable → deny (fail-closed)",
                  {"tool_name": "Workflow", "tool_input": {"scriptPath": tmp + ".nope"}}, True, "cannot read"))
    for token in ("fable", "opus", "sonnet", "haiku", "effort", "omit", "/usage", "role", OWNER_ENV, HAIKU_ROLE_MODE_ENV):
        if token not in GUIDE:
            print("FAIL - GUIDE text is missing %r" % token)
            sys.exit(1)
    print("PASS - GUIDE text has all 10 tokens (fable/opus/sonnet/haiku/effort/omit//usage/role/owner env/role env), length %d chars" % len(GUIDE))
    failed = 0

    def run(name, payload, expect_deny, must_contain):
        if must_contain and must_contain in GUIDE:   # GUIDE is appended to every deny reason, so text found there cannot tell which gate blocked
            print("FAIL -", name, "| must_contain %r is inside GUIDE (use reason-only text)" % must_contain)
            return 1
        reason = decide(payload)
        ok = (reason is not None) == expect_deny
        if ok and must_contain and must_contain not in (reason or ""):
            ok = False
        print(("PASS" if ok else "FAIL"), "-", name, ("" if ok else " | reason=%r" % reason))
        return 0 if ok else 1

    # haiku role mode (env SUBAGENT_HAIKU_ROLE_MODE=role) - the case name head is the env value
    R = "[haiku role: lint queue cleanup] [verify: memory-lint rerun, 0 findings] "
    role_cases = [
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Agent haiku role+verify, effort low → deny (medium or higher)",
         {"tool_name": "Agent", "tool_input": {"prompt": R + "fix the descriptions", "model": "haiku", "effort": "low"}}, True, "medium or higher"),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Agent haiku role+verify, effort medium → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": R + "fix the descriptions", "model": "haiku", "effort": "medium"}}, False, None),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Agent haiku role+verify, effort high → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": R + "fix the descriptions", "model": "haiku", "effort": "high"}}, False, None),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Agent haiku role mark only → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "[haiku role: lint] x", "model": "haiku", "effort": "medium"}}, True, "roles with a mechanical check"),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Agent haiku verify mark only → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "[verify: lint 0 hits] x", "model": "haiku", "effort": "medium"}}, True, "roles with a mechanical check"),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Agent haiku empty role mark → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "[haiku role: ] [verify: lint 0 hits] x", "model": "haiku", "effort": "medium"}}, True, "roles with a mechanical check"),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Agent haiku role+verify, effort xhigh → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": R + "x", "model": "haiku", "effort": "xhigh"}}, True, "up to medium/high"),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Agent haiku approval mark only → pass (the mark rule still holds)",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: haiku] x", "model": "haiku", "effort": "high"}}, False, None),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Workflow haiku role+verify in label, effort medium → pass",
         wf("const a = await agent('p', {label: '" + R + "aggregate', model: 'haiku', effort: 'medium'})"), False, None),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Workflow haiku role+verify, effort low → deny (medium or higher)",
         wf("const a = await agent('p', {label: '" + R + "aggregate', model: 'haiku', effort: 'low'})"), True, "medium or higher"),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Workflow haiku marks only in a comment outside the call → deny",
         wf("// " + R + "\nconst a = await agent('p', {model: 'haiku', effort: 'medium'})"), True, "roles with a mechanical check"),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Workflow haiku role+verify, effort xhigh → deny",
         wf("const a = await agent('p', {label: '" + R + "aggregate', model: 'haiku', effort: 'xhigh'})"), True, "up to medium/high"),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: fable without mark is still denied",
         {"tool_name": "Agent", "tool_input": {"prompt": R + "x", "model": "fable", "effort": "high"}}, True, "to use fable"),
        ("SUBAGENT_HAIKU_ROLE_MODE=off: Agent haiku role+verify marks only → deny (the mark rule)",
         {"tool_name": "Agent", "tool_input": {"prompt": R + "x", "model": "haiku", "effort": "medium"}}, True, "to use haiku"),
        ("SUBAGENT_HAIKU_ROLE_MODE=bogus: role marks only → deny (treated as off)",
         wf("const a = await agent('p', {label: '" + R + "aggregate', model: 'haiku', effort: 'medium'})"), True, "haiku agent() (line 1) is allowed only"),
        ("SUBAGENT_HAIKU_ROLE_MODE=role: Agent haiku role+verify, effort 'Low' (uppercase) → deny on the range, not only on the case",
         {"tool_name": "Agent", "tool_input": {"prompt": R + "x", "model": "haiku", "effort": "Low"}}, True, "medium or higher"),
        ("SUBAGENT_HAIKU_ROLE_MODE=off: role marks present → the deny names the env that turns role mode on",
         {"tool_name": "Agent", "tool_input": {"prompt": "[haiku role: lint] [verify: lint 0 hits] x", "model": "haiku", "effort": "medium"}}, True, "role mode is off"),
    ]
    # owner name env SUBAGENT_GUARD_OWNER - the marks change with it
    owner_cases = [
        ("SUBAGENT_GUARD_OWNER=alice: Agent haiku '[alice-approved: haiku]' → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "[alice-approved: haiku] log parsing", "model": "haiku", "effort": "low"}}, False, None),
        ("SUBAGENT_GUARD_OWNER=alice: Agent haiku '[owner-approved: haiku]' → deny (name mismatch)",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: haiku] log parsing", "model": "haiku", "effort": "low"}}, True, "needs alice approval"),
        ("SUBAGENT_GUARD_OWNER=alice: Workflow fable '[alice-approved: fable]' → pass",
         wf("const a = await agent('p', {label: '[alice-approved: fable] judge', model: 'fable', effort: 'high'})"), False, None),
        ("SUBAGENT_GUARD_OWNER=alice: Agent effort max '[alice-approved: max]' → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "[alice-approved: max] final", "model": "opus", "effort": "max"}}, False, None),
        ("SUBAGENT_GUARD_OWNER=: empty means the default → '[owner-approved: haiku]' passes",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: haiku] log parsing", "model": "haiku", "effort": "low"}}, False, None),
        ("SUBAGENT_GUARD_OWNER=<your name>: a placeholder means unset → '[owner-approved: haiku]' passes",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: haiku] log parsing", "model": "haiku", "effort": "low"}}, False, None),
    ]
    # review findings: regex literals that pair their quotes, division, more than one tier name, marks inside comments
    review_cases = [
        ("agent() between two regex literals with quotes is blanked → deny",
         wf("const a = s.replace(/\"/g, '')\nconst r = await agent('p', {model: 'haiku', effort: 'max'})\nconst b = t.replace(/\"/g, '')"), True, "cannot be parsed"),
        ("division a / b is not a regex → pass",
         wf("const x = (a + 1) / 2\nconst y = n / m\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("model with two tier names (opus-haiku) → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus-haiku", "effort": "high"}}, True, "more than one tier"),
        ("agent() between two return /'/ regexes → deny",
         wf("const f = s => { return /'/.test(s) }\nconst r = await agent('p', {model: 'haiku', effort: 'max'})\nconst g = s => { return /'/.test(s) }"), True, "cannot be parsed"),
        ("regex at the head of a template expression ${/'/…} → deny",
         wf("const t = `${/'/.source}`\nconst r = await agent('p', {model: 'haiku', effort: 'max'})\nconst u = `${/'/.source}`"), True, "cannot be parsed"),
        ("i++ / 2 and n-- / k are division → pass",
         wf("let i = 1, n = 4\nconst y = i++ / 2\nconst z = n-- / k\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("a mark inside a comment within the parentheses counts → pass",
         wf("const r = await agent('p', { /* [owner-approved: fable] judge */ model: 'fable', effort: 'high' })"), False, None),
        ("a / after an identifier is division (not a keyword) → pass",
         wf("const avg = total / count\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("a quoted string broken across lines → deny (syntax error, fail-closed)",
         wf("const s = 'abc\ndef'\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), True, "cannot be parsed"),
        # review round 1: ternary and escaped keys, regex after `)` or `...`, CRLF continuation
        ("`c ? model : 'opus'` inside a label is not the model key → the real haiku is seen → deny",
         wf("const model = 1\nconst a = await agent('p', {model: 'haiku', effort: 'high', label: c ? model : 'opus'})"), True, "is allowed only"),
        ("`c ? 'effort' : 'high'` inside a label is not the effort key → the real max is seen → deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'max', label: c ? 'effort' : 'high'})"), True, "above the cap"),
        ("a unicode escape inside a quoted key ('eff\\u006frt' overrides effort at runtime) → shape deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'high', 'eff\\u006frt': 'max'})"), True, "escape sequence"),
        ("a regex after an if header `)` whose quote pairs with a later regex on one line → deny (regex literal)",
         wf("if (a) /'/.test(s); const r = await agent('p', {model: 'haiku', effort: 'max'}); if (b) /'/.test(t)"), True, "regex literal"),
        ("a regex after a spread (...) whose quote pairs with a later regex → deny (regex literal)",
         wf("const xs = [.../'/.source]; const r = await agent('p', {model: 'haiku', effort: 'max'}); const ys = [.../'/.source]"), True, "regex literal"),
        ("a regex after an if header that ends in // (opens a line comment that hides the rest of the line) → deny",
         wf("if (x) /a//1; const r = await agent('p', {model: 'haiku', effort: 'max'})\nreturn r"), True, "regex literal"),
        ("a regex after an if header that ends in /* (opens a block comment across lines) → deny",
         wf("if (x) /a/*2\nconst r = await agent('p', {model: 'haiku', effort: 'max'})\nif (y) /b*/.test(t)"), True, "regex literal"),
        ("a regex after a while header with nested parens → deny",
         wf("while (f(g(x))) /y/.test(z)\nconst r = await agent('p', {model: 'haiku', effort: 'max'})"), True, "regex literal"),
        ("division after a call's `)` → pass (Math.round(budget.remaining()/1000))",
         wf("const FLEET = Math.round(budget.remaining()/1000)\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("division after a parenthesized expression and after `]` → pass",
         wf("const h = (a + b) / 2\nconst k = xs[0] / 3\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("division, a string and a trailing comment on one line → pass",
         wf("const pct = done / total + '%' // progress\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("two divisions around a string → pass",
         wf("const t = a / b + ' of ' + c / d\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("a line comment ended by a bare CR → the agent() after it is seen → deny (haiku)",
         wf("// note\rconst r = await agent('p', {model: 'haiku', effort: 'max'})\n"), True, "is allowed only"),
        ("a unicode line separator (U+2028) after a line comment → deny",
         wf("// note const r = await agent('p', {model: 'haiku', effort: 'max'})\nreturn r"), True, "line separator"),
        ("a quoted key with a backslash-newline line continuation (effort at runtime) → shape deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'high', 'eff\\\nort': 'max'})"), True, "escape sequence"),
        ("an HTML-style comment opener in code position → deny",
         wf("<!-- /*\nconst r = await agent('p', {model: 'haiku', effort: 'max'})\n*/ return r"), True, "HTML-style"),
        # review round 3: contexts the paren stack missed, and the safety net that makes a misread context harmless
        ("a regex after a for-await header → deny (regex literal)",
         wf("for await (const x of xs) /'/.test(x); const r = await agent('p', {model: 'haiku', effort: 'max'}); for await (const y of ys) /'/.test(y)"), True, "regex literal"),
        ("a regex after extends → deny (regex literal)",
         wf("class A extends /'/.constructor {}; const r = await agent('p', {model: 'haiku', effort: 'max'}); class B extends /'/.constructor {}"), True, "regex literal"),
        ("a regex after a unary plus (1 + +/'/) → caught by the safety net (unpaired quote before the next slash)",
         wf("const n = 1 + +/'/.source.length; const r = await agent('p', {model: 'haiku', effort: 'max'}); const m = 1 + +/'/.source.length"), True, "unpaired quote"),
        ("a misread regex that ends in // (safety net: comment opener glued to a word) → deny",
         wf("const n = 1 + +/a//1; const r = await agent('p', {model: 'haiku', effort: 'max'})\nreturn r"), True, "glued to a word"),
        ("--> after a block comment that spans lines → deny (HTML-style)",
         wf("const a = 1 /*\n*/ --> `\nconst r = await agent('p', {model: 'haiku', effort: 'max'})\n`"), True, "HTML-style"),
        ("property names are not keywords: cfg.default / 2 and arr.with(0, 1) / 2 → pass",
         wf("const h = cfg.default / 2\nconst k = arr.with(0, 1) / 2\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("division followed by a spaced trailing comment → pass",
         wf("const half = n / 2 // half of n\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("division followed by a comment glued to a digit (x/2//c) → deny (put a space before the comment)",
         wf("const half = x/2//c\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), True, "glued to a word"),
        ("division, an unpaired quote and a second slash on one line → deny (split the line)",
         wf("const n = total / 2; const u = 'a/b'\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), True, "unpaired quote"),
        ("division followed by a string without a slash → pass",
         wf("const t = total / count + ' items'\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("CRLF line continuation inside a string → pass",
         wf("const s = 'ab\\\r\ncd'\r\nconst r = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("a regex after `)` with escaped slashes opens a line comment that hides the rest of the line → deny (backslash in code)",
         wf("if (x) /a\\/\\//.test(s); const r = await agent('p', {model: 'haiku', effort: 'max'})"), True, "cannot be parsed"),
        ("an identifier escape in a bare key (\\u006dodel) → deny (backslash in code)",
         wf("const a = await agent('p', {\\u006dodel: 'haiku', effort: 'max'})"), True, "cannot be parsed"),
    ]
    saved_cap = os.environ.pop("SUBAGENT_EFFORT_CAP", None)   # run with the default cap (the caller's values are restored at the very end)
    saved_role = os.environ.pop(HAIKU_ROLE_MODE_ENV, None)    # settings.json env may reach the shell too - run with role mode off
    saved_owner = os.environ.pop(OWNER_ENV, None)
    for name, payload, expect_deny, must_contain in cases:
        failed += run(name, payload, expect_deny, must_contain)
    for name, payload, expect_deny, must_contain in cap_cases:
        os.environ["SUBAGENT_EFFORT_CAP"] = name.split(":")[0].split("=")[1]   # the case name head (SUBAGENT_EFFORT_CAP=<value>) is the env value
        failed += run(name, payload, expect_deny, must_contain)
    os.environ.pop("SUBAGENT_EFFORT_CAP", None)                 # the role and owner cases run with the default cap too
    for name, payload, expect_deny, must_contain in role_cases:
        os.environ[HAIKU_ROLE_MODE_ENV] = name.split(":")[0].split("=")[1]
        failed += run(name, payload, expect_deny, must_contain)
    os.environ.pop(HAIKU_ROLE_MODE_ENV, None)
    for name, payload, expect_deny, must_contain in owner_cases:   # role mode off: the mark rule only
        os.environ[OWNER_ENV] = name.split(":")[0].split("=", 1)[1]
        failed += run(name, payload, expect_deny, must_contain)
    os.environ.pop(OWNER_ENV, None)
    for name, payload, expect_deny, must_contain in review_cases:
        failed += run(name, payload, expect_deny, must_contain)
    import time
    big = "const r = await agent('p', {model: 'opus', effort: 'high', schema: {type: 'object', properties: {" + ",".join("k%d: {type: 'string'}" % i for i in range(1500)) + "}}})"
    t0 = time.time(); big_reason = decide(wf(big)); dt = time.time() - t0
    ok = big_reason is None and dt < 2.0
    print(("PASS" if ok else "FAIL"), "- a 1500-key schema passes in under 2 seconds (linear depth table) | %.2fs reason=%r" % (dt, big_reason))
    failed += 0 if ok else 1
    if saved_cap is not None:                                   # the caller's env comes back only after every case ran
        os.environ["SUBAGENT_EFFORT_CAP"] = saved_cap
    if saved_owner is not None:
        os.environ[OWNER_ENV] = saved_owner
    if saved_role is not None:
        os.environ[HAIKU_ROLE_MODE_ENV] = saved_role
    try:
        _settings = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")   # the Claude home this hook is installed in
        _st = json.load(open(_settings, encoding="utf-8"))
        _env = _st.get("env") or {}
        print("%s env: owner=%s role_mode=%s cap=%s (what Claude Code passes to hooks - a terminal shell does not carry it)"
              % (_settings, _env.get(OWNER_ENV) or "(unset -> default %s)" % OWNER_DEFAULT, _env.get(HAIKU_ROLE_MODE_ENV) or "(unset -> off)",
                 _env.get("SUBAGENT_EFFORT_CAP") or "(unset -> %s)" % EFFORT_CAP_DEFAULT))
    except Exception as e:
        print("settings.json next to this hook's folder not readable (%s) - skipped the env summary" % e.__class__.__name__)
    cases = cases + cap_cases + role_cases + owner_cases + review_cases + ["big-schema"]
    try:
        os.unlink(tmp)
    except OSError:
        pass
    print("self-test: %d/%d PASS" % (len(cases) - failed, len(cases)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        _self_test()
    try:
        payload = json.load(sys.stdin)
        reason = decide(payload)
    except Exception as e:  # fail-closed - if the hook dies, it must not fall through to allow
        deny("subagent-model-guard blocked by an internal error (fail-closed): %s: %s - check ~/.claude/hooks/subagent-model-guard.py" % (e.__class__.__name__, e))
    if reason:
        deny(reason)
