# Memory Scope Disclosure (CRITICAL)

> 이 규칙의 `~/.claude/memory/`, `~/.claude/rules/<스택>/` 는 이 하네스의 관례다 (Claude Code 가 자동으로 읽는 경로가 아니다). 자기 구조에 맞게 고쳐 쓴다.

메모리 저장/수정 시 **반드시** scope 명시. 예외 없음.

| Scope (공식 명칭) | 색 | 경로 | 누가 보나 | Git | 뭘 담나 |
|-------|---|------|------|-----|------|
| **Global** (user) | 🔵 | `~/.claude/rules/`, `~/.claude/memory/` | 나, 모든 프로젝트 | ❌ | 스택, 도메인 무관 원칙, 함정, 절차, 사용자-Claude 관계 |
| **Local** (auto memory) | 🟡 | `~/.claude/projects/<project>/memory/` | 나, 그 프로젝트, 이 머신 | ❌ | 그 프로젝트의 작업 로그, 인계, 결정 이력, 실측 사실 (= Claude 의 기억) |
| **Project** (project) | 🟢 | repo 의 `CLAUDE.md`, `.claude/rules/`, `docs/` | 팀 전체 | ✅ | 팀이 지켜야 할 프로젝트 규칙, 설계, 운영 지식 |
| ~~Project-local~~ (local) | 🟠 | **폐지 (2026-10-19)** - `CLAUDE.local.md` 안 씀 (`.claude/settings.local.json` 은 설정이라 별개) | - | ❌ | 내 환경 전용 지시는 Local memory `reference_env_<주제>.md` 로 |
| **Team hub** | 🟣 | 팀 공용 저장소 (rules, skills, stacks) → 각 repo 로 sync | 팀, 여러 프로젝트 | ✅ (팀 공용 repo) | 2개+ 프로젝트에서 검증된 팀 공통 규칙, 스택별 규칙 |

- 색은 **위치**를 따라간다. 🟠 Project-local 은 쓰지 않는다.
- **Project-local 을 쓰지 않는 이유** - `CLAUDE.local.md` 는 만든 체크아웃에만 있어 worktree 세션이 못 보고 (공식 문서), 매 세션 통째로 실려 자리만 차지했다. 내 환경 전용 지시 (접속 진입점, wrapper 이름, 터널, 로컬 경로, 함정) 는 Local memory `reference_env_<주제>.md` 로 주제별로 (lazy, worktree 공유), 모르고 밟는 함정 몇 줄과 [작업 전 Read] 트리거만 프로젝트 MEMORY.md 에.
- **Team hub 는 scope 라기보다 승격의 종착지** — Local 에서 2번째 프로젝트 재현 → Global (사용자 개인의 작업 방식) 또는 Team hub (팀이 지켜야 하는 규칙). 갈림 질문 = "사용자 개인의 작업 방식인가, 팀 규칙인가".

> **이름 주의**: 공식 scope = User (여기선 Global) / Project (repo, 팀 공유) / Local (개인 + 그 프로젝트). `~/.claude/projects/<project>/memory/` 의 공식 이름은 auto memory (machine-local).

## 판정 흐름 (저장 전 3단계)
1. **나만 알면 되나?** → yes: Local 🟡 (내 환경 지시면 `reference_env_*` + MEMORY.md 트리거)
2. **팀이 알아야 하나?** → yes: 이 프로젝트만이면 Project 🟢, 여러 프로젝트면 Team hub 🟣 (스택 무관 = common, 스택 종속 = stack)
3. **모든 프로젝트, 모든 스택에서 그대로 유효하고 사용자-Claude 작업 방식에 관한 것** → Global 🔵 (rules = 행동 지시, memory = 맥락)

