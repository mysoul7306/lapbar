# Subagent 리뷰, 검증 레인 (CRITICAL)

> 모델 티어의 정본 = hook `~/.claude/hooks/subagent-model-guard.py` (GUIDE). 이 파일은 hook 이 못 막는 운용 규칙까지 적는다.

## 리뷰어는 읽기 전용, 실측은 분리
- 리뷰, 반증, critic 레인은 사용자 agent **`review-readonly`** (Read, Grep, Glob 만) 로 띄운다 — Workflow 는 `agentType: 'review-readonly'`, 단건 Agent/Task 는 `subagent_type: 'review-readonly'`. Bash 가 없어 셸 실행 권한 프롬프트가 생기지 않는다 (Read 권한 질문은 남을 수 있다).
- **실측 요구는 유지하고 실행 담당만 나눈다**: 리뷰어가 [실측 요청] (명령, 실행 환경과 전제, 부수효과, 성공/실패 판별) 을 설계해 돌려주면, 부모가 검토한 뒤 직접 (또는 허용 명령을 명시한 sonnet 1개 레인이) 돌리고 결과를 리뷰어에게 되돌린다. 리뷰어 직접 실측은 격리 환경 (컨테이너, 전용 홈) 을 **사용자가 미리 허용한 작업**에서만 general-purpose 로.
- 2차 의견은 (Codex 가 설치돼 있으면) Codex read-only sandbox 를 Claude 검증자보다 먼저 (Claude 한도를 쓰지 않는다).

## 한 리뷰 라운드의 규모
- 렌즈 ≤ 3, critic ≤ 1 (opus, fable 은 `[owner-approved: fable]` 표지 때만). 검증은 **지적별 독립 인원** high 2, medium 1, low 0 (low 는 부모 판단). 검증자 1명이 지적 ≤ 4건을 묶어 본다.
- 렌즈 프롬프트에 **출력 상한** 을 적는다 — 상한 = (10 - 렌즈 - critic) × 4 ÷ 렌즈 수 (렌즈 3 + critic 1 이면 렌즈당 8건, 심각도순). 상한 없는 렌즈 3개가 103건을 내면 반증 라운드가 예산을 넘겨 부모가 손으로 판정하게 된다 (실사고 2026-10-17, 라운드 2 는 렌즈당 ≤ 12 로 전부 검증)
- 라운드 합계 **agent ≤ 10** (단건 리뷰, 재시도, 후속 검증 포함, workflow 를 쪼개도 예산은 하나). 넘길 이유가 있으면 돌리기 전에 agent 수와 예상 토큰을 사용자에게 예고하고 사인.
- 다음 라운드 생략 조건 = 반증 결과가 [고칠 목록] 으로 닫혀 있고 **판단 여지가 없고 기계적으로 검증 가능** (테스트, 잔존 grep 0) 할 때만. 공유 동작 (파서, 권한, 배포 경로) 을 바꾸는 수정은 그 범위만 다시 리뷰.

## 모델 티어 (hook 이 막는 것)
- **루프 = 머리 세션 모델, 손발 하위 모델**: 부모 (세션 모델) 가 해독, 분해, 종합, 위험 결정의 사용자 확인을 맡고, 레인은 read = opus high, collect = sonnet low (허용 명령 목록이 있을 때만 셸), refute = opus xhigh, 취합 압축 = opus (여기 read 는 렌즈이고, 아래 GUIDE 의 judge·critic xhigh 는 최종 판정자 자리 - 다른 것). 모델 하나로 강제하지 않는다. 절차 = `/lane`, 검수 = `/review-lane`
- 모든 Agent/Task 와 workflow `agent()` 에 model 을 **문자열 리터럴**로 (생략, inherit, 변수, 삼항식 deny). 단건 = opus, workflow = opus 기본, sonnet = 기계적 수집·파싱.
- fable 은 사용자가 그 작업에 명시 허용했을 때만 — 그 호출 안 (label/prompt) 에 `[owner-approved: fable]` 표지, 표지가 있어도 workflow 한 스크립트에 호출 위치 2곳까지 (3곳부터 deny). 루프 1곳이 N번 도는 건 hook 이 못 세니 규모 예고로.
- haiku 는 사용자가 그 작업에 명시 허용했을 때만 — 그 호출 안 (label/prompt) 에 `[owner-approved: haiku]` 표지. 허용 없이 표지를 적는 건 규칙 위반. hook 이 거부하면 우회하지 말고 고쳐서 다시 부른다.
- **effort 도 리터럴로** — Agent/Task 의 `effort`, workflow `agent()` opts 최상위의 `effort:` 는 low/medium/high/xhigh (max 는 `[owner-approved: max]` 표지) 중 하나의 소문자 문자열 리터럴 (생략 = 세션 effort 상속 → deny, 변수·삼항식·shorthand deny). opts 는 평평한 객체 리터럴 하나 (삼항식으로 고른 객체, spread, 계산 키, get/set 은 deny — 런타임 값이 hook 이 읽은 리터럴과 달라진다). 배분 = 작업 agent `high`, 기계적 수집·파싱 `low`, judge·critic `xhigh`. 상한 (기본 `xhigh`, settings.json `env.SUBAGENT_EFFORT_CAP` 로 조절) 위 단계는 그 호출 안에 `[owner-approved: <단계>]` 표지가 있을 때만 (`max` 는 `[owner-approved: max]`). hook self-test 74.
