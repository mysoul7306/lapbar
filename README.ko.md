> 🌐 **English**: [README.md](README.md)

# lapbar 🎢

*롤러코스터 무릎 안전바 (lap bar) 에서 따온 이름 - 자리에 붙잡아 주는 장치이지 가두는 우리가 아니다.*

**비싼 모델을 머리로, 싼 모델을 손발로 - hook 이 강제하는 Claude Code 하네스와, 그게 얼마나 아꼈는지 보여 주는 리포트.**

> 한 줄 요약: `lapbar install` 이 Claude Code hook, rules, skills 몇 개를 `~/.claude` 에 깔고, `lapbar report` 가 내 transcript 를 읽어 서브에이전트를 머리 모델로 다 돌렸을 때보다 얼마나 싸게 했는지 보여 준다.

## 설치

```
# PyPI (준비 중)
pipx install lapbar               # or: uv tool install lapbar

# 소스에서 (지금 동작)
git clone https://github.com/mysoul7306/lapbar && cd lapbar
pipx install .                    # or: pip install .
```

그 다음:

```
lapbar install --dry-run          # what would be installed, nothing written
lapbar install                    # hooks + rules + skills + agent into ~/.claude, settings.json wired (backup kept)
lapbar doctor                     # everything in place, hook self-tests pass
lapbar report --since 2026-10-13  # your own savings, from your own transcripts
lapbar session                    # one session: hook injection, rules loaded, subagent models, tokens
```

## 왜 만들었나

Claude Code 에서 가장 비싼 건 **머리 모델이 손발 일까지 하는 것** 이다. 파일 훑기, 리뷰 렌즈, 수집 같은 서브에이전트 작업은 더 싼 모델로도 품질이 같은데, `Agent` / `Workflow` 호출에 model 을 안 적으면 머리 모델을 그대로 상속한다. 그래서 hook 으로 막았다.

- **model, effort 는 리터럴로** - `subagent-model-guard.py` 가 `Agent`, `Task`, `Workflow` 호출에서 model 생략, 변수, 삼항식을 거부하고 (deny), 최상위 모델 (fable) 과 최하위 모델 (haiku, 품질 사고 전례로 기본 차단) 은 그 호출 안에 `[owner-approved: <모델>]` 표지가 있을 때만 허용한다. effort 도 같은 규칙 (low / medium / high / xhigh 리터럴). 허용 모델 이름은 hook 의 `TIERS` 표 하나에서 바꾼다.
- **레인 루프** - `/lane` (머리는 해독, 분해, 종합만; read 는 opus high, collect 는 sonnet low, refute 는 opus xhigh) 과 `/review-lane` (렌즈 3 → 반박자 2, agent 상한 10) 이 그 배분을 절차로 굳힌다.
- **보호 브랜치 push 차단** - `git-push-guard.py` 가 develop / main / master 로 가는 push 를 refspec, upstream 까지 풀어 판정하고 못 풀면 fail-closed 로 막는다. 머지는 AI 가, push 는 사람이.
- **메모리 위생** - `memory-lint-hook.py` 가 세션 시작 때 메모리 디렉토리의 깨진 링크, 공인 IP, 비밀값, 라우터 크기를 점검한다.

두 가드는 **정규식 기반** 이다 - AI 가 실수로 push 하거나 model 을 빠뜨리는 걸 막는 안전장치이지, 작정한 우회 (eval, 설정 주입, 셸 트릭) 를 전부 막는 샌드박스가 아니다. 알려진 우회 꼴은 self-test 케이스로 하나씩 닫고 있고 (push 60, model 80), AST 파서 전환은 다음 판 과제.

## 얼마나 아끼나

`lapbar report` 는 `~/.claude/projects/` 의 transcript (jsonl) 를 읽어 요청마다 모델과 토큰 (input, output, cache read / write) 을 모으고, **서브에이전트 요청 전부를 머리 모델 단가로 다시 계산한 가정 비용** 과 실제 비용을 비교한다. before / after 가 필요 없는 반사실 계산이라 설치 첫날부터 숫자가 나온다.

이 하네스를 만든 사람의 실측 (hook 을 켠 2026-10-13 부터 10일, `lapbar report --since 2026-10-13`, 2026-10-22 밤 실행):

