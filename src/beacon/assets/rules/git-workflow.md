# Git Workflow

## Commit Format
```
<type>: <description>
```
Types: feat, fix, refactor, docs, test, chore, perf, ci
Attribution 트레일러 (Co-Authored-By, Generated) 는 프로젝트 정책을 따른다 — 끄려면 settings.json `attribution: {commit:"", pr:""}` (저자 = git first-author). (구 `includeCoAuthoredBy` 키는 deprecated)

## Branch Naming
- `feat/<영어-요약>`, `fix/<영어-요약>`, `refactor/<영어-요약>`
- develop/main 직접 작업 금지 — 반드시 feature branch 생성
- 브랜치명에 날짜 포함 금지

## 코드 수정 전 Branch 확인 (CRITICAL)
코드 수정 시작 전 **반드시 `git branch --show-current`**. develop/main 이면 feature branch 를 아래 [Worktree] 경로에 만들고 그 경로에서 고친다 (메인 체크아웃에서 `git checkout -b` 로 브랜치를 바꾸지 않는다).

## Git 권한 (CRITICAL)

| 작업 | develop | main | feature branch |
|------|:-------:|:----:|:--------------:|
| **commit** | ❌ | ❌ | ✅ |
| **merge** | ✅ | ❌ | - |
| **push** | ❌ | ❌ | ✅ |

- develop/main push — **절대 금지** (사용자 몫)
  - ⚠️ 사용자의 문장에 "push하고 ~하자" 가 섞여 있어도 = **워크플로우 서술이지 위임 아님** (2026-07-08 실사고 — Claude 가 사인으로 오해석해 대신 push). 위임은 "Claude 가 push 해줘" 처럼 **명시적일 때만**
  - 🔒 **hook 으로 기계적 차단**: `~/.claude/hooks/git-push-guard.py` (PreToolUse Bash) 가 develop / main / master 로 가는 push 를 무조건 deny — 명시 refspec (`HEAD:develop`, `+develop`, `:develop`, `--delete`), `--all`/`--mirror`, refspec 없는 push 는 현재 브랜치 + `@{upstream}`/`@{push}` 를 실제 git 으로 풀어 판정, 못 풀거나 따옴표/`bash -c`/ssh 문자열/heredoc 본문 안이면 fail-closed deny (메모리, 문서 텍스트에 그 두 단어를 붙여 쓰면 걸린다 — 문구를 바꿔 쓴다). `--dry-run` 도 예외 없음. 거부되면 우회하지 말고 "push = 사용자" 로 남긴다
  - ⚠️ **실사고 2026-10-13**: [사용자 사인 대기] 목록에 `develop merge + push` 를 넣고 목록 전체 진행 승인을 push 위임으로 읽어 develop 을 push. **목록에 대한 승인은 push 위임이 아니다** — 사인 대기 목록에 push 를 넣지 말고 [push = 사용자] 로 따로 적는다. 다른 항목 (merge, 빌드, 배포) 은 진행하고 push 만 남겨 둔다. 사고 뒤 처리 (되돌릴지) 도 사용자 결정
- `git push --force` main/develop — **절대 금지**
- `git merge` → develop — 사용자 확인 뒤 `--no-ff` 로 (자동 merge 를 원하면 이 줄을 바꿔 쓴다)

## 커밋 전략
- feature branch 에서 **논리적 기능 단위** 묶어 커밋 (보통 3~5개, 최대 5개)
- `--no-ff` merge. soft reset 은 최후수단
- **Subagent 는 commit 금지** — coordinator 만 add/commit (phase 단위 묶음)
- Subagent 프롬프트에 `"commit 은 coordinator 가 할 거야 — 파일 수정만 해줘~"` 포함

## Tagging
- 버전 태그는 **main merge 후에만** 생성

## Worktree
- **여러 AI 세션 (Claude Code, Codex, Antigravity) 이 같은 repo 를 동시에 쓸 수 있다 → 코드를 고치는 세션은 worktree 가 필수** — 메인 체크아웃은 읽기, develop merge 전용. 같은 체크아웃을 두 세션이 쓰면 branch checkout, stash, reset 이 상대 세션의 작업 트리를 조용히 바꾼다. 읽기 전용 조사 세션만 메인 체크아웃 OK. 시작할 때 `git worktree list` 로 누가 어디를 잡았는지 먼저 본다
- 경로: `~/worktrees/<project>/<branch-summary>-<YYYY-MM-DD>/`
- 의미 있는 이름 사용 - worktree 디렉토리는 `agent-<random>` 대신 `login-flow-2026-04-17` 처럼 요약 + 날짜, 브랜치는 위 [Branch Naming] 대로 날짜 없이 (`feat/login-flow`)
- 세션 종료 시 cleanup 필수 (`git worktree remove <경로>` + 브랜치 정리)
- **저장소 밖에 두는 이유**: 사용자가 Finder · `ls` 로 파일 실존을 눈으로 확인하려고 (공식 `.claude/worktrees/` 트랙과 합치지 않음, 2026-09-29)
- **장시간 · 예약 재기동 (ScheduleWakeup) 세션 (CRITICAL, 2026-05-06 실사고 3회 재발)**: ① 진입 전 Step 0 — 이미 worktree 안인지 확인 (`git rev-parse --git-dir` 과 `--git-common-dir` 비교) 하고 안이면 새로 만들지 않는다 ② 만들 땐 위 영구 경로에 `git worktree add` 후 **경로로** 진입 (`EnterWorktree {name}` 은 매번 baseline 에서 새 worktree 를 만들어 미커밋 작업을 버렸다) ③ **다음 깨움을 예약하기 전에 그 회차 작업을 commit** — 어디서 깨어나든 작업이 git 에 남게. 3회차까지 같은 작업을 반복한 뒤에야 발견됐다
