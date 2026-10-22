#!/usr/bin/env python3
"""subagent model 가드 (PreToolUse: Task | Agent | Workflow).

배경: subagent 는 model 을 생략하면 부모 (세션) 모델을 그대로 상속한다. 모델 티어 규칙이 권고만으로는
여러 번 재발해서 harness 레벨에서 막는다.

막는 것은 **생략, 불명확** 과 **사용자 허용 없는 상위/하위 티어** 다 — model 을 opus / sonnet 리터럴로 적으면 통과,
fable / haiku 는 그 호출 안의 허용 표지 (`[owner-approved: fable]`, `[owner-approved: haiku]`) 와 함께일 때만.

검사 규칙:
  ① model, effort 는 **opts 최상위의 문자열 리터럴만** 인정 — 변수, 삼항식, 괄호식, 중첩 객체 (metadata.model),
     중첩 agent() 의 값은 전부 deny. 값은 문자열과 주석을 걷어낸 코드에서 찾은 key 위치에서만 읽는다
     (prompt 문자열 속 "model: 'opus'" 글자에 안 속음). 값 앞 주석 (`effort: /*j*/ 'high'`) 은 리터럴로 읽는다.
  ② inherit, 허용 외 이름 deny. full model ID (claude-haiku-4-5 류) 는 계열로 정규화해 같은 규칙 적용.
  ③ Workflow 한 스크립트에 fable agent() **호출 위치 FABLE_MAX 곳까지**. 루프 안 1곳이 N번 도는 건 정적으로
     못 센다 — 실행 횟수 상한은 규칙 (rules/subagent-review.md) 과 규모 예고 몫.
  ④ fable, haiku 는 **그 호출 안** (Agent 는 prompt, agent() 는 괄호 안) 에 표지가 있을 때만.
     모델이 표지를 스스로 적을 수 있다는 한계는 알고 둔다 — 규칙 위반으로 다룬다.
  ⑤ effort 는 EFFORTS 안 소문자 리터럴. 상한 EFFORT_CAP (기본 xhigh, env SUBAGENT_EFFORT_CAP 으로 조절 —
     settings.json env 에 두면 모든 세션에) 위 단계는 그 호출 안에 `[owner-approved: <단계>]` 표지가 있을 때만.
  ⑥ opts 는 **평평한 객체 리터럴 하나** — 삼항식으로 고른 객체 (`c ? {…} : {…}`), spread (`...`), 계산 키
     (`['effort']`), get/set, 식별자 이스케이프는 shape deny (뒤 값이 앞 리터럴을 덮는 우회). shorthand (`{effort}`) 는
     [변수] 라고 알려 준다.
  ⑦ fail-closed: 문자열·주석이 닫히지 않은 스크립트 (정규식 리터럴 `/[/*]/` 류), 읽을 수 없는 scriptPath,
     hook 내부 예외는 전부 deny. 저장된 workflow 이름 호출은 `~/.claude/workflows/<name>.js`,
     `<cwd>/.claude/workflows/<name>.js` 를 찾아 검사하고, 없으면 (내장) 통과.
  ⑧ deny 문구 = 사유 + 고칠 위치 + 짧은 GUIDE 한 줄. 상세 규칙은 rules/subagent-review.md 로.

`python3 subagent-model-guard.py --self-test` = 회귀 케이스 (self-test 의 must_contain 은 GUIDE 에 없는 사유 전용 문구).
"""
import json
import os
import re
import sys

GUIDE = (
    "규칙: model 은 opus / sonnet 리터럴로 (생략·inherit·변수 금지). "
    "단건 Agent = opus, Workflow agent() = opus, 기계적 수집·파싱 = sonnet. "
    "fable 은 사용자가 그 작업에 허용했을 때만 — 그 호출 안에 '[owner-approved: fable]' 표지 (workflow 2곳까지), "
    "haiku 도 '[owner-approved: haiku]' 표지가 있을 때만. "
    "effort 도 리터럴로 (low/medium/high/xhigh, max 는 '[owner-approved: max]' 표지 — 작업 agent high, 기계적 수집 low, judge·critic xhigh. 생략 = 세션 effort 상속). "
    "한도는 요금제마다 달라 /usage 먼저. 상세 = ~/.claude/rules/subagent-review.md"
)

TIERS = ("fable", "opus", "sonnet", "haiku")
FABLE_MAX = 2                      # Workflow 한 스크립트의 fable agent() 호출 위치 상한
HAIKU_MARK = "[owner-approved: haiku]"   # haiku 허용 표지 — 그 호출 (Agent 는 prompt) 안에 있어야 한다
FABLE_MARK = "[owner-approved: fable]"   # fable 허용 표지 — haiku 와 같은 규칙
EFFORTS = ("low", "medium", "high", "xhigh", "max")   # 낮은 것부터. Agent/Task 의 effort, workflow agent() 의 effort: 는 이 중 하나의 리터럴
EFFORT_CAP_DEFAULT = "xhigh"       # 이 위 단계 (max) 는 그 호출 안의 표지 ([owner-approved: max]) 가 있어야. env SUBAGENT_EFFORT_CAP 로 조절
EFFORT_MARK = "[owner-approved: %s]"      # effort 상한 위 단계의 허용 표지 (fable, haiku 표지와 같은 꼴)

