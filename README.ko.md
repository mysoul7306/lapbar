> 🌐 **English**: [README.md](README.md)

# lapbar 🎢

*롤러코스터 무릎 안전바 (lap bar) 에서 따온 이름 - 자리에 붙잡아 주는 장치이지 가두는 우리가 아니다.*

**비싼 모델을 머리로, 싼 모델을 손발로 - hook 이 강제하는 Claude Code 하네스와, 티어를 나눈 값어치를 보여 주는 리포트.**

> 한 줄 요약: `lapbar install` 이 Claude Code hook, rules, skills 몇 개를 `~/.claude` 에 깔고, `lapbar report` 가 내 transcript 를 읽어 같은 서브에이전트 요청을 머리 모델 단가로 매겼을 때보다 얼마나 싸게 했는지 보여 준다.

## 설치

```
# PyPI
pipx install lapbar               # or: uv tool install lapbar

# 소스에서
git clone https://github.com/mysoul7306/lapbar && cd lapbar
pipx install .                    # or: pip install .
```

그 다음:

```
lapbar install --dry-run          # what would be installed, nothing written
lapbar install                    # hooks + rules + skills + agent into ~/.claude, settings.json wired (backup kept)
lapbar doctor                     # everything in place, hook self-tests pass
lapbar report --since <설치한 날>  # 내 숫자를, 내 transcript 에서
lapbar session                    # one session: hook injection, rules loaded, subagent models, tokens
```

## 왜 만들었나

Claude Code 에서 가장 비싼 건 **머리 모델이 손발 일까지 하는 것** 이다. 파일 훑기, 리뷰 렌즈, 수집 같은 서브에이전트 작업은 더 싼 모델로도 품질이 같은데, `Agent` / `Workflow` 호출에 model 을 안 적으면 머리 모델을 그대로 상속한다. 그래서 hook 으로 막았다.

