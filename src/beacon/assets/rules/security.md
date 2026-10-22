# Security Guidelines

> 실제 사고에서 실증된 사고 클래스만 규칙 문장으로 모았다. 사례 (날짜, 경로, 수치) 는 각자의 메모리 노트에 남긴다.

## Secret / Credential (CRITICAL)

- **하드코딩 금지** — API key, password, token 은 소스/설정 파일에 절대 직접 기입하지 않는다. env 또는 secret manager 로 주입.
- **credential 에 default 값 금지** — `${VAR:default}` 의 default 가 있으면 env 미주입 사고가 **영구 은폐**된다. credential 류는 default 없이 부팅 시 fail-fast 가 정답.
- **노출된 secret 은 즉시 rotate** — git 이력에 한 번이라도 평문으로 남았으면 제거가 아니라 교체가 해법.

## 배포 설정 정합 (CRITICAL)

- **배포 env 키 = 앱 설정 프로퍼티 경로 1:1 미러** — helm values / docker env 키가 application 설정 경로와 다르면 잘못된 값이 조용히 들어간다. 중간 변환, 별칭 금지. env 키는 yaml 경로에서 기계적으로 도출하는 것이 정본.
- **필수 설정은 부팅 시 검증** — 미주입 시 기동 실패가 런타임 오동작보다 낫다. 단, 테스트 mock 빈에 가려 미검출될 수 있으니 실제 부팅 경로로 확인.

## 보안 우회 (CRITICAL)

- **"임시" 보안 우회 커밋 금지** — permitAll 광역 허용, 인증 비활성 등 temp 우회가 공유 브랜치에 들어가면 반드시 잊힌다. 불가피한 한시 허용은 최소 범위 (경로+메소드) + 제거 조건 기록 + CI 가드를 동반할 것. 제거 조건은 주석으로 남기되, 소스 주석을 금지하는 프로젝트 (공개 대비 등) 는 커밋 메시지, docs, CI 가드 실패 메시지에 남긴다 - 공개 소스에 우회 지점을 알리지 않게.
- **보안 가드 (CI job, lint rule) 를 우회하거나 삭제하지 말 것** — 가드가 거슬리면 가드를 고치는 논의부터.

## 마이그레이션 중 import 정합 (HIGH)

- 프레임워크/라이브러리 메이저 마이그레이션 중에는 **구버전 패키지 import 가 컴파일은 통과하면서 런타임 사고**를 만든다 (Jackson 2/3 혼용 — bean 미존재 CrashLoop, annotation 조용히 무시). 프로젝트별 정합 규칙을 확인하고, 검증 grep 을 마련할 것.

## TLS 검증 — fail-closed (CRITICAL)

- **self-signed / 사설 CA 상대 서버라도 검증을 끄지 말고 CA 를 반입해 검증한다** — cert 를 truststore 에 넣거나 CA 파일로 검증하되, **CERT_NONE / InsecureTrustManager / verify=false 는 금지**. "붙긴 붙는데 검증은 안 되는" 상태는 MITM 표면을 열고, 인증서 만료, 교체 사고를 조용히 숨긴다.
- **검증 실패는 fail-closed** — CA 파일이 없거나 로드 실패면 **예외로 기동을 막는다** (조용히 CERT_NONE 로 폴백 = fail-open 금지). 검증 우회 스위치를 두더라도 명시적 opt-in + 비-loopback 대상 거부 등 안전장치를 동반. 자동 보안 리뷰가 fail-open 을 지적하면 정당한 지적이니 fail-closed 로 강화.

## 보편 원칙