AGENT_CALL = re.compile(r'(?<![\w.$])agent\s*\(')
# agent 를 호출이 아닌 자리 (별칭 대입, 인자, 속성 접근) 에 쓰면 호출 지점을 못 세니 deny (2026-10-22 보안 리뷰).
AGENT_ALIAS = re.compile(r'(?<![\w$])agent\b(?!\s*\()')
LITERAL = re.compile(r"""\s*(['"`])([A-Za-z0-9_.\-]+)\1""")         # 값 자리의 문자열 리터럴
OPT_KEYS = ("model", "effort")                                       # agent() opts 최상위에서 리터럴을 요구하는 키


def deny(reason):
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, sys.stdout)
    sys.exit(0)


# ----------------------------------------------------------------------------- 소스 읽기

def read_script(tool_input):
    """(script, source, error) — script 가 None 이면 검사 대상 아님 (error 가 있으면 deny 사유)."""
    script = tool_input.get("script")
    if script:
        return script, "script", None
    path = tool_input.get("scriptPath")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read(), "scriptPath", None
        except (OSError, UnicodeDecodeError) as e:
            return None, "scriptPath", "scriptPath 를 읽을 수 없어 (%s): %s" % (e.__class__.__name__, path)
    name = tool_input.get("name")
    if name:
        for base in (os.path.expanduser("~/.claude/workflows"), os.path.join(os.getcwd(), ".claude", "workflows")):
            p = os.path.join(base, name + ".js")
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as fh:
                        return fh.read(), "name:" + p, None
                except (OSError, UnicodeDecodeError) as e:
                    return None, "name", "저장된 workflow %s 를 읽을 수 없어 (%s)" % (p, e.__class__.__name__)
    return None, None, None   # 내장 workflow 등 — 소스를 알 수 없으면 검사하지 않는다


# ----------------------------------------------------------------------------- 코드 영역 추출

def blank_strings_and_comments(src):
    """(문자열·주석 내용을 공백으로 바꾼 같은 길이의 텍스트, 끝 상태) 를 돌려준다.

    개행은 보존해 줄 번호와 오프셋이 원문과 1:1 로 맞는다. 템플릿 리터럴의 ${ … } 안은 코드라서
    그대로 둔다 (중첩 중괄호 추적). 정규식 리터럴은 다루지 않는다 — `/[/*]/` 처럼 주석·문자열을
    여는 꼴이면 끝 상태가 "code" 가 아니라서 호출자가 deny 한다 (fail-closed).
    """
    out = []
    n = len(src)
    i = 0
    state = "code"
    stack = []            # 템플릿 안 ${ 로 들어간 코드 레벨의 중괄호 깊이
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                state = "line"; out.append("  "); i += 2; continue
            if c == "/" and nxt == "*":
                state = "block"; out.append("  "); i += 2; continue
            if c in ("'", '"', "`"):
                state = c; out.append(c); i += 1; continue        # 따옴표 자체는 남긴다 (리터럴 위치 추적용)
            if stack:
                if c == "{":
                    stack[-1] += 1
                elif c == "}":
                    if stack[-1] == 0:
                        stack.pop(); state = "`"; out.append("}"); i += 1; continue
                    stack[-1] -= 1
            out.append(c); i += 1; continue
        if state == "line":
            if c == "\n":
                state = "code"; out.append(c)
            else:
                out.append(" ")
            i += 1; continue
        if state == "block":
            if c == "*" and nxt == "/":
                state = "code"; out.append("  "); i += 2; continue
            out.append(c if c == "\n" else " "); i += 1; continue
        # 문자열 상태 (' " `)
        if c == "\\":
            out.append(" "); out.append("\n" if nxt == "\n" else " "); i += 2; continue
        if state == "`" and c == "$" and nxt == "{":
            stack.append(0); state = "code"; out.append("${"); i += 2; continue
        if c == state:
            state = "code"; out.append(c); i += 1; continue
        out.append(c if c == "\n" else " "); i += 1; continue
    return "".join(out), state


def _depths_at(text, idx):
    """text[:idx] 의 (중괄호, 소괄호, 대괄호) 깊이."""
    b = p = k = 0
    for ch in text[:idx]:
        if ch == "{": b += 1
        elif ch == "}": b -= 1
        elif ch == "(": p += 1
        elif ch == ")": p -= 1
        elif ch == "[": k += 1
        elif ch == "]": k -= 1
    return b, p, k


