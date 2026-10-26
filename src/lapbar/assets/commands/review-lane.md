# 검수 레인 (/review-lane) - 절차로 깊이를 확보

사용자가 "꼼꼼히 봐줘", "검수해줘", "정리된 거 확인해줘", `/review-lane <대상>` 하면 이 절차로. 큰 memory 정리, 문서 묶음, 인계 파일, 설정 변경, 코드 diff 에 쓴다. 한 프로젝트의 memory 정리 검수 (지적 24, 반증 뒤 medium 11 → 4, low 강등 7) 에서 확립했다. 깊이가 모델에서 오는지 절차에서 오는지는 아직 실험 중이라 여기선 절차만 고정한다.

## 순서 (건너뛰지 않는다)
1. **대상 고정** - 무엇이 [최근에 바뀐 것] 인지 mtime, git log, 백업 파일로 목록화해 파일로 저장하고, 프롬프트의 목록은 그 파일에서 기계로 넣는다 (`$(cat 목록)`). 손으로 옮겨 적지 않는다 (손으로 옮기다 틀린 전례). 사용자의 요청 원문은 글자 그대로 보관 (의미를 바꿔 읽지 않는다 - 관점을 뜻하는 낱말을 시각으로 오독한 전례).
2. **기계 점검 먼저 (부모가 직접, python)** - memory 면 `python3 ~/.claude/scripts/memory-lint.py ~/.claude/projects/<프로젝트 디렉토리>/memory ~/.claude/memory --style --desc-dates --archive --days <대상 중 가장 오래된 파일까지의 일수> --allow-ip "$(python3 -c 'import json,os;p=os.path.expanduser("~/.claude/memory-lint.json");print(",".join(json.load(open(p)).get("allow_ip",["8.8.8.8"]) if os.path.exists(p) else ["8.8.8.8"]))')"` (hook 과 같은 허용 IP 목록) (`--days` 기본 2 는 문체, frontmatter, wikilink 를 최근 파일만 보고, `--archive` 없으면 archive 의 IP, 비밀값을 안 본다). 문서, 코드면 그에 맞는 grep, diff, 링크 검사. 결과가 전부 0건이면 [명령이 돌았는지] 부터 의심 (zsh-pitfalls §4-1). 여기서 잡은 건 렌즈 프롬프트에 [이미 확인한 것] 으로 적어 중복 지적을 막는다.
3. **렌즈 ≤ 3 으로 역할 분리** - 쓴 사람 ≠ 검수자. 기본 렌즈 3 (10/20, 두 부모 비교 뒤 Codex 제안 채택) = [사실과 행동 정합 (같은 사실이 두 곳에 다르게, hook·description 이 틀린 행동을 유도하나)] [상태 전이와 인계 보존 (열린 일이 기준선, 라우터에 이어졌나, 닫힌 일에 표지가 있나)] [접근성과 교훈 재사용 (죽은 경로, 폐지 포인터, 교훈의 feedback 파일화)]. 한 지적 = 한 파일, 한 주장 (복합 주장은 쪼갠다). severity 공통 기준 = medium: 라우터, description, hook 이 틀린 행동 (회신, 배포, 삭제, 재작업) 을 유도 / low: 본문 stale 에 표지 없음, 죽은 경로 / info: 형식, 문체. 대상이 memory 가 아니면 부모가 렌즈를 다시 설계한다 (무엇과 무엇을 대조하나, 읽기 범위, 출력 상한 8건, coverage_note 요구).
4. **지적별 독립 반증** - 그 전에 렌즈 사이 중복 (같은 파일 + 줄 ±3 + 같은 주장) 을 부모가 묶는다 (10/19 밤 두 팔 모두 같은 이슈가 2~3 렌즈에서 나와 반증 자리를 먹었다). high 는 2명, medium 은 1명, 한 명이 ≤ 4건. 프롬프트 기본 태도 = [틀렸다고 가정하고 근거 찾기]. 반증자는 severity 를 다시 매긴다.
5. **부모가 재채점, 그 다음에야 수정** - 고치는 건 confirmed 만 (high 는 2명 다 confirmed, 갈리거나 uncertain 이면 부모가 원문을 열어 판정). `unverified_ids` 와 low 는 부모가 원문을 확인한 것만. 고칠 땐 치환 건수를 단언 (python `assert count == 1`) 하고 archive 백업 (`archive/<파일>-<날짜>-before-<검수명>.bak.md`) 을 남긴다. 실측 요청은 부모가 돌리고 (외부 접근은 목적을 적고 진행) 결과를 기록에 되돌린다.
6. **보고** = 전체 평가 한 줄 → 고칠 것 (medium 이상, 근거와 줄) → 작은 것 묶음 → 실측 못 한 것 → 사용자 결정 항목. 검수 중 부모가 렌즈 밖에서 찾은 것은 따로 표시 (실험 지표).

