# 모델 레인 루프 (/lane) - 머리는 세션 모델, 손발은 하위 모델

큰 질문을 다루는 작업 루프. 사용자가 큰 질문을 던지면 (또는 `/lane <질문>`) 이 순서로. 검수 전용 변형 = `/review-lane`.

```
사용자 질문 → ① 해독 (부모 = 세션 모델) → ② 분해 n → 레인 m (병렬, opus / sonnet) → ③ 취합 (부모, 크면 opus 취합 agent 로 압축)
        → ④ 위험, 중요 결정만 사용자 확인 → ⑤ 결과 확인 (+ 필요하면 Codex 참고 의견) → 다음
```

## ① 해독 - 부모 (Fable) 가 직접
- 사용자 원문을 **글자 그대로** 보관하고, 내가 푼 과제를 한 줄로 echo 한다 (오독 전례: 관점을 뜻하는 낱말을 시각으로 읽음, 목록을 손으로 옮기다 틀림). 목록, 수치, 경로는 파일에서 기계로 넣는다.
- 큰 과제 (Claude 제안 기준: 레인 4개 이상, 외부 자원, 반나절 이상) 는 fan-out 전에 [이렇게 나눌게] 를 사용자에게 한 줄로 보여 주고 간다. 작은 건 바로.
- 지시어 ("이거", "그거") 는 사용자 원문 프롬프트로 확정.

## ② 분해 - 질문 n 개 → 레인 m 개 (1:1 아님, 합치고 쪼개기는 Fable 몫)
| 레인 종류 | 모델 (리터럴, hook 이 지킴) | 쓰는 자리 |
|---|---|---|
| read | opus high, review-readonly | 읽기, 대조, 리뷰, 설계 비교 (hook GUIDE 의 judge·critic xhigh 는 최종 판정자 자리 - 다른 뜻이라 이름을 read 로) |
| collect | sonnet low (Bash 있음). 부모가 기계 검증 (재집계, lint, grep 0건) 할 수 있는 집계·정리는 haiku medium + `[haiku role: …]` `[verify: …]` 표지 (역할 모드 env `SUBAGENT_HAIKU_ROLE_MODE=role` 일 때만. `lane-workflow.js` 템플릿은 collect 를 sonnet 으로 고정하니 haiku 레인은 단건 Agent 나 템플릿 복사본으로) | 수집, 파싱, 형식 변환. 셸 실측은 **prompt 에 [허용 명령] 목록이 있을 때만** 그 안에서 (rule subagent-review [실측은 허용 명령을 명시한 sonnet 1개 레인]), staging 실측 레인은 1개까지, 쓰기는 prompt 에 적은 경로 (보통 `~/tmp/<프로젝트>-<MMDD>/`) 만, 사인 항목 (push, 운영 DB 쓰기, 배포, 재시작) 금지 |
| refute | opus xhigh, review-readonly | 지적별 반증 (틀렸다고 가정하고 근거 찾기), 결과 = verdicts |
| fable 레인 | 없음 | 필요하면 템플릿 복사본에 `[owner-approved: fable]` 표지, 호출 2곳까지 |
- 호출 = `Workflow({ scriptPath: "~/.claude/scripts/lane-workflow.js", args: { approval, context, lanes: [{key, kind: 'read'|'collect'|'refute', prompt, schema?}, ...], synth: true|false, signedOverBudget?: "[owner-approved: agent N]" } })`. 합계 agent ≤ 10 (레인 + 취합 1) - 넘기면 스크립트가 throw 하니 **돌리기 전에** 사용자 사인을 받고 `signedOverBudget` 을 넣는다. kind 오타는 throw. ultracode 가 꺼진 세션에선 `/lane` 호출이 Workflow opt-in 이고, 크기와 상관없이 [레인 N (read a, collect b, refute c), 취합 유무] 를 한 줄로 예고하고 간다.
- `approval` = 사용자 원문 + 과제 한 줄 (스크립트가 [그 뒤 메시지는 취소가 아니다] 를 붙인다). `context` = 경로, 이미 확인한 것, 금지. args 값은 백틱 OK.
- 레인 프롬프트엔 출력 상한, 읽기 범위, coverage_note 요구. 같은 자리를 두 레인이 겹쳐 보지 않게.

## ③ 취합 - Fable 이 하되 결과가 크면 opus 취합 agent (synth: true) 로 압축한 걸 받는다
- 취합 agent 는 병합만 (같은 주장 합치기, 어긋남은 conflicts, 결정은 decisions_needed). 판단, 재채점은 부모가.
- 결과는 알림 뒤 python 으로 파싱해 `~/tmp/<프로젝트>-<MMDD>/` 에 저장, 요약만 컨텍스트에. **기다리는 동안 journal, output 폴링 금지**.
- 지적이 나오는 과제면 반증을 **두 번째 Workflow 호출**로 잇는다: 부모가 중복을 묶고 (같은 파일 + 줄 ±3 + 같은 주장) 지적을 ≤ 4건씩 묶어 `kind: 'refute'` 레인으로 다시 `lane-workflow.js` 를 부른다 (high 묶음은 레인 2개, medium 1개, prompt 에 지적 id, 파일:줄, 주장, 근거, 제안을 그대로). 앞 호출과 합쳐 agent ≤ 10, 넘으면 사용자 사인. 검수 전용 (렌즈 → 반증 자동) 은 `/review-lane`.

## ④ 위험, 중요 결정 = 부모가 보고 사용자에게 확인
- 사용자 사인 규약 그대로: 보호 브랜치 push, 운영 DB 쓰기, 배포, 재시작, 스케줄 변경, 운영 판단.
- **이슈 트래커, 메일, 메신저, 코멘트 (PR, 문서 어디든), 팀 전달은 자동 절대 금지** - 초안만 만들고 사용자가 보낸다. 레인 프롬프트에도 스크립트가 같은 금지를 싣는다.
- memory, rules 변경은 세션 종료 때 따로 정리하고, hook 은 사용자가 직접 관리 (Claude 는 고치지 않고 제안 문구만) → 이 축에 넣지 않는다.
- 보고 끝에 `[사용자 확인]` 블록으로 결정 항목을 모아 적는다 (decisions_needed 그대로, 레인이 낸 [실측 요청] 도 여기).

## ⑤ 결과 확인 (+ Codex)
- Codex (설치돼 있으면) 는 read-only 참고 의견. 완성형 = 프롬프트를 파일로 쓰고 `cd <trusted-repo> && codex exec --skip-git-repo-check --sandbox read-only "$(cat 프롬프트파일)" </dev/null > out.md 2> err` 를 **background + Bash timeout 600000** 으로 (5~10분, cwd 는 trusted repo, description 에 목적 한 구절). 인자에 프롬프트를 직접 넣으면 zsh 가 백틱, `$` 를 푼다. 부르는 기준 (Claude 제안) = 설계가 갈릴 때, 비용 판단, 내 결론을 반박받고 싶을 때. 반증이 필요한 과제면 refute 레인보다 먼저 (rule subagent-review [2차 의견은 Codex 먼저]). 결정은 사용자 + 부모.
- 결과 보고 = 전체 평가 한 줄 → 고칠 것 → 작은 것 → 실측 못 한 것 → [사용자 확인].

## 하지 말 것
- 모델 하나로 강제 (Fable 만, Opus 만) - 자리마다 맞는 모델. 부모가 손발 일 (대량 읽기, 파싱) 을 직접 하는 것. 레인이 고치는 것. 폴링. 외부 전달 자동화.