| 항목 | 값 |
|---|---|
| 요청 수 | 16,051 (머리 6,501, 서브에이전트 9,550, 그중 workflow 8,031) |
| 실제 비용 (list price 추정) | $2,897.65 |
| 서브에이전트까지 전부 머리 모델이었다면 | $3,929.62 |
| **절감** | **$1,031.97** - 가정 비용 대비 26.3%, 실제 지출 대비 35.6% |
| 서브에이전트 effort | high 65%, xhigh 33%, medium 2% |
| 캐시 쓰기 중 1시간 TTL 비중 | 40% (1시간 쓰기는 input 단가 × 2 로 계산) |

서브에이전트 요청의 97% 가 opus 로 돌았고 (fable 2%, opus 4.8 1%), 절감액은 그 단가 차이다. 머리 쪽 비용 ($1,507 안팎) 은 건드리지 않은 숫자라 서브에이전트 몫만 보면 비용이 절반 아래로 내려간다.

- 단가는 공개 list price (`src/lapbar/prices.json`, 기준일 명시) × 토큰이라 **추정치** 다 (실청구가 아니다). `--prices` 로 바꿀 수 있다.
- 같은 requestId 는 한 번만 센다 (한 응답이 content block 마다 레코드를 남긴다, output 이 가장 큰 레코드 채택). `<synthetic>` 은 제외. 머리/서브에이전트 분류는 파일 경로 (`<세션>.jsonl` vs `subagents/`) 기준이라 2026-10 이후 transcript 구조를 전제한다.
- 머리 모델은 `--head` 로 주거나, 없으면 머리 쪽에서 비용이 가장 큰 모델로 추론한다 (출력에 `inferred` 표시).
- 자기 숫자를 보려면 `lapbar report --json > report.json` 으로 뽑아 두면 된다.

## 설치 뒤 생기는 것

| 종류 | 파일 | 하는 일 |
|---|---|---|
| hook | `hooks/subagent-model-guard.py` (PreToolUse: Task, Agent, Workflow) | model, effort 리터럴 강제, 상위 티어는 표지 필요. `--self-test` 80 케이스 |
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
2. 서브에이전트를 부를 때 model 과 effort 를 적는다. 안 적으면 hook 이 막고 이유를 보여 준다
3. 리뷰는 `/review-lane`, 긴 작업은 `/lane` 으로
4. 가끔 `lapbar report` 로 숫자를, `lapbar session` 으로 한 세션을 본다
5. 계획 중 "이거 전에 있었나?" 는 `scout-git` 에이전트에 맡긴다. git 이 읽기 전용이라 안전하다

규칙 파일은 한국어다 (이 하네스를 쓴 사람의 작업 언어). 필요하면 복사해서 고쳐 쓰면 된다 - 그래서 `install` 이 덮어쓰기를 하지 않는다.

## 공개본에서 뺀 것

이 저장소의 hook, rules 는 한 사람의 라이브 `~/.claude` 에서 **`lapbar bundle`** 로 추출한 것이다. 치환 표 (`scrub_map.json`) 는 원문을 담고 있어 저장소 밖 (`$LAPBAR_SCRUB_MAP`) 에만 있고, 공개 코드에는 digest 로만 들어 있다. 추출 때 호칭, 실명, 계정, 홈 경로, 프로젝트와 회사 이름, 벤더, wrapper 이름, IP, 토큰 패턴을 치환하거나 뺐다. `lapbar bundle --check` 가 게이트인데, CI 에서는 범용 패턴 (홈 경로, IP, JWT, 자격증명 대입, 메모리 파일 이름) 만 보고, 고유명사 (digest 로만 보관) 와 호칭은 관리자 로컬에서 `--require-map` 으로 확인한다. 외부 접근 기록 hook (trust-ledger) 은 회사 wrapper 표와 결합이 커서 다음 판에 넣는다.

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

- Author and copyright: **Geunrok Kim** (license: MIT)
- Co-author: **Claude** (Anthropic) - the hooks, rules and this tool were built together in Claude Code sessions; commits carry a `Co-Authored-By: Claude` trailer.
