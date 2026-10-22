# beacon 🔦

**비싼 모델을 머리로, 싼 모델을 손발로 — hook 이 강제하는 Claude Code 하네스와, 그게 얼마나 아꼈는지 보여 주는 리포트.**

> 한 줄 요약 (English below): `beacon install` puts a small set of Claude Code hooks, rules and skills into `~/.claude`; `beacon report` reads your own transcripts and shows how much cheaper your subagents were than running everything on the head model.

```
pipx install claude-beacon        # or: uv tool install claude-beacon
beacon install --dry-run          # what would be installed, nothing written
beacon install                    # hooks + rules + skills + agent into ~/.claude, settings.json wired (backup kept)
beacon doctor                     # everything in place, hook self-tests pass
beacon report --since 2026-10-13  # your own savings, from your own transcripts
```

## 왜 만들었나

Claude Code 에서 가장 비싼 건 **머리 모델이 손발 일까지 하는 것** 이다. 파일 훑기, 리뷰 렌즈, 수집 같은 서브에이전트 작업은 더 싼 모델로도 품질이 같은데, `Agent` / `Workflow` 호출에 model 을 안 적으면 머리 모델을 그대로 상속한다. 그래서 hook 으로 막았다.

- **model, effort 는 리터럴로** — `subagent-model-guard.py` 가 `Agent`, `Task`, `Workflow` 호출에서 model 생략, 변수, 삼항식을 거부하고 (deny), 최상위 모델은 그 호출 안에 `[owner-approved: fable]` 표지가 있을 때만 허용한다. effort 도 같은 규칙 (low / medium / high / xhigh 리터럴).
- **레인 루프** — `/lane` (머리는 해독, 분해, 종합만; read 는 opus high, collect 는 sonnet low, refute 는 opus xhigh) 과 `/review-lane` (렌즈 3 → 반박자 2, agent 상한 10) 이 그 배분을 절차로 굳힌다.
- **보호 브랜치 push 차단** — `git-push-guard.py` 가 develop / main / master 로 가는 push 를 refspec, upstream 까지 풀어 판정하고 못 풀면 fail-closed 로 막는다. 머지는 AI 가, push 는 사람이.
- **메모리 위생** — `memory-lint-hook.py` 가 세션 시작 때 메모리 디렉토리의 깨진 링크, 공인 IP, 비밀값, 라우터 크기를 점검한다.

## 얼마나 아끼나

`beacon report` 는 `~/.claude/projects/` 의 transcript (jsonl) 를 읽어 요청마다 모델과 토큰 (input, output, cache read / write) 을 모으고, **서브에이전트 요청 전부를 머리 모델 단가로 다시 계산한 가정 비용** 과 실제 비용을 비교한다. before / after 가 필요 없는 반사실 계산이라 설치 첫날부터 숫자가 나온다.

이 하네스를 만든 사람의 실측 (hook 을 켠 2026-10-13 부터 10일, `beacon report --since 2026-10-13`, 2026-10-22 밤 실행):

| 항목 | 값 |
|---|---|
| 요청 수 | 15,777 (머리 6,415, 서브에이전트 9,362, 그중 workflow 8,031) |
| 실제 비용 (list price 추정) | $2,514.70 |
| 서브에이전트까지 전부 머리 모델이었다면 | $3,520.82 |
| **절감** | **$1,006.12 (28.6%)** |
| 서브에이전트 effort | high 65%, xhigh 33%, medium 2% |

서브에이전트 요청의 97% 가 opus 로 돌았고 (fable 2%, opus 4.8 1%), 그 차액이 전부다. 머리 쪽 비용 ($1,507) 은 건드리지 않은 숫자라 "총액의 28.6%" 는 보수적인 표현이다 (서브에이전트 몫만 보면 $1,008 → $2,014, 절반).

- 단가는 공개 list price (`src/beacon/prices.json`, 기준일 명시) × 토큰이라 **추정치** 다 (실청구가 아니다). `--prices` 로 바꿀 수 있다.
- 같은 requestId 는 한 번만 센다 (한 응답이 content block 마다 레코드를 남긴다). `<synthetic>` 은 제외.
- 자기 숫자를 보려면 `beacon report --json > report.json` 으로 뽑아 두면 된다.