- **model, effort 는 리터럴로** - `subagent-model-guard.py` 가 `Agent`, `Task`, `Workflow` 호출에서 model, effort 의 생략, 변수, 삼항식을 거부한다 (deny). 최상위 모델 (fable) 과 최하위 모델 (haiku, 품질 사고 전례로 기본 차단) 은 그 호출 안에 허용 표지가 있을 때만, haiku 는 역할 모드에서 역할 표지 + 검증 표지로도 통과한다. 표지, 설정, Workflow 스크립트에서 거부되는 꼴은 [model guard 자세히](#model-guard-자세히) 에.
- **레인 루프** - `/lane` (머리는 해독, 분해, 종합만; read 는 opus high, collect 는 sonnet low, refute 는 opus xhigh) 과 `/review-lane` (렌즈 3 → 반박자 2, agent 상한 10) 이 그 배분을 절차로 굳힌다.
- **보호 브랜치 push 차단** - `git-push-guard.py` 가 develop / main / master 로 가는 push 를 refspec, upstream 까지 풀어 판정하고 못 풀면 fail-closed 로 막는다. 머지는 AI 가, push 는 사람이.
- **메모리 위생** - `memory-lint-hook.py` 가 세션 시작 때 메모리 디렉토리의 깨진 링크, 공인 IP, 비밀값, 라우터 크기를 점검한다.

두 가드는 **정규식 기반** 이다 - AI 가 실수로 push 하거나 model 을 빠뜨리는 걸 막는 안전장치이지, 작정한 우회 (eval, 설정 주입, 셸 트릭) 를 전부 막는 샌드박스가 아니다. 알려진 우회 꼴은 self-test 케이스로 하나씩 닫고 있고 (push 60, model 153), model guard 의 JS 읽기는 진짜 파서와 비교하는 차등 퍼징 (`tools/fuzz/`, 이 판에서 60,000 스크립트 중 우회 0) 으로 점검한다. AST 파서 전환은 다음 판 과제. 그래서 `Workflow` 스크립트에 쓰면 안 되는 것은 [Workflow 스크립트에 쓰면 안 되는 것](#workflow-스크립트에-쓰면-안-되는-것) 에 모아 두었다.

## `lapbar report` 가 재는 것

`lapbar report` 는 `~/.claude/projects/` 의 transcript (jsonl) 를 읽어 요청마다 모델과 토큰 (input, output, cache read / write) 을 모으고 list price 로 값을 매긴다. 보여 주는 절감액은 반사실이다: **같은 서브에이전트 요청, 같은 토큰을 머리 모델 단가로 다시 매긴 값**. before / after 가 필요 없어서 설치 첫날부터 숫자가 나온다.

```
lapbar report                          # 전체 기간
lapbar report --since <설치한 날>       # hook 을 켠 뒤만
lapbar report --head claude-opus-5-5   # 추론된 머리 모델이 내 것이 아닐 때
lapbar report --json                   # 같은 리포트를 JSON 으로
```

말하는 것과 말하지 않는 것:

- 절감은 머리 모델과 서브에이전트가 돈 모델의 단가 차이다. fable 머리 + opus 서브에이전트는 input, output 에서 2.5배, cache read 에서 1.25배 차이. opus 머리 + opus 서브에이전트는 0. sonnet 머리 + opus 서브에이전트는 음수다 (리포트는 `cost $X more` 로 찍는다).
- 하네스가 없었어도 같은 호출을 했을 거라고 주장하지 않는다. 서브에이전트마다 컨텍스트를 새로 시작하므로 [전부 머리 모델이 인라인으로] 였다면 토큰이 이 반사실보다 적다. 티어 분할의 가치로 읽지, 절감 보장으로 읽지 않는다.
- 단가는 공개 list price (`src/lapbar/prices.json`, 모델 7개, 기준일 명시) × 토큰이라 **추정치** 다 (실청구가 아니다). 표에 없는 모델은 양쪽 다 $0 으로 빠진다. 1시간 TTL 캐시 쓰기는 input × 2 (추정). `--prices` 로 바꿀 수 있다.
- 같은 requestId 는 한 번만 센다 (한 응답이 content block 마다 레코드를 남긴다, output 이 가장 큰 레코드 채택). `<synthetic>` 은 제외. 머리/서브에이전트 분류는 파일 경로 (`<세션>.jsonl` vs `subagents/`) 기준이라 2026-10 이후 transcript 구조를 전제한다.
- 머리 모델은 `--head` 로 주거나, 없으면 머리 쪽에서 비용이 가장 큰 모델로 추론한다 (출력에 `inferred` 표시).

### 사례 하나: 저자의 숫자

머리 모델 fable, 서브에이전트 97% opus, Workflow 를 많이 쓴 사람의 hook 을 켠 뒤 10일 (2026-10-13 ~ 2026-10-22, `lapbar report --since 2026-10-13 --until 2026-10-22` 한 번 실행):

| 항목 | 값 |
|---|---|
| 요청 수 | 17,432 (머리 6,852, 서브에이전트 10,580, 그중 workflow 8,031) |
| 실제 비용 (list price 추정) | $3,100.75 |
| 같은 서브에이전트 요청을 머리 단가로 | $4,299.48 |
| **절감** | **$1,198.73** - 가정 비용 대비 27.9% |

머리 쪽 비용 ($1,930) 은 양쪽이 같으니 서브에이전트 몫만 보면 $2,369 에서 $1,171 로 (× 0.49). 서브에이전트 토큰의 94% 가 cache read 인데 거기서 fable / opus 격차는 1.25배뿐이라, 2.5배만큼 싸지지는 않는다.

## 설치 뒤 생기는 것

| 종류 | 파일 | 하는 일 |
|---|---|---|
| hook | `hooks/subagent-model-guard.py` (PreToolUse: Task, Agent, Workflow) | model, effort 리터럴 강제, 상위, 하위 티어는 표지 필요 (역할 모드면 haiku 는 역할 표지). `--self-test` 153 케이스 |
| hook | `hooks/git-push-guard.py` (PreToolUse: Bash) | develop / main / master push deny, fail-closed. `--self-test` 60 케이스 |
| hook | `hooks/memory-lint-hook.py` (SessionStart) + `scripts/memory-lint.py` | 메모리 디렉토리 점검, 문제 있을 때만 알림 |
| hook | `hooks/git-readonly-guard.py` (에이전트 전용 PreToolUse: Bash, `settings.json` 에는 안 들어감) | `scout-git` 에이전트 안에서 읽기 전용 git 만 통과시키는 allowlist: 파이프, 리다이렉트, 쓰기 서브커맨드, 모르는 옵션은 deny, `--no-ext-diff` 강제. `--self-test` 76 케이스 |
| rule | `rules/zsh-pitfalls.md` | Bash 툴의 zsh 에서 조용히 어긋나는 함정 모음 (실사고 기반) |
| rule | `rules/security.md` | 출력 위생 - 설정, env, wrapper 를 볼 때 값이 새지 않게 |
| rule | `rules/memory-scope.md` | 메모리를 어느 scope 에 둘지 판정하는 4 바구니 |
| rule | `rules/subagent-review.md` | 리뷰 레인의 규모, 모델 티어, 읽기 전용 원칙 |
| rule | `rules/git-workflow.md` | 브랜치, 커밋, worktree, push 권한 |
| skill | `commands/lane.md`, `commands/review-lane.md` + `scripts/*-workflow.js` | 레인 루프, 검수 레인 절차 |
| agent | `agents/review-readonly.md` | Read, Grep, Glob 만 가진 읽기 전용 리뷰어 (레인의 refute, review 역할) |
| agent | `agents/scout-git.md` | 계획 단계의 git 이력 정찰 ("이거 전에 있었나, 언제 왜 넣고 뺐나"). Bash 는 frontmatter 에 적힌 위 hook 이 읽기 전용 git 으로 묶는다 |

`lapbar install` 은 **기존 파일을 덮지 않는다** (내용이 다르면 건너뛰고 알려 준다, `--force` 로만 덮음). `settings.json` 은 쓰기 전에 `settings.json.bak-<시각>` 을 남기고, 같은 hook 배선이 이미 있으면 다시 넣지 않는다.

`lapbar doctor` 는 자산이 제자리에 있고 배선됐는지, hook 마다 `--self-test` 가 통과하는지에 더해, 가드마다 표본 입력을 넣어 실제로 deny / allow 하는지 (가드가 로드에 실패하면 조용히 가드 없이 진행되는 걸 잡으려고), 그리고 `settings.json` 의 모든 hook 명령 (lapbar 것이 아니어도) 이 실존하고 컴파일되는 파일을 가리키는지 본다.

`lapbar session` 은 세션 transcript 하나 (Claude Code 안에서 돌리면 현재 세션, 아니면 `--latest` 나 경로) 를 읽어 40줄 안에 hook 별 주입 글자 수 (머리 세션만, 서브에이전트 transcript 의 hook 은 아직 안 센다), 로드된 지시 파일과 크기, 서브에이전트 종류 × 모델 × effort, 양쪽 토큰을 보여 준다. 메모리를 정리하거나 rules 를 다듬기 전에 보라고 만든 것.

## 쓰는 법 (짧게)

전제: `Agent` 툴에 `effort` 파라미터가 있고 `Workflow` 툴이 있는 Claude Code (2026-10 기준 최신). 없는 판에서는 model 가드만 쓰고 `/lane`, `/review-lane` 은 건너뛴다.

1. `lapbar install` → Claude Code 재시작
2. 서브에이전트를 부를 때 model 과 effort 를 적는다 (fable, haiku 는 표지도 필요하다, 아래 참조). 안 적으면 hook 이 막고 이유를 보여 준다
3. 리뷰는 `/review-lane`, 긴 작업은 `/lane` 으로
4. 가끔 `lapbar report` 로 숫자를 ([무엇을 재나](#lapbar-report-가-재는-것)), `lapbar session` 으로 한 세션을 본다
5. 계획 중 "이거 전에 있었나?" 는 `scout-git` 에이전트에 맡긴다. git 이 읽기 전용이라 안전하다

가드가 무엇을 보는지, 표지, `settings.json` 의 선택 `env` 키 (effort 상한, haiku 역할 모드, 소유자 이름) 는 [model guard 자세히](#model-guard-자세히) 에 있다.

hook 메시지는 영어다. 규칙 파일과 `/lane`, `/review-lane` 절차는 한국어 (이 하네스를 쓴 사람의 작업 언어). 필요하면 복사해서 고쳐 쓰면 된다 - 그래서 `install` 이 덮어쓰기를 하지 않는다.

## model guard 자세히

`subagent-model-guard.py` 는 `Agent`, `Task`, `Workflow` 호출마다 먼저 돈다. 호출을 정적으로 읽어 (`Agent` 입력, 또는 Workflow 스크립트 본문) 통과시키거나, 무엇을 고치면 되는지 적은 사유와 함께 거부한다 (deny). 호출을 고쳐 주지는 않는다.

### 호출에 있어야 하는 것

| 항목 | 규칙 |
|---|---|
| `model` | 티어 이름 하나를 적은 문자열 리터럴. `opus`, `sonnet` 은 그대로 통과, `fable`, `haiku` 는 표지가 필요하다 (다음 표). `claude-opus-5-5` 같은 전체 ID 는 티어로 환산한다. `inherit`, 변수, 삼항식, 괄호식, 티어 낱말이 둘인 이름 (`opus-haiku`) 은 거부. 허용 티어는 hook 의 `TIERS` 표 하나에 있다 |
| `effort` | 소문자 리터럴 하나: `low`, `medium`, `high`, `xhigh`, `max`. `SUBAGENT_EFFORT_CAP` 위 단계는 표지가 필요하다 (기본 상한이면 `max` 가 그렇다). 생략하면 세션 effort 를 상속하므로 거부 |
| Workflow `agent()` opts | 평평한 객체 리터럴 하나. 삼항식으로 고른 객체, spread, 계산 키, getter / setter, 식별자 이스케이프, shorthand (`{effort}`) 는 거부. 키는 `{` 나 `,` 바로 뒤에서만 키로 세므로 `label: c ? model : 'x'` 의 `model :` 은 키가 아니다. 같은 키가 반복되면 JS 처럼 마지막 것 |
| fable 개수 | 표지가 있어도 Workflow 스크립트 하나에 fable `agent()` 호출 위치 2곳까지 |

### 표지

표지는 같은 호출 안의 평문이다: `Agent` / `Task` 는 `prompt`, Workflow `agent()` 는 괄호 안 어디든 (label, prompt, 주석도). hook 은 소유자가 정말 허용했는지 알 수 없다 - 허용 없이 표지를 적는 건 규칙 위반이지 hook 이 잡는 게 아니다.

| 표지 | 통과시키는 것 |
|---|---|
| `[owner-approved: fable]` | fable (2곳 상한 안에서) |
| `[owner-approved: haiku]` | haiku, 상한까지의 effort 로 (상한 위는 effort 표지가 따로 필요하다) |
| `[owner-approved: max]` (다른 단계도 같은 꼴) | 상한 위의 그 effort 단계 |
| `[haiku role: <기계적 일>]` **과** `[verify: <부모가 돌릴 검증>]` | 역할 모드가 켜져 있을 때 (다음 표) haiku 를 effort `medium` 또는 `high` 로. `low`, `xhigh`, `max` 는 거부 |

역할 모드는 부모가 기계적으로 검증하는 일 (테스트 결과 집계, lint 큐 정리, grep 요약, 체크섬 대조) 을 위한 것이다. 역할 범위는 medium 에서 high 까지: 그보다 생각이 필요한 일은 haiku 일이 아니다.

표지 안의 이름은 기본 `owner`, `SUBAGENT_GUARD_OWNER` 로 바꾼다 (`[alice-approved: fable]`).

### 설정 (`settings.json` 의 `env` 블록)

`lapbar install` 은 이 값을 쓰지 않는다. Claude Code 는 `settings.json` 의 `env` 블록을 hook 에 넘기지만 터미널 셸에는 안 실리므로, hook 의 `--self-test` 는 자기가 설치된 Claude 홈의 `settings.json` 에서 읽은 값을 찍어 준다 (폴백 전 원값 그대로).

| 키 | 기본값 | 효과 |
|---|---|---|
| `SUBAGENT_EFFORT_CAP` | `xhigh` | 표지 없이 허용되는 effort 상한: `low`, `medium`, `high`, `xhigh`, `max` 중 하나. 다른 값은 `xhigh` 로 돌아간다 |
| `SUBAGENT_HAIKU_ROLE_MODE` | off | `role` 이면 haiku 역할 표지가 켜진다, 다른 값은 off |
| `SUBAGENT_GUARD_OWNER` | `owner` | 허용 표지 안의 이름. `<자리표시자>` 그대로면 미설정 취급 |

소유자 이름을 바꾸면 설치된 규칙, 절차 파일의 `owner-approved` 도 같은 이름으로 고친다 (`rules/subagent-review.md`, `commands/lane.md`, `commands/review-lane.md`, `scripts/lane-workflow.js`. `grep -rl owner-approved ~/.claude` 로 전부 찾는다). 안 그러면 규칙이 hook 이 더는 받지 않는 표지를 쓰라고 안내한다.

```
"env": {
  "SUBAGENT_EFFORT_CAP": "xhigh",
  "SUBAGENT_HAIKU_ROLE_MODE": "role",
  "SUBAGENT_GUARD_OWNER": "alice"
}
```

### Workflow 스크립트에 쓰면 안 되는 것

hook 은 `agent()` 호출을 찾으려고 작은 상태 기계로 문자열과 주석을 지운다. JS 파서가 아니라서, 엉뚱한 자리에서 문자열이나 주석 상태를 열어 `agent()` 호출을 숨길 수 있는 꼴 몇 가지는 아예 거부한다. deny 사유마다 그 꼴과 고칠 방법이 적혀 있다.

| 거부하는 것 | 왜 | 대신 |
|---|---|---|
| JS 가 정규식으로 읽는 자리의 정규식 리터럴 `/…/`: 스크립트 맨 앞, 연산자나 `( [ { } , ; :` 뒤, spread 뒤, `return` 같은 키워드 뒤, `if` / `while` / `for` / `with` 헤더의 `)` 뒤 | 정규식 안의 따옴표나 주석 기호가 뒤 텍스트와 짝을 지어 그 사이가 통째로 지워진다 | `new RegExp('…')` |
| 나눗셈 뒤 같은 줄에 짝 없는 따옴표와 또 다른 `/` (`n / 2; const u = 'a/b'`), 또는 글자에 붙은 주석 (`x/2//c`) | 잘못 읽힌 정규식과 같은 모양이라, hook 이 문맥을 못 가르는 경우의 안전망 | 줄을 나누거나 주석 앞에 공백 |
| 문자열, 주석 밖의 백슬래시 | 이스케이프된 슬래시가 든 정규식 본문이거나 `\u006dodel` 같은 식별자 이스케이프 | `new RegExp('…')`, 평범한 식별자 |
| `'` / `"` 문자열 안의 이스케이프되지 않은 개행이나 CR (`\` 줄 이어 쓰기는 괜찮다), 어디든 U+2028 / U+2029, 코드 자리의 `<!--` 나 `-->` | JS 는 줄 끝이나 주석으로 보는데 hook 은 그러지 않는다 | 여러 줄 텍스트는 템플릿 리터럴, 구분자는 제거 |
| 대상이 문자열 리터럴이나 `{scriptPath: '<리터럴>'}` 이 아닌 `workflow()`, 직접 호출이 아닌 `workflow` | 자식 스크립트는 도구 호출로 hook 에 닿지 않으므로 hook 이 대상 파일 (`.claude/workflows/` 의 저장 이름, 또는 세션 cwd 기준 경로) 을 읽어 같은 방식으로 3단계까지 검사한다 | 리터럴 이름이나 경로 |
| 직접 호출이 아닌 `agent` (별칭, 인자, 속성), `eval` / `Function` / `globalThis` 경유 | 호출 지점을 셀 수 없다 | `agent(...)` 를 직접 호출 |

식별자, 숫자, `]`, 헤더가 아닌 `)` 뒤의 나눗셈은 나눗셈으로 읽는다: `Math.round(budget.remaining()/1000)`, `done / total + '%' // progress`, `a / b + ' of ' + c / d` 는 통과한다.

### 점검

`python3 ~/.claude/hooks/subagent-model-guard.py --self-test` 가 회귀 케이스 153개를 돌리고 읽은 env 값을 찍는다. `lapbar doctor` 는 설치된 사본으로 그 self-test 와 프로브 3개 (model 없음, 정상 호출, 표지 없는 haiku) 를 돈다. 지금까지 찾아 닫은 숨기기 꼴은 `CHANGELOG.md` 에 있다.

## 공개본에서 뺀 것

이 저장소의 rules, 레인 절차, `review-readonly` 에이전트는 한 사람의 라이브 `~/.claude` 에서 **`lapbar bundle`** 로 추출한 것이고, hook 과 `scout-git` 에이전트는 여기서 직접 쓴다 (manifest 의 `keep` 표지, `bundle` 이 덮지 않는다). 치환 표 (`scrub_map.json`) 는 원문을 담고 있어 저장소 밖 (`$LAPBAR_SCRUB_MAP`) 에만 있고, 공개 코드에는 digest 로만 들어 있다. 추출 때 호칭, 실명, 계정, 홈 경로, 프로젝트와 회사 이름, 벤더, wrapper 이름, IP, 토큰 패턴을 치환하거나 뺐다. `lapbar bundle --check` 가 게이트인데, CI 에서는 범용 패턴 (홈 경로, IP, JWT, 자격증명 대입, 메모리 파일 이름) 만 보고, 고유명사 (digest 로만 보관) 와 호칭은 관리자 로컬에서 `--require-map` 으로 확인한다. 외부 접근 기록 hook (trust-ledger) 은 회사 wrapper 표와 결합이 커서 다음 판에 넣는다.

## 개발

```
git clone https://github.com/mysoul7306/lapbar && cd lapbar
python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m lapbar.cli --help
```

의존성 0 (Python 3.11+ 표준 라이브러리만).

## 라이선스

**MIT** - 저작권 표기만 남기면 자유롭게 쓰고 고치고 나누고 팔 수 있다. 전문은 `LICENSE`.

**왜 공개했나** - 비싼 모델은 머리로, 싼 모델은 손발로 쓰는 건 그냥 합리적인 소비다. 합리적인 습관은 누구나 묻지 않고 가져갈 수 있어야 해서 공개했고, 라이선스도 MIT 로 뒀다.

## Credits

- Author and copyright: **rok_root** (license: MIT)
- Co-author: **Claude** (Anthropic) - the hooks, rules and this tool were built together in Claude Code sessions; commits carry a `Co-Authored-By: Claude` trailer.