def _literal_value(code, raw, pos):
    """pos (model: 의 콜론 뒤) 에서 시작하는 값이 **문자열 리터럴 하나** 면 그 값, 아니면 None.

    리터럴 뒤에 , } 또는 끝만 올 수 있다 — 'opus' ? 'haiku' : 'sonnet' 같은 식은 리터럴이 아니다.
    값 앞 공백과 주석은 블랭크 코드 (주석 = 공백) 에서 건너뛰고, 따옴표 자리부터 원문에서 읽는다 (effort: /*j*/ 'high' 도 리터럴).
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


def _opt_literal(code, script, start, end, key):
    """agent() 괄호 안 (code[start:end]) 의 opts **최상위** `key:` (또는 'key':) 값 → (status, value), status ∈ missing | expr | ok.

    최상위 = 중괄호 깊이 1, 소괄호·대괄호 깊이 0. 중첩 객체, 중첩 호출의 같은 키는 무시 (= missing). 같은 키가 여럿이면 JS 처럼 마지막 것.
    따옴표 키는 블랭크된 코드에서 `'     ':` 꼴 (키 길이만큼 공백) 로 보이므로 원문에서 이름을 확인한다.
    """
    args = code[start:end]
    bare = re.compile(r"(?<![\w.$])%s\s*:" % key)                 # $effort:, o.effort : 는 이 키가 아니다
    quoted = re.compile(r"""(['"`])\s{%d}\1\s*:""" % len(key))
    shorthand = re.compile(r"(?<![\w.$])%s\s*[,}]" % key)          # {effort} = 변수 effort 를 그 이름으로 넣는 꼴
    ends = []
    for km in bare.finditer(args):
        if _depths_at(args, km.start()) == (1, 0, 0):
            ends.append(km.end())
    for qm in quoted.finditer(args):
        if script[start + qm.start() + 1: start + qm.start() + 1 + len(key)] == key and _depths_at(args, qm.start()) == (1, 0, 0):
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


def _opts_shape(code, start, end):
    """agent() 괄호 안의 opts 가 **평평한 객체 리터럴 하나** 가 아니면 그 이유, 맞으면 None.

    런타임 값이 hook 이 읽은 리터럴과 달라질 수 있는 꼴을 전부 거절한다 (fail-closed): 최상위 객체 리터럴이 둘 이상 (c ? {…} : {…}),
    객체 앞뒤에 다른 식 (c ? X : {…}, {…}.x), spread (...), 계산 키 (['effort']), get/set 메소드, 식별자 이스케이프 (역슬래시).
    `${` (프롬프트 템플릿 리터럴의 식) 는 객체가 아니다. 객체가 하나도 없으면 (opts 생략, 변수) 여기서는 두고 missing 이 알린다.
    """
    args = code[start:end]
    opens = [i for i, ch in enumerate(args) if ch == "{" and not (i > 0 and args[i - 1] == "$") and _depths_at(args, i) == (0, 0, 0)]
    if not opens:
        return None
    if len(opens) > 1:
        return "opts 자리에 객체 리터럴이 %d개 (삼항식으로 고르는 꼴) — 하나만" % len(opens)
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
        return "opts 객체가 닫히지 않았어"
    before = args[:o]
    commas = [i for i, ch in enumerate(before) if ch == "," and _depths_at(before, i) == (0, 0, 0)]
    if before[(commas[-1] + 1) if commas else 0:].strip():
        return "opts 객체 앞에 다른 식이 있어 (c ? X : {…} 꼴)"
    if args[close + 1:].strip().rstrip(",").strip():
        return "opts 객체 뒤에 다른 식이 있어"
    body = args[o + 1:close]
    for i, ch in enumerate(body):
        if _depths_at(body, i) != (0, 0, 0):
            continue
        if ch == "\\":
            return "키에 식별자 이스케이프 (역슬래시)"
        if ch == "." and body[i:i + 3] == "...":
            return "spread (...) 는 뒤 값이 앞 리터럴을 덮어"
        if ch == "[":
            prev = body[:i].rstrip()
            if not prev or prev[-1] in "{,":
                return "계산 키 (['effort']) 는 못 읽어"
    if re.search(r"(?<![\w.$])(get|set)\s+[\w$]+\s*\(", body):
        return "get/set 메소드는 못 읽어"
    return None


def agent_calls(script):
    """agent( 호출마다 {"line", "model", "status", "effort", "effort_status", "shape", "args"} — status ∈ missing | shorthand | expr | ok.

    model, effort 는 opts (두 번째 인자 객체) 의 **최상위** 키 값만 본다 (_opt_literal). shape 는 _opts_shape 의 거부 이유 (없으면 None).
    """
    code, end_state = blank_strings_and_comments(script)
    if end_state != "code":
        raise ValueError("닫히지 않은 문자열 또는 주석 — 정규식 리터럴 (/[/*]/ 류) 이면 new RegExp('…') 로 바꿔줘")
    alias = AGENT_ALIAS.search(code)
    if alias:
        raise ValueError("agent 를 호출 말고 다른 자리 (별칭, 인자, 속성) 에 썼어 (script, 줄 %d) — 호출마다 agent(...) 로 직접 써줘"
                         % (code.count("\n", 0, alias.start()) + 1))
    calls = []
    for m in AGENT_CALL.finditer(code):
        start = m.end()           # '(' 바로 뒤
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
                      "shape": _opts_shape(code, start, end), "args": script[start:end]})
    return calls


def agent_calls_without_model(script):
    """(전체 호출 수, model 없는 호출의 줄 번호 목록) — 구 인터페이스 유지."""
    calls = agent_calls(script)
    return len(calls), [c["line"] for c in calls if c["status"] == "missing"]


# ----------------------------------------------------------------------------- 판정

def classify(model):
    """모델 값 → (tier, error). full ID (claude-haiku-4-5) 도 계열로 정규화."""
    v = str(model).strip().lower()
    if not v or v == "inherit":
        return None, "model '%s' 은 금지 — 상속하려면 현재 모델 이름을 그대로 적어줘" % v
    for t in TIERS:
        if t in v:
            return t, None
    return None, "model '%s' 은 허용 목록 (fable / opus / sonnet / haiku) 에 없어" % v