## 설치 뒤 생기는 것

| 종류 | 파일 | 하는 일 |
|---|---|---|
| hook | `hooks/subagent-model-guard.py` (PreToolUse: Task, Agent, Workflow) | model, effort 리터럴 강제, 상위 티어는 표지 필요. `--self-test` 74 케이스 |
| hook | `hooks/git-push-guard.py` (PreToolUse: Bash) | develop / main / master push deny, fail-closed. `--self-test` 37 케이스 |
| hook | `hooks/memory-lint-hook.py` (SessionStart) + `scripts/memory-lint.py` | 메모리 디렉토리 점검, 문제 있을 때만 알림 |
| rule | `rules/zsh-pitfalls.md` | Bash 툴의 zsh 에서 조용히 어긋나는 함정 모음 (실사고 기반) |
| rule | `rules/security.md` | 출력 위생 — 설정, env, wrapper 를 볼 때 값이 새지 않게 |
| rule | `rules/memory-scope.md` | 메모리를 어느 scope 에 둘지 판정하는 4 바구니 |
| rule | `rules/subagent-review.md` | 리뷰 레인의 규모, 모델 티어, 읽기 전용 원칙 |
| rule | `rules/git-workflow.md` | 브랜치, 커밋, worktree, push 권한 |
| skill | `commands/lane.md`, `commands/review-lane.md` + `scripts/*-workflow.js` | 레인 루프, 검수 레인 절차 |
| agent | `agents/review-readonly.md` | Read, Grep, Glob 만 가진 읽기 전용 리뷰어 (레인의 refute, review 역할) |

`beacon install` 은 **기존 파일을 덮지 않는다** (내용이 다르면 건너뛰고 알려 준다, `--force` 로만 덮음). `settings.json` 은 쓰기 전에 `settings.json.bak-<시각>` 을 남기고, 같은 hook 배선이 이미 있으면 다시 넣지 않는다.

## 쓰는 법 (짧게)

1. `beacon install` → Claude Code 재시작
2. 서브에이전트를 부를 때 model 과 effort 를 적는다. 안 적으면 hook 이 막고 이유를 보여 준다
3. 리뷰는 `/review-lane`, 긴 작업은 `/lane` 으로
4. 가끔 `beacon report` 로 숫자를 본다

규칙 파일은 한국어다 (이 하네스를 쓴 사람의 작업 언어). 필요하면 복사해서 고쳐 쓰면 된다 — 그래서 `install` 이 덮어쓰기를 하지 않는다.

## 공개본에서 뺀 것

이 저장소의 hook, rules 는 한 사람의 라이브 `~/.claude` 에서 **`beacon bundle`** 로 추출한 것이다. 치환 표 (`scrub_map.json`) 는 원문을 담고 있어 저장소 밖 (`$BEACON_SCRUB_MAP`) 에만 있고, 공개 코드에는 digest 로만 들어 있다. 추출 때 호칭, 실명, 계정, 홈 경로, 프로젝트와 회사 이름, 벤더, wrapper 이름, IP, 토큰 패턴을 치환하거나 뺐고, `beacon bundle --check` 가 CI 게이트로 그 흔적 0건을 확인한다. 외부 접근 기록 hook (trust-ledger) 은 회사 wrapper 표와 결합이 커서 다음 판에 넣는다.

## 개발

```
git clone https://github.com/mysoul7306/beacon && cd beacon
python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m beacon.cli --help
```

의존성 0 (Python 3.11+ 표준 라이브러리만). 라이선스 MIT.

## English summary

`beacon` ships a small Claude Code harness whose point is cost: an expensive head model does the thinking, and hooks force every subagent / workflow call to name a cheaper model and an explicit effort level (deny on omission, approval tag required for the top tier). It also blocks pushes to protected branches and lints your memory directory on session start. `beacon report` scans your own transcripts, dedupes by request id, prices every request at list price, and shows the counterfactual: what it would have cost had every subagent run on the head model. Rules are in Korean (the author's working language); install never overwrites your files.

## Credits

- Author and copyright: **rok_root**
- Co-author: **Claude** (Anthropic) — the hooks, rules and this tool were built together in Claude Code sessions; commits carry a `Co-Authored-By: Claude` trailer.