- SQL 은 parameterized query 만 (문자열 연결 금지)
- 사용자 입력은 신뢰 경계에서 검증
- 에러 메시지/로그에 secret, PII 노출 금지
- **외부 payload 의 URL, 파일명은 그대로 쓰지 않는다** — 통보 본문의 다운로드 URL 은 호스트를 따라가지 말고 (SSRF, 오연결) 경로와 쿼리만 취해 설정된 호스트로 재조립하거나 호스트 allowlist 로 검증한다. presigned URL, CDN 처럼 서명에 호스트가 들어간 URL 은 재조립하면 깨지니 allowlist 쪽으로. 경로 조각으로 쓰는 이름 (파일명, 이벤트명) 은 `/`, `\`, `.`, `..`, 빈 값을 거부하는 [이름만] 검증을 둔다.

## 출력 위생 (CRITICAL) — 의도가 좁아도 필터는 파이프라인에

설정, values, env, credential wrapper, API 컬렉션을 볼 때 [이 줄만 보려던 것] 은 방어가 안 된다. 같은 접두어, 바로 다음 줄, `value:` 줄, diff 접두 줄로 매번 다른 경로로 샜다.

- **설정/values/env 조회는 키 이름만 출력** — `helm get values`, `kubectl get secret -o yaml`, `printenv`, `.env` 를 통째로 찍지 않는다. 키 목록만 뽑고 (`yq 'keys'`, `awk -F= '{print $1}'`), 값이 필요하면 셸 변수로 받아 길이, 일치 여부만 본다.
- **values/설정 스냅샷 파일을 grep 할 때 패턴이 secret 키 줄에 닿지 않게 한다** — `svc[^:]*:\s*\S*` 처럼 [접두어로 시작하는 모든 키] 를 잡는 패턴은 `svcClientSecret: <값>` 줄까지 그대로 찍는다. 규칙 = 원하는 키를 **정확히** 앵커 (`grep -E '^\s*url:'`) 하고, 출력 직전에 `grep -viE 'secret|password|token|credential'` 를 **항상** 끼워 넣는다. [호스트만 뽑는다] 는 의도가 있어도 파일 전체를 훑는 grep 은 secret 키가 같은 접두어를 쓰는 순간 새는 구조라, 필터를 의도가 아니라 파이프라인에 둔다.
- **credential wrapper 본문은 아예 출력하지 않는다 — 마스킹 정규식은 짧은 비번을 못 잡는다** — 필요한 건 usage 줄, URL, 포트뿐이니 `grep -oE "Usage:.*|https?://[^ ]+|:[0-9]{2,5}"` 처럼 **원하는 토큰만** 뽑고, 본문 전체 sed/cat 은 금지. 아래 [함수 경계로 잘라 읽기] 는 그래도 본문을 봐야 할 때의 차선책이다.
- **credential wrapper 파일 (`secret-wrappers.sh` 류) 본문은 함수 경계로 잘라 읽는다** — `grep -A N` 은 함수 끝을 넘겨 옆 자격증명 줄까지 찍는다. 시작줄 `^name()` → 다음 `^}` 까지 `sed -n` 으로 자르고, 그 안에서도 값이 될 만한 긴 토큰은 `sed -E 's/[A-Za-z0-9._-]{20,}/<masked>/g'` 로 마스킹 후 출력. 필요한 건 대개 URL, 포트, 변수 이름뿐이니 그것만 `grep -o` 로 뽑는 편이 더 안전.
- **credential wrapper 는 [경로/구조만 본다] 는 의도로도 본문 줄을 출력하지 않는다 — 함수 이름 목록 (`grep -oE '^[a-z-]+\(\)'`) 까지만** — [줄 필터 + 긴 토큰 마스킹] 조합은 짧은 비번에 무력하다. 경로가 궁금하면 wrapper 대신 **호출 결과의 오류 문구, 프로세스 argv (`ps -o args=`, ssh 대상 호스트만)** 로 판단하고, 그래도 본문이 필요하면 `grep -c` 로 [해당 패턴이 있는가] 만 센다.
- **`Usage:` 줄을 찾을 때도 `grep -A N` 은 금지 — usage 바로 다음 줄이 비밀번호 대입 줄이다** — 규칙 = usage 는 `grep -oE 'Usage:[^"]*'` 처럼 **그 줄 안의 토큰만**, 래퍼 파일에는 `-A`/`-B`/`-C` 를 아예 쓰지 않는다, 출력 필터 패턴에 **`_pw|pw=|passwd`** 를 추가한다 (단어 필터 `secret|…|auth` 는 `XXX_PW='…'` 대입 줄을 못 잡는다).
- **출력 마스킹 필터에 `key` 계열도 넣는다 — `secret|password|token|credential` 만으로는 API 키가 샌다** — 규칙 = 외부 API 컬렉션, env, values 류를 출력하기 전 `grep -viE 'secret|password|token|credential|key|auth'` 를 **파이프라인에 항상** 끼우고, request body 는 키 이름만 (`jq 'keys'`) 본다. 서브에이전트 프롬프트에도 같은 필터를 명시해 넘긴다 — 에이전트는 이 규칙 파일을 읽어도 필터 패턴을 스스로 좁혀 쓴다.
- **helm values [구조만 본다] grep 도 `value:` 줄에 닿는다** — `grep -E '^  [a-zA-Z_]+:'` 같은 들여쓴 `key:` 패턴은 `extraEnv` 의 `  value: <값>` 줄에도 매치한다 (`value:` 도 key 다). 규칙 = env 리스트 구조는 **`grep -E '^- name:'` 로 이름만**, values 파일 전체 grep 에는 항상 `grep -vE '^\s*value:'` 를 추가하고, 출력 직전 마스킹 필터 (`secret|password|token|credential|key|auth|_pw|pw=|passwd`) 는 값 줄 자체를 못 거른다는 걸 전제로 한다.
- **manifest/values `diff` 출력도 `value:` 줄이 `<`/`>` 접두로 나온다 — `^\s*value:` 앵커는 diff 줄을 못 거른다** — 규칙 = helm/kubectl 출력을 찍는 모든 파이프라인에 **`grep -vE 'value:'` 를 앵커 없이** 항상 포함하고 (diff, yaml, json 공통), env 비교는 `- name:` 줄만 뽑아 이름으로 diff 한다. 템플릿, 헬퍼를 새로 만들 때 필터 줄을 복사하지 말고 `safe_out` 류 공통 함수 (`tr -d '\r' | grep -vE 'value:' | grep -viE 'secret|password|token|credential|key|auth|_pw|pw=|passwd|비밀번호|비번'`) 를 source 해서 쓸 것 — 필터를 손으로 옮겨 적는 순간 한 항목이 빠진다.
- **expect/ssh 래퍼로 `sudo -S` 비밀번호를 보낼 때 로컬 pty 에코로 평문이 찍힌다** — 원격 명령 안의 `sudo -S` 프롬프트, ProxyJump 2번째 hop 비밀번호 프롬프트는 에코 억제가 없다. 규칙 = **전송 구간 (multi-hop 이면 전 구간) `log_user 0`**, 출력은 원격 파일로 받아 (`sh -c '{ cmd; } > /tmp/out 2>&1'`, 리다이렉트와 필터는 `sh -c` 내부) **별도 비-sudo 호출로 회수**. 출처 = zsh-pitfalls §4-3

## 외부 전달 패키지

- **외부 전달 패키지 (시험 자료, 인계 tar) 는 조립 후 자동 스캔** — JWT 패턴 (`eyJ…\.…`), credential 키=값, **게이트웨이 공인 IP 숫자**, 작성자 머신의 펼쳐진 홈 경로 (`~` 대신 실제 계정 이름이 든 절대 경로) 를 grep 으로 전수 확인하고, 공인 IP, 자격은 `<STAGING_HOST>`, wrapper 참조 자리표시자로 치환한다. 원본 데이터에 이미 박혀 있던 IP 가 사본으로 그대로 흘러간다. 스캔 = 사람이 보는 문서만이 아니라 **데이터 JSON, 스크립트, 증적** 까지
- **패키지 위생 스캔에 DB 계정, 비밀번호 리터럴을 포함하고, 정정 백업 (`.bak`) 은 패키지 밖에 만든다** — 패치 스크립트가 패키지 안에 만든 `.bak` 에 비밀값이 되살아난다 (정본만 고쳐도 백업이 남는다). 스캔 = `user=`, `password=`, `PGPASSWORD`, 계정명 리터럴 + `find -name '*.bak*'`.

## secret 전달, 메모리 grep

- **secret 을 원격 pod/컨테이너 안 스크립트에 넘길 땐 argv, `env X=` 가 아니라 stdin 으로** — 셸 변수로 받아 `printf '%s\n' "$U" "$P" | kubectl exec -i pod -- python -c '… sys.stdin.read() …'` 처럼 전달하고, 쓰고 나면 `unset`. argv 는 ps, 감사 로그, transcript 에 남고, `kubectl exec -- env X=…` 도 같은 문제. 출력엔 롤 이름처럼 비밀이 아닌 식별자만
- **메모리 디렉토리를 grep 할 때 `archive/` 를 제외한다 — 압축 전 스냅샷 (`MEMORY-*-precompact.bak.md`) 에는 옛 평문이 그대로 남아 있다** — 규칙 = 메모리 grep 은 `--exclude-dir=archive` (또는 `grep -r … memory/*.md` 처럼 최상위만) + 출력 직전 마스킹 필터 (`secret|password|token|credential|key|auth|_pw|pw=|passwd|비밀번호|비번`) 를 파이프라인에, 서브에이전트 프롬프트에도 같은 제외를 명시.