## 4 바구니 결정 트리 (세션 종료 때 파일 하나씩)
```
1. 호스트, DB 이름, 포트, 계정, wrapper 이름, 로컬 경로 같은 [값] 이 핵심인가?
   → (d) 🟡 Local memory `reference_env_<주제>.md` (값은 wrapper 참조로만, 비밀값 금지. 함정이면 MEMORY.md 에 트리거 한 줄)
2. 프로젝트 이름, 도메인 용어 (서비스 이름, 사내 시스템 약어 …) 를 지우면 문장이 무너지나?
   → (c) 프로젝트 고유: 팀이 알아야 하면 🟢 repo docs, 나만 알면 되면 🟡 Local
3. 스택 이름 (Java, Spring, jOOQ, Postgres, Helm, k8s, zsh …) 을 지우면 무너지나?
   → (b) 스택 종속 일반 지식: 🔵 `~/.claude/rules/lang/<언어>/`, `framework/<프레임워크>/`, `database/<db>/`
      (rules 는 매 세션 자동 주입이라 **원칙 한 줄 + memory 링크**, 사례, 수치, 프로젝트 이름은 memory 쪽에)
4. 셋 다 아니면 (a) 스택 무관 작업 방식 → 🔵 Global memory `feedback_*` (행동 지시로 굳으면 rules). 3질문 통과 + 출처 표지 `> 출처: <프로젝트> 1회 (<날짜>)` 를 달고 **즉시** 올린다
```
- **승급 기준 재정의**: [2번째 프로젝트 재현] 은 **올리는 조건이 아니라 표지를 떼는 조건**. 다른 프로젝트 종료 때 같은 패턴을 만나면 `grep -l '출처: .* 1회' ~/.claude/memory/*.md` 로 대조해 표지만 지운다. (옛 기준은 세션 단위로 아무것도 못 올리게 해서 승격이 한꺼번에 몰렸다)
- **(d) 접속 정보** (DB 이름, 호스트, 포트, 계정, wrapper, 터널, VPN 조건, 로컬 경로) 는 Local memory `reference_env_<주제>.md` 에 주제별로
- 받을 그릇 (rules 파일) 이 없어 못 올린 (b) 만 후보 목록 파일 하나에 모으고, 그 외는 쌓아 두지 않고 즉시 올린다

## 교훈은 `feedback_*` 파일로 분리한다
- 사용자의 피드백, 세션에서 확립된 교훈은 **`feedback_<slug>.md` 한 파일 = 한 교훈** 으로 저장한다. `todo_*` hand-off 나 `project_*` 작업 로그 안에 "교훈" 절로만 남기지 않는다 — 거기 묻히면 라우터에서 안 보이고 승격 감사 때마다 다시 파내야 한다 (실측: 한 프로젝트의 Local 458 파일 중 type=feedback 50, 교훈 177 문단이 todo_/project_ 안에 있었다).
- hand-off 에는 교훈의 **한 줄 hook + wikilink (slug 를 이중 대괄호로 감싼 꼴) 링크** 만. 프로젝트 MEMORY.md 라우터에는 📌 Feedback 섹션을 두고 새 feedback 파일을 한 줄씩 등록한다.
- `metadata.type` 과 파일 접두어는 일치시킨다 (type=feedback ↔ `feedback_`). 승격 감사는 type 으로 훑는다.

## 표시 형식
```
📝 저장 완료:
- `파일명.md` → **[Scope]** (경로)
```

## Global 검증 3질문
1. **모든 프로젝트**에서 동일 적용?
2. **다른 도메인**으로 옮겨도 유효?
3. **무지성 적용**해도 부작용 없는가?

3개 모두 yes → Global. 단, **팀이 지켜야 하는 규칙**이면 Global 대신 Team hub 🟣. 하나라도 no → Local (나만 알면 됨, 내 환경 지시 포함) 또는 Project (팀이 알아야 함).

## 프로젝트 ↔ global 경계 (요약)

한 줄 판정: **"다른 스택/도메인 프로젝트에 그대로 들고 가도 유효한가?"** yes = global / no = Local 또는 Project. 승급은 2개+ 프로젝트 검증 후, cross-scope 이동은 원본 **archive** (삭제 X) + 중복 검색 + 역링크 수정.