def _lines(nums):
    return ", ".join(str(n) for n in nums)


def effort_cap():
    """effort 상한. env SUBAGENT_EFFORT_CAP (EFFORTS 안 값) 이 있으면 그 값, 아니면 기본 (xhigh)."""
    v = os.environ.get("SUBAGENT_EFFORT_CAP", "").strip().lower()
    return v if v in EFFORTS else EFFORT_CAP_DEFAULT


def effort_error(effort, text, where):
    """effort 값 (Agent 의 tool_input.effort, agent() 의 effort: 리터럴) 의 거부 사유 또는 None. text = 표지를 찾을 범위 (prompt, 괄호 안)."""
    if effort is None or str(effort).strip() == "":
        return (where + " 에 effort 가 없어~ 세션 effort 가 그대로 상속되니 effort 를 적어서 다시 불러줘~ "
                "(작업 agent high, 기계적 수집 low, judge·critic xhigh)")
    v = str(effort).strip()
    if v not in EFFORTS:
        if v.lower() in EFFORTS:
            return where + ": effort '%s' 은 소문자로 적어줘 ('%s') — 런타임이 대문자를 받는지 모르니 그대로 안 보내~" % (v, v.lower())
        return where + ": effort '%s' 은 허용 목록 (%s) 에 없어~" % (v, "/".join(EFFORTS))
    cap = effort_cap()
    if EFFORTS.index(v) > EFFORTS.index(cap) and (EFFORT_MARK % v) not in (text or ""):
        return (where + ": effort '%s' 은 상한 '%s' 위야~ 사용자가 그 작업에 허용했으면 그 호출 안에 '%s' 표지, 아니면 '%s' 이하로. "
                "(상한은 env SUBAGENT_EFFORT_CAP 로 조절)" % (v, cap, EFFORT_MARK % v, cap))
    return None