## Workflow 호출 (3~5 단계)
`Workflow({ scriptPath: "~/.claude/scripts/review-lane-workflow.js", args: { target, context, lenses: [{key, prompt}, ...], verifierBudget: 7 } })`
- `target` = 사용자의 요청 원문 그대로 (스크립트가 [이 작업은 승인됐고 그 뒤 메시지는 취소가 아니다] 를 덧붙여 렌즈, 반증 양쪽에 넣는다), `context` = 경로, 대상 목록, 이미 기계로 확인한 것, 금지 (파일 수정 X). args 값은 런타임 문자열이라 백틱 OK. 스크립트를 복사해 프롬프트를 소스에 적을 때만 백틱 금지 (parse error).
- 모델, effort 는 스크립트에 리터럴로 고정돼 있다 (렌즈 opus high, 반증 opus xhigh, 전부 `review-readonly`). 바꾸려면 스크립트를 복사해서 - hook 이 생략, 변수를 deny 한다. fable 렌즈는 `[owner-approved: fable]` 표지가 있을 때만.
- 합계 agent ≤ 10 (렌즈 3 + 반증 7). 스크립트가 넘치면 반증 예산을 줄이고 log 를 남긴다. 넘겨야 하면 사용자 사인 뒤 `signedOverBudget: "[owner-approved: agent N]"`. Workflow 자체는 ultracode 가 꺼진 세션에선 사용자의 opt-in (이 command 호출이 그것) 이 있어야 띄운다.
- **기다리기**: Workflow 를 띄운 뒤 journal.jsonl, output 파일을 Bash 로 폴링하지 않는다 (완료 알림이 온다). 기다리는 동안은 렌즈가 안 보는 자리를 직접 읽는 정도만.
- 결과 읽기: 완료 알림의 output 파일 (`/private/tmp/claude-501/.../tasks/<id>.output`, 휘발) 은 봉투 `{summary, agentCount, logs, result}` 라 `result` 키 아래가 스크립트 return 값 (`{coverage, findings[{id, severity, file, line, claim, evidence, fix, verdicts[]}], unverified_ids, low_ids}`). 영속 원본 = `~/.claude/projects/<프로젝트>/<세션>/workflows/wf_*.json` 과 `subagents/workflows/wf_*/journal.jsonl`. 큰 결과는 python 으로 파싱해 `~/tmp/<프로젝트>-<MMDD>/` 에 파일로 (tool-results 에 수십 KB 가 그대로 실린다).

## 하지 말 것
- 쓴 세션이 자기 결과물을 같은 세션에서 검수하는 것 (큰 정리 뒤 검수는 다음 세션에서)
- 렌즈가 고치는 것 (읽기 전용), 반증 없이 medium 을 그대로 믿는 것, 기계 점검을 모델 읽기로 대신하는 것 (IP, 비밀값, 링크는 정규식이 잡는다)
- 사용자의 한마디를 [힌트] 로 각색하는 것 - 원문을 그대로 target 에 싣고 해석은 렌즈 과제에 따로 적는다