def decide(payload):
    """거부 사유 문자열 또는 None."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool in ("Agent", "Task"):
        model = tool_input.get("model")
        if not model:
            return tool + " 호출에 model 이 없어~ 부모 모델이 그대로 상속되니 model 넣어서 다시 불러줘~ " + GUIDE
        tier, err = classify(model)
        if err:
            return tool + ": " + err + "~ " + GUIDE
        if tier == "haiku" and HAIKU_MARK not in (tool_input.get("prompt") or ""):
            return (tool + " 에 haiku 를 쓰려면 사용자가 그 작업에 허용했어야 해~ 허용받았으면 prompt 에 "
                    + HAIKU_MARK + " 표지, 아니면 sonnet 으로. " + GUIDE)
        if tier == "fable" and FABLE_MARK not in (tool_input.get("prompt") or ""):
            return (tool + " 에 fable 을 쓰려면 사용자가 그 작업에 허용했어야 해~ 허용받았으면 prompt 에 "
                    + FABLE_MARK + " 표지, 아니면 opus 로. " + GUIDE)
        err = effort_error(tool_input.get("effort"), tool_input.get("prompt") or "", tool + " 호출")
        if err:
            return err + " " + GUIDE
        return None

    if tool == "Workflow":
        script, source, err = read_script(tool_input)
        if err:
            return "Workflow " + err + "~ 경로를 확인하고 다시 불러줘. " + GUIDE
        if not script:
            return None  # 내장 workflow 등 — 검사 대상 아님
        try:
            calls = agent_calls(script)
        except ValueError as e:
            return "Workflow 스크립트를 해석할 수 없어 (%s): %s~ " % (source, e) + GUIDE
        shapes = [(c["line"], c["shape"]) for c in calls if c["shape"]]
        if shapes:
            return ("Workflow agent() 의 opts 가 평평한 객체 리터럴 하나가 아니야 (줄 %s: %s) — 런타임 값이 hook 이 읽은 것과 달라질 수 있어서 막아. "
                    "{model: 'opus', effort: 'high', …} 를 그 자리에 직접 적어줘~ " % (_lines([l for l, _ in shapes]), shapes[0][1]) + GUIDE)
        short = [c["line"] for c in calls if c["status"] == "shorthand" or c["effort_status"] == "shorthand"]
        if short:
            return ("Workflow agent() 의 model 이나 effort 가 shorthand ({effort}) 야 (줄 %s) — 변수라서 못 읽어, 'high' 처럼 리터럴로 적어줘~ "
                    % _lines(short) + GUIDE)
        missing = [c["line"] for c in calls if c["status"] == "missing"]
        if missing:
            return ("Workflow agent() %d개 중 %d개에 model 이 없어~ (%s, 줄 %s). opts 최상위에 model 을 넣고 다시 돌려줘~ "
                    "(meta.phases 의 model, 중첩 객체 안 model 은 안 세) " % (len(calls), len(missing), source, _lines(missing)) + GUIDE)
        expr = [c["line"] for c in calls if c["status"] == "expr"]
        if expr:
            return ("Workflow agent() 의 model 이 문자열 리터럴이 아니야 (줄 %s) — 변수, 삼항식, 괄호식 말고 "
                    "'opus' / 'sonnet' 을 직접 적어줘~ " % _lines(expr) + GUIDE)
        tiers = {}
        for c in calls:
            tier, err = classify(c["model"])
            if err:
                return "Workflow agent() 줄 %d: %s~ " % (c["line"], err) + GUIDE
            tiers.setdefault(tier, []).append(c)
        fable_unmarked = [c["line"] for c in tiers.get("fable", []) if FABLE_MARK not in c["args"]]
        if fable_unmarked:
            return ("Workflow 의 fable agent() (줄 %s) 는 사용자가 그 작업에 허용했을 때만이야~ 허용받았으면 그 agent() 괄호 안 "
                    "(label 이나 prompt) 에 %s 표지, 아니면 opus 로. " % (_lines(fable_unmarked), FABLE_MARK) + GUIDE)
        fable = [c["line"] for c in tiers.get("fable", [])]
        if len(fable) > FABLE_MAX:
            return ("Workflow 에 fable agent() 가 %d곳 (줄 %s) — fable 은 judge/critic 자리 %d곳까지, 나머지는 opus 로~ "
                    % (len(fable), _lines(fable), FABLE_MAX) + GUIDE)
        haiku = [c["line"] for c in tiers.get("haiku", []) if HAIKU_MARK not in c["args"]]
        if haiku:
            return ("Workflow 의 haiku agent() (줄 %s) 는 사용자가 그 작업에 허용했을 때만이야~ 허용받았으면 그 agent() 괄호 안 "
                    "(label 이나 prompt) 에 %s 표지, 아니면 sonnet 으로. " % (_lines(haiku), HAIKU_MARK) + GUIDE)
        emissing = [c["line"] for c in calls if c["effort_status"] == "missing"]
        if emissing:
            return ("Workflow agent() %d개 중 %d개에 effort 가 없어~ (%s, 줄 %s). 세션 effort 가 상속되니 opts 최상위에 effort 를 넣어줘 "
                    "(작업 high, 기계적 수집 low, judge·critic xhigh)~ " % (len(calls), len(emissing), source, _lines(emissing)) + GUIDE)
        eexpr = [c["line"] for c in calls if c["effort_status"] == "expr"]
        if eexpr:
            return ("Workflow agent() 의 effort 가 문자열 리터럴이 아니야 (줄 %s) — 변수, 삼항식 말고 'high' 처럼 직접 적어줘~ "
                    % _lines(eexpr) + GUIDE)
        for c in calls:
            err = effort_error(c["effort"], c["args"], "Workflow agent() 줄 %d" % c["line"])
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
        # --- 2026-09-24 회귀 16
        ("Agent model 없음 → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "effort": "high"}}, True, "model 이 없어"),
        ("Agent model 있음 (opus) → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "high"}}, False, None),
        ("Task (구버전 이름) model 없음 → deny",
         {"tool_name": "Task", "tool_input": {"prompt": "x", "effort": "high"}}, True, "Task 호출에 model 이 없어"),
        ("Task model 있음 → pass",
         {"tool_name": "Task", "tool_input": {"prompt": "x", "model": "sonnet", "effort": "low"}}, False, None),
        ("Workflow 두 호출 전부 model → pass",
         wf("const a = await agent('p', {model: 'opus', effort: 'high'})\nconst b = await agent('q', { label: 'x', model: 'sonnet', effort: 'low' })"),
         False, None),
        ("Workflow agent 별칭 대입 → deny",
         wf("const run = agent\nconst r = await run('p', {model: 'opus', effort: 'high'})"),
         True, "별칭"),
        ("Workflow agent 를 인자로 전달 → deny",
         wf("const r = await helper(agent, ITEMS)"),
         True, "별칭"),
        ("Workflow agentType 키는 별칭 아님 → pass",
         wf("const r = await agent('p', { agentType: 'review-readonly', model: 'opus', effort: 'high' })"),
         False, None),
        ("Workflow 둘 중 하나 누락 → deny (줄 2)",
         wf("const a = await agent('p', {model: 'opus', effort: 'high'})\nconst b = await agent('q', { label: 'x', effort: 'high' })"),
         True, "model 이 없어~ (script, 줄 2)"),
        ("meta.phases 의 model 만 있고 agent 는 없음 → deny (구 방식 미탐 회귀)",
         wf("export const meta = { name: 'x', phases: [{ title: 'a', model: 'opus' }] }\nconst r = await agent('do', { label: 'one', effort: 'high' })\nreturn r"),
         True, "model 이 없어~ (script, 줄 2)"),
        ("프롬프트 문자열 속 'agent() 호출' 글자는 세지 않음 → pass (구 방식 오탐 회귀)",
         wf("const P = `이 스크립트의 agent() 호출 2개 중 model 지정...`\nconst r = await agent(P, {model: 'opus', effort: 'high'})"),
         False, None),
        ("주석 속 agent( 만 → pass (호출 0)",
         wf("// agent('x')\n/* agent('y') */\nreturn 1"), False, None),
        ("템플릿 ${} 안 호출도 검사 → deny",
         wf("const s = `v=${await agent('p', {label: 'l', effort: 'high'})}`"), True, "model 이 없어~ (script, 줄 1)"),
        ("템플릿 ${} 안 호출 model 있음 → pass",
         wf("const s = `v=${await agent('p', {label: 'l', model: 'opus', effort: 'medium'})}`"), False, None),
        ("여러 줄 opts → pass",
         wf("const r = await agent(\n  'p',\n  {\n    label: 'x',\n    schema: S,\n    model: 'opus',\n    effort: 'high',\n  },\n)"), False, None),
        ("pipeline 콜백 안 호출 누락 → deny (줄 3)",
         wf("const r = await pipeline(\n  ITEMS,\n  it => agent(it.p, { label: it.k, schema: S, effort: 'high' }),\n  (a, it) => agent('v', { model: 'opus', effort: 'high' }),\n)"),
         True, "model 이 없어~ (script, 줄 3)"),
        ("agentType: 키는 호출로 안 봄 → pass",
         wf("const r = await agent('p', { agentType: 'general-purpose', model: 'sonnet', effort: 'low' })"), False, None),
        ("저장된 workflow 이름 (내장, 소스 없음) → pass",
         {"tool_name": "Workflow", "tool_input": {"name": "review-changes-builtin-nonexistent-zz"}}, False, None),
        ("따옴표 키 {'model': 'opus'} → pass",
         wf("const r = await agent('p', { 'model': 'opus', label: 'x', 'effort': 'high' })"), False, None),
        # --- 2026-10-11 fable 상한, haiku 표지
        ("Workflow fable 표지 2곳 → pass (상한 안)",
         wf("const a = await agent('p', {label: '[owner-approved: fable] judge', model: 'fable', effort: 'xhigh'})\nconst b = await agent('q', {label: '[owner-approved: fable] critic', model: 'fable', effort: 'xhigh'})\nconst c = await agent('r', {model: 'opus', effort: 'high'})"),
         False, None),
        ("Workflow fable 표지 3곳 → deny (줄 1, 2, 3)",
         wf("const a = await agent('p', {label: '[owner-approved: fable]', model: 'fable', effort: 'high'})\nconst b = await agent('q', {label: '[owner-approved: fable]', model: \"fable\", effort: 'high'})\nconst c = await agent('r', { label: '[owner-approved: fable] x', model: 'fable', effort: 'high' })"),
         True, "fable agent() 가 3곳 (줄 1, 2, 3)"),
        # --- 2026-10-13 fable 표지
        ("Agent fable 표지 없음 → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "fable", "effort": "high"}}, True, "fable 을 쓰려면"),
        ("Agent fable 표지 있음 → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: fable] 미묘한 설계 판정", "model": "fable", "effort": "xhigh"}}, False, None),
        ("Workflow fable 표지 없음 → deny (10/13 카드 검증 사고 재현)",
         wf("const r = await parallel(G.map(g => () => agent(g.p, { label: 'verify:' + g.key, model: 'fable', effort: 'high' })))"), True, "fable agent() (줄 1) 는 사용자가"),
        ("Workflow fable 표지가 호출 밖 주석에만 → deny",
         wf("// [owner-approved: fable]\nconst a = await agent('p', {model: 'fable', effort: 'high'})"), True, "fable agent() (줄 2) 는 사용자가"),
        ("Workflow fable 표지 1곳 + opus → pass",
         wf("const a = await agent('p', {label: '[owner-approved: fable] judge', model: 'fable', effort: 'xhigh'})\nconst b = await agent('q', {model: 'opus', effort: 'high'})"), False, None),
        ("Workflow haiku 표지 없음 → deny",
         wf("const a = await agent('p', {model: 'haiku', effort: 'low'})"), True, "haiku agent() (줄 1) 는 사용자가"),
        ("Workflow haiku 표지가 그 호출 안 (label) → pass",
         wf("const a = await agent('p', {label: '[owner-approved: haiku] 파싱', model: 'haiku', effort: 'low'})"), False, None),
        ("Workflow haiku 표지가 호출 밖 주석에만 → deny (범위 좁힘)",
         wf("// [owner-approved: haiku]\nconst a = await agent('p', {model: 'haiku', effort: 'low'})"), True, "haiku agent() (줄 2) 는 사용자가"),
        ("Workflow full ID claude-haiku-4-5 표지 없음 → deny",
         wf("const a = await agent('p', {model: 'claude-haiku-4-5', effort: 'low'})"), True, "haiku agent() (줄 1) 는 사용자가"),
        ("Workflow full ID claude-opus-5-5 → pass",
         wf("const a = await agent('p', {model: 'claude-opus-5-5', effort: 'high'})"), False, None),
        # --- 2026-10-11 Codex 지적: 값 읽기 위치, 중첩, 비리터럴, inherit, 파싱 실패
        ("프롬프트 문자열 속 model: 'opus' 글자에 안 속음 → deny (실제는 haiku)",
         wf("const a = await agent(\"model: 'opus' 로 해줘\", {model: 'haiku', effort: 'low'})"), True, "haiku agent() (줄 1) 는 사용자가"),
        ("중첩 객체 metadata.model 만 → deny (missing)",
         wf("const a = await agent('p', {metadata: {model: 'opus'}, label: 'x', effort: 'high'})"), True, "model 이 없어~ (script, 줄 1)"),
        ("중첩 agent() 의 model 이 바깥 누락을 못 가림 → deny (줄 1)",
         wf("const a = await agent(await agent('inner', {model: 'opus', effort: 'high'}), {label: 'outer', effort: 'high'})"), True, "model 이 없어~ (script, 줄 1)"),
        ("model 이 변수 → deny (리터럴 아님)",
         wf("const M = pick()\nconst a = await agent('p', {model: M, effort: 'high'})"), True, "model 이 문자열 리터럴이 아니야"),
        ("model 이 삼항식 'opus' ? 'haiku' : 'sonnet' → deny",
         wf("const a = await agent('p', {model: 'opus' ? 'haiku' : 'sonnet', effort: 'high'})"), True, "model 이 문자열 리터럴이 아니야"),
        ("model 이 괄호식 ('haiku') → deny",
         wf("const a = await agent('p', {model: ('haiku'), effort: 'high'})"), True, "model 이 문자열 리터럴이 아니야"),
        ("Workflow model: 'inherit' → deny",
         wf("const a = await agent('p', {model: 'inherit', effort: 'high'})"), True, "은 금지 — 상속하려면"),
        ("Agent model inherit → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "inherit", "effort": "high"}}, True, "은 금지 — 상속하려면"),
        ("Agent 허용 외 모델 이름 → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "gpt-6", "effort": "high"}}, True, "허용 목록 (fable / opus / sonnet / haiku) 에 없어"),
        ("Agent haiku 표지 없음 → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "haiku", "effort": "low"}}, True, "haiku 를 쓰려면"),
        ("Agent haiku 표지 있음 → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: haiku] 로그 파싱", "model": "haiku", "effort": "low"}}, False, None),
        ("정규식 리터럴 /[/*]/ 가 주석을 열어 파싱 깨짐 → deny (fail-closed)",
         wf("const r = /[/*]/\nconst a = await agent('p', {model: 'haiku', effort: 'low'})"), True, "해석할 수 없어"),
        # --- 2026-10-13 저녁 effort
        ("Agent effort 없음 → deny (세션 effort 상속)",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus"}}, True, "effort 가 없어"),
        ("Agent effort 허용 외 값 → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "ultra"}}, True, "허용 목록"),
        ("Agent effort max 표지 없음 → deny (상한 xhigh)",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "max"}}, True, "상한"),
        ("Agent effort max 표지 있음 → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: max] 최종 판정", "model": "opus", "effort": "max"}}, False, None),
        ("Agent effort xhigh (기본 상한 안) → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "xhigh"}}, False, None),
        ("Workflow effort 둘 중 하나 누락 → deny (줄 2)",
         wf("const a = await agent('p', {model: 'opus', effort: 'high'})\nconst b = await agent('q', {model: 'opus'})"), True, "effort 가 없어~ (script, 줄 2)"),
        ("Workflow effort 변수 → deny (리터럴 아님)",
         wf("const E = 'high'\nconst a = await agent('p', {model: 'opus', effort: E})"), True, "effort 가 문자열 리터럴이 아니야"),
        ("Workflow effort 삼항식 → deny",
         wf("const a = await agent('p', {model: 'opus', effort: big ? 'xhigh' : 'low'})"), True, "effort 가 문자열 리터럴이 아니야"),
        ("Workflow effort max 표지가 그 호출 안 → pass",
         wf("const a = await agent('p', {label: '[owner-approved: max] judge', model: 'opus', effort: 'max'})"), False, None),
        ("Workflow effort max 표지가 호출 밖 주석에만 → deny",
         wf("// [owner-approved: max]\nconst a = await agent('p', {model: 'opus', effort: 'max'})"), True, "상한"),
        ("Workflow 중첩 객체 안 effort 만 → deny (missing)",
         wf("const a = await agent('p', {model: 'opus', metadata: {effort: 'high'}})"), True, "effort 가 없어"),
        ("프롬프트 문자열 속 effort: 'low' 글자에 안 속음 → deny (실제는 생략)",
         wf("const a = await agent(\"effort: 'low' 로 해줘\", {model: 'opus'})"), True, "effort 가 없어"),
        # --- 2026-10-13 저녁 반증 리뷰: opts 모양, 키 경계, 값 앞 주석, shorthand, 소문자
        ("opts 삼항식 (객체 둘) → shape deny (앞 분기 max 가 표지 없이 지나가던 우회)",
         wf("const a = await agent('p', big ? {model: 'opus', effort: 'max'} : {model: 'opus', effort: 'high'})"), True, "객체 리터럴이 2개"),
        ("opts 삼항식 (변수 : 객체) → shape deny",
         wf("const a = await agent('p', big ? OPTS : {model: 'opus', effort: 'high'})"), True, "객체 앞에 다른 식"),
        ("opts spread → shape deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'high', ...(c ? {effort: 'max'} : {})})"), True, "spread"),
        ("opts 계산 키 ['effort'] → shape deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'high', ['effort']: 'max'})"), True, "계산 키"),
        ("opts getter → shape deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'high', get effort() { return 'max' }})"), True, "get/set"),
        ("opts 뒤에 .x → shape deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'high'}.x)"), True, "객체 뒤에 다른 식"),
        ("$effort: 는 키가 아니다 → effort 없음 deny",
         wf("const a = await agent('p', {model: 'opus', $effort: 'high'})"), True, "effort 가 없어"),
        ("label 값 안의 o.effort : 는 키가 아니다 → pass",
         wf("const a = await agent('p', {model: 'opus', effort: 'high', label: big ? cfg.effort : 'judge'})"), False, None),
        ("shorthand {effort} → 변수라고 알려 주며 deny",
         wf("const effort = 'high'\nconst a = await agent('p', {model: 'opus', effort})"), True, "shorthand"),
        ("값 앞 주석 effort: /*j*/ 'high' → pass",
         wf("const a = await agent('p', {model: 'opus', effort: /*judge*/ 'high'})"), False, None),
        ("effort 'High' (대문자) → 소문자로 deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'High'})"), True, "소문자로"),
        ("프롬프트 템플릿의 ${} 와 배열 값, 끝 쉼표는 모양 위반이 아니다 → pass",
         wf("const a = await agent(`k=${key} v=${f(x)}`, {model: 'opus', effort: 'high', files: [a, b], schema: {type: 'object'},},)"), False, None),
        ("Agent effort 'XHIGH' (대문자) → 소문자로 deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "XHIGH"}}, True, "소문자로"),
    ]
    # env 로 상한을 내리면 그 위 단계는 표지 없이 deny (settings.json env 에 두면 모든 세션에)
    cap_cases = [
        ("SUBAGENT_EFFORT_CAP=high: Agent xhigh 표지 없음 → deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "xhigh"}}, True, "상한 'high'"),
        ("SUBAGENT_EFFORT_CAP=high: Agent xhigh 표지 있음 → pass",
         {"tool_name": "Agent", "tool_input": {"prompt": "[owner-approved: xhigh] judge", "model": "opus", "effort": "xhigh"}}, False, None),
        ("SUBAGENT_EFFORT_CAP=high: Workflow high → pass",
         wf("const a = await agent('p', {model: 'opus', effort: 'high'})"), False, None),
        ("SUBAGENT_EFFORT_CAP=bogus: 기본 상한 (xhigh) 으로 → max deny",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "max"}}, True, "상한 'xhigh'"),
        ("SUBAGENT_EFFORT_CAP=high: Workflow xhigh 표지 없음 → deny",
         wf("const a = await agent('p', {model: 'opus', effort: 'xhigh'})"), True, "상한 'high'"),
        ("SUBAGENT_EFFORT_CAP=high: Workflow xhigh 표지가 그 호출 안 → pass",
         wf("const a = await agent('p', {label: '[owner-approved: xhigh] judge', model: 'opus', effort: 'xhigh'})"), False, None),
        ("SUBAGENT_EFFORT_CAP=max: Agent max 표지 없음 → pass (상한이 max)",
         {"tool_name": "Agent", "tool_input": {"prompt": "x", "model": "opus", "effort": "max"}}, False, None),
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write("const a = await agent('p', { label: 'x', effort: 'high' })\n")
        tmp = fh.name
    cases.append(("scriptPath 누락 → deny (scriptPath)",
                  {"tool_name": "Workflow", "tool_input": {"scriptPath": tmp}}, True, "scriptPath"))
    cases.append(("scriptPath 읽기 불가 → deny (fail-closed)",
                  {"tool_name": "Workflow", "tool_input": {"scriptPath": tmp + ".nope"}}, True, "읽을 수 없어"))
    for token in ("fable", "opus", "sonnet", "haiku", "effort", "생략", "/usage"):
        if token not in GUIDE:
            print("FAIL - GUIDE 문구에 %r 가 없다" % token)
            sys.exit(1)
    print("PASS - GUIDE 문구 토큰 7종 확인 (fable/opus/sonnet/haiku/effort/생략//usage), 길이 %d자" % len(GUIDE))
    failed = 0

    def run(name, payload, expect_deny, must_contain):
        if must_contain and must_contain in GUIDE:   # GUIDE 는 모든 deny 사유 끝에 붙으니, 거기 든 문구로는 어느 게이트가 막았는지 못 가른다
            print("FAIL -", name, "| must_contain %r 가 GUIDE 에 들어 있어 (사유 전용 문구로)" % must_contain)
            return 1
        reason = decide(payload)
        ok = (reason is not None) == expect_deny
        if ok and must_contain and must_contain not in (reason or ""):
            ok = False
        print(("PASS" if ok else "FAIL"), "-", name, ("" if ok else " | reason=%r" % reason))
        return 0 if ok else 1

    saved_cap = os.environ.pop("SUBAGENT_EFFORT_CAP", None)   # 기본 상한으로 본다 (호출 환경의 값은 끝에 복원)
    for name, payload, expect_deny, must_contain in cases:
        failed += run(name, payload, expect_deny, must_contain)
    for name, payload, expect_deny, must_contain in cap_cases:
        os.environ["SUBAGENT_EFFORT_CAP"] = name.split(":")[0].split("=")[1]   # 케이스 이름 머리 (SUBAGENT_EFFORT_CAP=<값>) 이 env 값
        failed += run(name, payload, expect_deny, must_contain)
    os.environ.pop("SUBAGENT_EFFORT_CAP", None)
    if saved_cap is not None:
        os.environ["SUBAGENT_EFFORT_CAP"] = saved_cap
    cases = cases + cap_cases
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
    except Exception as e:  # fail-closed — hook 이 죽으면 허용으로 흘러가지 않게
        deny("subagent-model-guard 내부 오류로 차단 (fail-closed): %s: %s — ~/.claude/hooks/subagent-model-guard.py 확인" % (e.__class__.__name__, e))
    if reason:
        deny(reason)
