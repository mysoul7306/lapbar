# zsh Pitfalls (CRITICAL)

> Bash tool 의 셸 = zsh (macOS). bash 지식으로 쓰면 조용히 어긋나는 함정 모음. 실사고 기반.

## 1. `$var:문자열` = history modifier 해석

`m=gateway; echo $m:latest` → `gatewayatest` (`:l` = lowercase modifier). push 는 엉뚱한 repo 로 "성공" 하고 옛 latest 로 롤아웃된다.

**규칙**: 변수 뒤에 `:` 가 오는 모든 조합은 **반드시 `${m}:latest` 중괄호** 사용. docker 태그, URL (`$host:8080`), 시각, **`rev:path` 형태 전부** (`git show "${B}:path"` 필수 — git show/diff/ls-tree) 해당. docker/buildx 명령 작성 시 `-t` 인자에 `$` 와 `:` 가 같이 있으면 무조건 중괄호인지 눈으로 재확인 + **push 출력의 repo 경로를 반드시 판독**.

## 2. 검증 원칙 — "push 성공 로그" ≠ "의도한 대상에 push"

이미지/아티팩트 배포 후에는 **소비 측 (k8s 파드 imageID, 레지스트리 매니페스트) 의 digest 를 빌드 산출물 digest 와 대조**한다. 태그 이름 오염은 push 성공 로그로는 안 잡힌다.

## 3. `while read` 루프 + ssh/expect 계열 = stdin 슬러핑

`while read -r line; do expect-wrapper "$line"; done < file` — expect/ssh/psql 등 stdin 을 읽는 명령이 루프 안에 있으면 **입력 파일의 남은 줄을 전부 소비** → 루프가 1회만 돌고 **exit 0 으로 조용히 종료** (완료 로그는 멀쩡히 찍힌다).

- **같은 가족: `claude -p`, `codex exec` 도 stdin 을 읽는다** — `bash -s <<EOF` 드라이버 안에서 부르면 스크립트의 나머지 줄을 프롬프트로 삼키고 뒤 단계는 조용히 미실행 (2026-10-05). 드라이버는 파일로 넣고 (`docker exec <box> bash /home/<u>/run.sh` — `~` 는 호스트 zsh 가 먼저 푼다) 각 호출에 `</dev/null`.

**규칙**: 루프 입력은 **fd 분리** (`while read -u 9 ...; done 9< file`) + 명령에 **`< /dev/null`** 이중 방어. `xargs` 자식도 같은 stdin 을 소비하니 파이프 대신 `< file` + 자식에 `</dev/null`. 완료 로그의 "N/total" 카운트가 total 과 일치하는지 반드시 확인 (exit 0 ≠ 전량 실행).
- **같은 가족 — Claude Code hook 스크립트 (PreToolUse 류, stdin 으로 JSON 을 받는다) 를 손으로 부를 때** `python3 hook.py --help` 처럼 인자만 주면 stdin 을 기다리며 영원히 매달려 Bash 호출 전체가 백그라운드로 밀린다 (실사고 2026-10-14, 가드 self-check). 규칙 = hook 은 `</dev/null` 또는 샘플 JSON 을 파이프로 (`printf '{}' | python3 hook.py`), 그 호출을 체인 끝에 두지 않는다.

## 4. "0건" 을 세는 `grep -c` 가 `&&` 체인을 끊는다

`gen.py && echo "잔여: $(grep -c '{{' out.md)" && <원상복구>` — 잔여 0건이면 `0` 이 찍히고 뒤의 원상복구는 안 돈다. `grep -c` 는 **매치 0건이면 카운트 `0` 을 출력하면서 exit 1** (`-c` 는 종료코드를 바꾸지 않는다). 즉 "미치환 0건" 같은 **성공 상태일수록 체인이 끊긴다** — 실패 시에는 멀쩡히 이어지므로 검증 중에 안 드러난다.

**규칙**: 카운트를 조건이 아니라 **값**으로 쓸 때는 `&&` 로 잇지 말고 `;` 로 분리하거나 `|| true` 를 붙인다. 같은 함정: `grep -q`, `diff`, `test`, `cmp` — **결과가 0/없음일 때 비영 종료** 하는 명령 뒤에 `&&` 로 후속 작업을 매달지 말 것. 특히 **원상복구, 정리 명령을 체인 끝에 두지 않는다**.
- **`set -e` 변종**: 커맨드 치환 안의 `grep -c` 도 0건이면 exit 1 이라 `set -e` 스크립트가 그 줄에서 조용히 즉사한다. **규칙**: `set -e` 아래 카운트 치환은 `n=$(grep -c … || true)`, 원격 스크립트는 종료코드 + **마지막 단계 출력 존재** 를 함께 확인 (rc 만 보면 조기 종료가 성공처럼 보인다).
- **복제본 변종**: 픽스는 원본만이 아니라 **템플릿, 복제본 전수** (`grep -n 'grep -c' *.sh`) 로 확인할 것.
- **`set -o pipefail` 변종 (bash 도 동일)**: `diff a b | grep -q PAT || { echo 중단; exit 1; }` 는 grep 이 매치해도 **diff 의 rc 1 (차이 있음) 이 파이프 상태가 돼** 게이트가 [차이가 기대와 다름] 으로 오판한다 — 차이를 기대하는 명령 (`diff`, `cmp`, `grep -c` 0건) 을 pipefail 파이프 선두에 두지 않는다. 규칙 = 결과를 파일로 받고 (`diff a b > f || true`) 파일을 grep 하거나, 선두 명령에 `|| true`. (실사고 2026-10-19, helm values 스냅샷 게이트, 첫 prep 중단)

## 4-1. `--include=*.java` 처럼 **옵션 값 안의 glob** 도 zsh 가 먼저 확장한다

`grep -rn KEY --include=*.java src/` → `(eval): no matches found: --include=*.java`. cwd 에 매치가 없으면 zsh 는 `no matches found` 로 **명령 자체를 실행하지 않는다** (bash 는 원문을 그대로 넘겨 정상 동작). 출력이 "매치 0건" 처럼 보여서 **"그런 참조는 없다"는 잘못된 결론**으로 간다.

**규칙**: 옵션 값에 glob 문자가 들어가면 **반드시 따옴표** — `--include='*.java'`, `--exclude='*.log'`, `-name '*.md'`, `-iname '*aux*'`. 조사 결과가 전부 0건으로 나오면 **명령이 실행되긴 했는지부터 의심**할 것.
- **일반 인자 glob 도 동일**: `rm -f out.pdf pv-*` 처럼 체인 선두 glob 이 0건이면 eval 전체가 시작도 안 된다. 지울 파일 glob 은 `rm -rf dir && mkdir dir` 식 디렉토리 재생성으로 방어. [처음엔 없을 수도 있는 파일] 을 지우는 자리엔 glob 을 두지 않는다. **`for f in dir/*.py; do …; done; echo X` 루프도 같다** — in 목록 glob 이 0건이면 루프와 같은 줄의 뒤 명령까지 통째 미실행 (실사고 2026-10-13, debug 로그 검색 줄 전체 누락).
- **URL 변종**: `curl https://host/api?x=1` 처럼 따옴표 없는 URL 의 `?` 도 glob 이라 `no matches found` 로 그 줄이 통째 미실행 (HTTP 코드 변수가 빈 값으로 남아 [응답 없음] 오판). URL 은 항상 홑따옴표 (실사고 2026-10-22)
- **dotfile 변종**: `*` 는 점으로 시작하는 파일을 안 잡는다 — `grep KEY collector/*.env.example` 은 `.dev.env.example` 만 있는 디렉토리에서 `no matches found` 로 그 줄 통째 미실행 ([참조 0건] 오판). dotfile 조회는 `find <dir> -name '*.env.example'` 또는 `.*.env.example` 패턴을 따옴표로 (실사고 2026-10-12)
- **앵커, 들여쓰기 가정 변종 (실사고 2026-10-22, 설정 파일 스캔)**: 구조화 텍스트 (cfg, sfs, craft, yaml) 를 `^\t\tpart = ` 처럼 **보지 않은 들여쓰기를 가정한 앵커** 로 쓸면 매치 0 건이 [그런 항목 없음] 으로 읽힌다 (실제 = 블록 이름은 들여쓰기 0, 키는 탭 1 개). 규칙 = ① 쓸기 전에 `sed -n '11,16p' f | cat -vet` 로 실제 공백을 한 번 본다 ② 앵커는 `^\s*key = ` 처럼 공백 관용으로 ③ **양성 대조 (positive control)** - 반드시 매치해야 하는 줄 (알고 있는 항목, 알고 있는 키) 을 같은 패턴으로 먼저 돌려 1 건 이상인지 확인한 뒤 본 스캔. 전 항목 0 건, 전부 NONE 은 결과가 아니라 패턴 고장 신호다. 리더 에이전트 보고의 `파일:줄` 인용도 들여쓰기까지는 안 알려 준다
- ① `;`/개행으로 나뉜 여러 명령 중 glob 실패는 **그 줄만 죽이고 다음 줄은 실행된다** (같은 줄의 non-glob 인자까지 미실행 = 부분 정리, 마지막 echo 는 정상이라 성공 착시).
- ② **Claude Code Bash 툴의 eval 에선 `(N)` qualifier 자체가 비활성** (`rm x*(N)` 이 도리어 `no matches found`) — `(N)` 처방은 이 환경에서 무효.
- ③ 대체 처방 = 같은 eval 에서 `setopt null_glob` 선행. 단 null_glob 은 매치 0건 시 **인자 자체가 소실**돼 `ls /tmp/x_*` 가 인자 없는 `ls` (cwd 전체 나열) 로 변한다 — 또 다른 성공 착시. **정리/조회는 명시 경로가 최선**, glob 이 꼭 필요하면 null_glob + 결과 실측 대조.
- 다른 래퍼가 문자열을 eval 하는 경로 (`<remote-wrapper> '…'` 류) 에서도 홑따옴표 안 `|`, `*)`, `\s` 가 같은 이유로 터진다 (래퍼 안 eval 은 따옴표를 못 지켜준다).

## 4-2. bash `${PIPESTATUS[...]}` = zsh 에선 조용히 빈 값

`cmd1 | cmd2; echo "EXIT=${PIPESTATUS[1]}"` → `EXIT=` (빈 값, 에러도 안 남). zsh 의 파이프 종료코드 배열은 **소문자 `$pipestatus` + 1-index** (`${pipestatus[2]}` 가 bash 의 `${PIPESTATUS[1]}`). 대문자는 미정의라 빈 문자열 — 에러 없이 판정이 무의미해진다. **파이프 판정은 가급적 결과물 실측 (행수/파일 크기 대조) 을 정본으로**, 코드 판정이 필요하면 소문자 배열.

## 4-2-1. docker run/exec + heredoc, 파이프 = **`-i` 필수**

`docker exec c psql <<'SQL' …` 에 `-i` 가 없으면 stdin 이 컨테이너에 안 붙어 psql 이 빈 입력으로 exit 0 — DDL 전량 미실행이 성공처럼 보인다.

**규칙**: docker run/exec 에 stdin 을 먹이는 모든 형태 (heredoc, `|`, `< file`) 는 **반드시 `-i`**. `-c "..."` 인자 방식은 stdin 불필요라 무관 — 같은 세션에서 조회는 되고 DDL 만 조용히 빠지는 비대칭이 생겨 더 헷갈린다. DDL/DML 실행 후엔 **결과를 별도 조회로 실측** (#2 원칙의 docker 판).

## 4-3. expect 로 `sudo -S` 비밀번호 전송 = 로컬 pty 에코로 평문 노출

ssh 로그인 프롬프트는 ssh 가 에코를 막아주지만, 원격 명령 안의 `sudo -S` 프롬프트 (ProxyJump 2번째 hop 프롬프트 포함) 는 **expect 의 로컬 pty 가 send 내용을 에코** → transcript 에 프롬프트 뒤 비밀번호가 평문으로 찍힌다.

**규칙**: ① 비밀번호 전송 구간 (multi-hop 이면 전 구간) **`log_user 0`** ② 출력은 원격 파일로 받아 별도 비-sudo 호출로 회수하거나, expect 버퍼로 모아 `[string map [list $env(PW) "<pw>"] $out]` 마스킹 후 출력 ③ 필터와 리다이렉트 (`| tail` 포함) 는 `sh -c` **내부** — 밖에 걸면 프롬프트까지 삼켜져 expect 가 영원히 대기한다. 같은 규칙 = `~/.claude/rules/security.md` [출력 위생]

## 4-4. `cmd; echo; echo RC=$?` = 직전 echo 의 RC

`$?` 가 **중간 echo 의 종료코드 (항상 0)** 를 읽어 연결 실패 (HTTP 000) 가 "RC=0" 으로 찍힌다.

**규칙**: 종료코드는 대상 명령 **직후** 캡처 (`RC=$?`) 하거나, HTTP 는 `curl -w '%{http_code}'` 로 **결과값을 실측** (#2 원칙의 curl 판).
- k8s port-forward 는 대상 pod 재시작 후 **리스너만 살아남아** 접속을 조용히 거부한다 — "리스너 있음" ≠ "포워딩 동작" (프로브 = 실요청). 프로브 실요청이 죽은 pf 를 자진 종료시키므로 정리 kill 은 프로브 뒤 목록을 다시 보고 한다. 터널 스크립트가 원격 pf 를 자기 원격 명령으로 띄우는 구조면 pf 를 손으로 먼저 띄우지 말 것 (포트 충돌 → ssh 즉시 종료).
- **루프 본문 변종**: `for m in a b; do ./build.sh $m; done; echo ALL_DONE` — 본문이 즉사해도 루프 뒤 `ALL_DONE` 은 찍힌다. 루프 본문마다 `rc=$?` 캡처 + 비영이면 `exit` 하고, 완료 판정은 로그 문구가 아니라 **산출물 실존** 으로.
- **역방향 착시**: 그 가드를 `[ $rc -ne 0 ] && { exit $rc; }` 로 쓰면 성공 회차마다 루프 종료코드가 1 이 된다 — 가드는 `if [ $rc -ne 0 ]; then exit $rc; fi` 로 쓰고, 총괄 판정은 루프 종료코드가 아니라 **모듈별 RC 줄 + 산출물** 로.

## 4-5. Claude Code Bash 툴의 `grep` = ugrep 래퍼 — binary 판정 파일을 조용히 통째로 스킵

`grep` = ugrep 함수 (`-I` 하드코딩 + `--ignore-files`). 제어문자 섞인 로그를 바이너리로 보고 **파일째 건너뛰며** `-c` 도 안 찍는다. .gitignore 대상도 빠진다.

**규칙**: 로그 파일, binary 섞임 가능 파일, gitignored 생성물 검색은 **`/usr/bin/grep` 명시** 또는 `-a` 플래그. 소스코드 검색은 래퍼 그대로 OK. 조사 결과가 뜻밖의 0건이면 #4-1 원칙대로 명령/파일 판정부터 의심. **CP949 한글 텍스트 (Windows bat, 원격 출력 저장본) 도 바이너리로 본다** — `grep -c 'foo' x.bat && …` 이 0 출력 없이 rc 1 로 끝나 뒤 단계 (sftp put) 가 조용히 빠진다 (실사고 2026-10-08). 비-UTF-8 파일은 `/usr/bin/grep -a`, 출력 디코딩은 `LC_ALL=C tr -d '\r' | iconv -f CP949 -t UTF-8 -c`.

## 4-6. `pkill -f "<패턴>"` = 패턴이 자기 셸 커맨드라인에 있으면 자기살해

`sh -c "pkill -f 'iperf3 -s'; nohup iperf3 -s &"` → exit 143, 후속 미실행. 패턴을 담은 `sh -c`/`bash -c` 자기 자신이 먼저 매칭돼 SIGTERM 으로 죽고 `;` 뒤 후속 명령이 조용히 사라진다 (원격이면 exit 143 이 가려져 성공처럼 보임).

**규칙**: ① 정확한 프로세스명이면 `pkill -x <이름>` ② `-f` 가 필요하면 **브래킷 트릭** `pkill -f "[p]attern"` (정규식은 대상 리터럴과 매칭되지만 자기 커맨드라인의 `[p]attern` 리터럴과는 불일치) ③ kill 후 후속 작업은 같은 체인에 두지 말 것. 원격 `bash -c '<전체 명령>'` 래퍼 안에서는 브래킷도 래퍼 자신에 매치된다 → **이름으로 좁히거나 (`pgrep -a kubectl | grep <포트>`) 띄울 때 받은 pid (`$!`) 로 kill, 판정**. 컨테이너 pid1 이 `sleep` 류면 죽은 exec 자식이 좀비 (Z) 로 남아 pgrep 에 잡힌다 — "안 죽는 프로세스" 로 오판 금지 (`/proc/<pid>/stat` 3열로 판별). 장기 프로세스는 nohup& 대신 **자체 데몬 옵션** (`iperf3 -D` 류) 이 정석.
- **역방향 변종 — 남의 argv 속 텍스트 (실사고 2026-10-11)**: `ps` 스냅샷을 이름 문자열 (`train\.py`, `http\.server`) 로 거르는 생존 판정은 **다른 프로세스의 커맨드라인에 든 같은 글자**에도 걸린다 — 다른 도구의 argv 에 든 같은 파일 이름, 리뷰 에이전트의 `grep <패턴>` 이 [장시간 작업 살아 있음] 으로 읽혀 체인 preflight 가 FAIL. 규칙 = 판정 패턴은 **기동 꼴로 앵커** (`python[0-9.]* (-u )?[^ ]*/train\.py( |$)`, `-m http\.server`) 하고, 매치해야 할 줄과 말아야 할 줄을 섞은 **합성 argv 로 패턴을 먼저 시험**한다. 상주 앱은 이름이 아니라 RSS 임계로 [모델 로드] 를 가늠.

## 4-7. 정체불명 ssh 리스너 kill = colima 전멸 위험

로컬 포트를 점유한 `ssh` 를 "죽은 staging 터널" 로 단정하고 kill → 실제로는 **colima 의 ssh mux** (docker 소켓 + 모든 published 포트를 한 ssh 로 포워딩) 라 docker API 단절 + 로컬 컨테이너 스택 전원 SIGTERM.

**규칙**: ssh 리스너를 kill 하기 전 ① `lsof -nP -i :<port>` 의 바인드 주소 확인 — colima 는 `*:<port>` (전 인터페이스), 수동 `-L` 터널은 보통 `127.0.0.1:<port>` ② `ps -p <pid> -o args=` 로 cmdline 에 `colima`/`lima` 흔적 확인 ③ 로컬 스택과 포트가 겹치는 수동 터널은 **처음부터 다른 로컬 포트로**. HTTP 000/TLS 실패로 "죽은 pf" 판정하기 전에 **그 포트가 지금 누구인지**부터 — 같은 포트에 http (로컬) / https (staging) 가 겹치면 TLS 에러가 죽음처럼 보인다.

## 4-8. `${v:+SET}${v:-UNSET}` = 값이 있으면 SET **뒤에 실제 값**이 붙는다 — credential 평문 노출

값이 세팅돼 있으면 `SET` + 원값 → `SET<진짜비밀번호>` 로 출력된다 (`:-` 는 '비었을 때만' 대체라 두 확장이 상호배타가 아님). `:+`/`:-` 는 POSIX 공통이라 원격 bash 도 똑같이 샌다.

**규칙**: 존재 여부만 볼 땐 값이 절대 안 새는 형태로 — `[ -n "$val" ] && echo SET || echo UNSET` 또는 `echo "${val:+SET}"` **단독** (미설정 시 빈 줄). credential 계열은 길이도 힌트가 되니 `${#val}` 조차 신중. 원격 스크립트를 문자열로 조립할 때 [존재 확인] 줄은 반드시 `[ -n "$AT" ] && echo OK || echo FAIL` 꼴로만. 토큰이 짧게 살아도 규칙 위반은 위반이다. 같은 계열 = wrapper 함수 본문 grep, `ps`/`pgrep -f` 풀 cmdline (4-5 참조).

## 4-9. expect 안의 원격 명령 문자열 = Tcl 큰따옴표 안 `[…]` 는 명령 치환

`send "… [p]ort …"` → `invalid command name "p"` 로 spawn 전 즉사 (원격 장애로 오판).

**규칙**: 원격 명령은 Tcl 문자열에 넣지 말고 **env 로 넘겨** `$env(CMD)` 로 send — 대괄호, 달러, 역슬래시 전부 무해. 같은 호스트라도 SSH 경로가 둘 이상 (계정, 포트, 공인 IP 별) 이면 [gw 죽음] 판정 전에 경로별로 따로 `nc -vz` 프로브 — 한 경로 timeout 을 전체 장애로 일반화하지 말 것.

## 4-10. `${v:+-H "$v"}` 선택 인자 확장 = 인자 경계가 깨진다

zsh 는 `${v:+word}` 의 word 안 따옴표를 인자 경계로 살려 주지 않아 `-H` 와 헤더값이 한 덩어리가 되거나 쪼개진다 → 서버는 `Bearer token is malformed` 류 가짜 **401**.

**규칙**: 선택 인자는 **배열**로 (`args=(); [[ -n $hdr ]] && args+=(-H "$hdr"); curl "${args[@]}" …`). 프로브가 4xx 를 내면 **서버 로그의 거부 사유부터** 읽고 (#2 원칙), [인증 정책이 바뀌었다] 류 결론은 같은 요청을 **명시 인자로 재현**한 뒤에만 낸다.

## 4-11. ssh 원격에서 `nohup cmd … &` = `</dev/null` + `setsid` 없으면 세션이 안 닫힌다

`ssh gw 'nohup bash ./watch.sh >/dev/null 2>&1 & disown; tail log'` → 출력 없이 매달림. stdout/stderr 만 돌리고 stdin 을 안 닫으면 백그라운드 프로세스가 ssh 채널을 붙들어 래퍼가 안 끝나고 [아무것도 안 됐다] 처럼 보인다 (데몬은 정상 기동돼 있다).

**규칙**: 원격 데몬 기동은 `setsid nohup cmd </dev/null >/dev/null 2>&1 &` 고정 + **기동 확인은 같은 세션이 아니라 별도 호출** 로 (pidfile, `pgrep -a <이름>`). ⚠️ **macOS 로컬에는 `setsid` 가 없다** (`env: setsid: No such file` 로 기동 자체가 안 됨) — 로컬 데몬은 `nohup cmd </dev/null > log 2>&1 &` 만, 원격 Linux 에서만 setsid.
- 짝 함정 ① bash 는 **`sleep` 자식이 도는 동안 SIGTERM 처리를 미룬다** → 판정은 sleep 주기 뒤 재확인, 급하면 `kill -TERM -- -<pgid>` ② 구 인스턴스가 늦게 죽으며 새 인스턴스의 pidfile 을 지운다 → 재기동 뒤 pidfile 과 `pgrep` 결과 일치 실측, 정리 단계는 [pidfile 내용 == 내 pid 일 때만 삭제]. ③ 자식 (학습 등) 은 살리고 **드라이버만 떼어 낼 땐 TERM 이 아니라 `kill -9 <드라이버 pid>`** (foreground 자식 대기 중엔 trap 이 미뤄져 [지금 떼어졌다] 판정 불가, 자식은 재부모화돼 계속) + 잠금, pidfile 을 **자식 pid 로 재작성** + 판정은 자식 생존 `ps -p` 실측 (실사고 2026-10-12). 런처는 처음부터 [학습만] / [체인만] 으로 쪼개 두면 수술이 필요 없다.
- 처방 3종을 다 써도 래퍼 사정 (pty, 마커 대기) 으로 매달릴 수 있다 → 원격 데몬 기동 호출은 ① 짧은 timeout (60초) ② 매달리면 TaskStop ③ 판정은 항상 별도 호출 (`pgrep -f '[이]름'` + 로그 tail). **기동 호출의 출력, 종료코드는 판정에 쓰지 않는다**. pidfile 의 `$!` 는 `setsid` 래퍼 pid 라 실제 pid 와 다를 수 있으니 생존 판정은 이름 pgrep 으로.
- **로컬 데몬 런처 변종 (실사고 2026-10-06)**: `( cd … && nohup cmd … & )` 로 띄우는 스크립트를 Python `subprocess.run(capture_output=True)` 로 부르면, 백그라운드 잡을 fork 한 **서브셸 bash 가 호출자의 stdout 파이프 (fd 1) 를 물고 데몬 옆에 남아** EOF 가 안 와 timeout 까지 멈춘다 (데몬 자신의 0/1/2 가 깨끗해도). 규칙 = 서브셸 전체에 `</dev/null >/dev/null 2>&1` 을 걸어 0/1/2 를 끊고, 판정은 포트 리스너 (`lsof -iTCP:<port> -sTCP:LISTEN`) 로.

## 5. 기타

- **`nohup python3 server.py > log 2>&1 &` 는 `-u` 없으면 성공 시 빈 로그** (블록 버퍼링 — 죽을 때만 flush 돼 [빈 출력 = 실패] 오판). 규칙 = `python3 -u` + 기동 판정은 로그가 아니라 **실요청** (`curl -w '%{http_code}'`) 으로.
- **`=word` 확장 — `echo ===` 도 터진다** (`=cmd` 명령 경로 확장 → `== not found` 로 eval 전체 중단, `;` 뒤 명령 조용히 미실행). 구분선은 `echo "==="` 따옴표 필수, 미실행 의심 시 후속 명령 출력 존재부터 확인.
- **변수에 명령 전체를 담아 `$CMD args` 로 실행 = zsh 는 word split 안 함** — `D="docker exec … psql …"; $D -c "…"` 가 `command not found` 인데 리다이렉트는 실행돼 **빈 파일이 생기며 성공처럼 보인다**. 명령 조립은 **함수** (`pgq() { docker exec ...; }`) 또는 `${=D}` (word split 강제).
- **grep 결과를 담은 변수를 인자 목록으로 넘겨도 word split 안 함** (`perl -pi … $files`, `sed -i … $files` → `Can't open …: File name too long` 으로 전량 미실행인데 뒤 echo 는 정상). 규칙 = `${=files}` 또는 `grep -rl … | xargs perl -pi …`. 대량 치환 뒤엔 반드시 **치환 대상 패턴의 잔존 0건** 을 실측.
- **`set -- $spec` / `for x in $list` 도 word split 안 함** (`spec="A B"; set -- $spec` 뒤 `$1` = `"A B"` 통째, `$2` 빈 값 → 조립한 URL 이 깨져 curl 이 HTTP 000 을 조용히 찍는다). 공백 구분 문자열은 **`set -- ${=spec}`** 또는 처음부터 배열 (`spec=(A B)`). 결과가 전부 000/0건이면 명령 조립부터 의심.
- **`path`, `cdpath`, `fpath`, `manpath`, `module_path`, `mailpath` 는 대문자 환경변수와 묶인 (tied) 배열** — `for path in /api/a /api/b; do curl …` 처럼 대입하거나 루프 변수로 쓰면 첫 반복부터 `command not found: curl`, 그 뒤 모든 외부 명령이 죽는다. 경로 변수명은 `target_path`, `ep`, `route` 로 (bash 겸용 스크립트도 같은 이름을 피한다).
- **`UID`/`EUID`/`GID` 등 zsh 예약 정수 변수** — 문자열/UUID 할당 시 산술 평가 → `bad math expression`. 임의 변수는 **비예약 이름** (`USERID`/`U`/`acc`). 전체 목록은 `man zshparam`.
- **`status` 는 zsh read-only 특수변수** (`$?` 의 alias) — 할당 시 `read-only variable: status` 로 즉사 (`zsh -n` 으로는 안 잡히는 런타임 에러). 상태 변수는 `dstat`/`state`, 배치 스크립트는 첫 사이클 완주를 실제 로그로 확인.
- **alias 와 같은 이름으로 함수 정의 불가** — 프로파일에 `alias g=…` 가 있으면 `g() { …; }` 가 `defining function based on alias` + `parse error near ()` 로 eval 전체가 시작도 안 된다. 한 글자, 짧은 이름은 alias 충돌 가능성이 커서 함수명은 `probe_api` 처럼 서술형으로.
- **`grep … | sort` / `| tail` 로 끝나는 파이프는 0건이 rc 0** (마지막 명령 종료코드). 판정은 rc 가 아니라 **출력 존재** 로, 0건이 의미 있는 유닛은 `|| echo "(none)"` 류로 빈 출력 착시를 없앨 것.
- **BSD `sed` 는 `\s`, `\d`, `\w`, `\b` 를 모른다 — 마스킹용 sed 가 조용히 무치환으로 통과** (exit 0, 원문 그대로 출력). 규칙 = macOS sed 패턴은 `[[:space:]]`, `[0-9]`, `[[:alnum:]_]`, **가림, 마스킹 용도는 sed 말고 python `re.sub`**. 마스킹 파이프라인은 본 데이터에 쓰기 전에 **가려져야 할 표본 한 줄로 치환이 실제로 일어나는지** 먼저 확인한다. 큰 바이너리 (GGUF 등) 가 있는 경로는 `grep -rl` 대신 이름 기반 `find -name` 으로.
- **psql 메타커맨드를 zsh `echo` 로 파이프하면 `\d`, `\e`, `\x` 가 이스케이프 시퀀스로 먹힌다** — `echo "\dt app.x" | psql` 이 깨진 메타커맨드로 들어가거나 (`\e` = ESC 제어문자) 조용히 아무것도 안 한다. 규칙 = psql 입력 조립은 **`printf '%s\n' '\dt …'`** 또는 heredoc (`<<'SQL'`) 으로, `echo` 는 SQL 평문에만.
- **문자열 비교는 `[[ $a < $b ]]` — `[ "$a" \< "$b" ]` 는 zsh 내장 `[` 에서 `condition expected` 오류 (rc 2) 라 while 대기 루프가 첫 판정에서 바로 끝난다** — 대기 루프가 발화 전에 끝나 [미발화] 로 오판한다. 규칙 = 대기, 폴링 루프는 시작 직후 조건이 의도대로 참인지 한 번 echo 로 확인하고, 시각 대기는 python sleep 으로. 루프가 즉시 끝나면 결과가 아니라 조건식부터 의심.
- **BSD `cat` 에 `-A` 가 없다** (`-vet` 로), **`git worktree add` 에 `-q` 가 없다**, **`git rev-parse --short A B` 는 인자 둘이면 `Needed a single revision` 으로 죽는다** (`--short` 는 `--verify` 라 하나씩, 실사고에서 두 번 밟음) — 없는 옵션은 명령 자체가 실패해 `&&` 뒤 단계가 조용히 빠진다 (실사고 2026-10-05). 체인 뒤 결과물 실존으로 판정.
- **macOS 에는 `timeout` (GNU coreutils) 이 없다** — `timeout 280 python3 …` 은 `command not found` 로 아무것도 실행하지 않는데, 뒤에 `| grep` 이 붙어 있으면 빈 출력이라 [결과 없음] 으로 오판한다 (2026-10-04 실측, 두 번). 시간 제한은 Bash 툴의 `timeout` 파라미터나 `python3 -c 'subprocess.run(..., timeout=)'` 로, 긴 작업은 nohup + 완료 파일 watcher 로.
- **서브셸 `(cd $LC && cmd $(pwd)/out …)` 의 `$(pwd)` 는 cd 뒤 값** — 변환기에 `/private/tmp/other-project/work/…` 같은 엉뚱한 경로가 들어가 `is not a directory` 로 즉사 (2026-10-07). 호출자 경로는 cd 전에 `HERE=$(pwd)` 로 받아 두고 서브셸 안에선 `$HERE` 만 쓴다. **Claude Code Bash `run_in_background` 도 `timeout` 파라미터에 묶인다 (기본 2분, 최대 2시간, 자식까지 죽는다)** — 그보다 긴 체인은 처음부터 `nohup … </dev/null > log 2>&1 &` + Monitor, 갈아탈 땐 원본 TaskStop 먼저.
- rsync glob 인자는 따옴표 필수 (`no matches found` 전체 실패).
- **BSD `date -j -f` 미지정 필드 = 현재 시각 값으로 채워짐** (시간 창 계산이 조용히 어긋남). **포맷과 입력을 풀 필드로** (`"%Y-%m-%dT%H:%M:%S"` + `"...T00:00:00"`). 시간 계산 스크립트는 반드시 드라이런으로 출력 시각 검증.
- **`BASH_ENV` (예: `~/.config/path.sh`) 가 비대화형 `/bin/bash` 의 PATH 를 되돌린다** — `PATH=<스텁>:$PATH bash hook.sh` 로 python3 스텁 (exit 1) 을 앞에 둬도 bash 가 시작하며 BASH_ENV 를 읽어 PATH 를 다시 세팅해 진짜 python3 가 잡힌다. [fallback 경로 측정] 이 조용히 python 경로가 돼 허수 PASS. 규칙 = 호스트에서 PATH 스텁으로 fallback 을 재현할 땐 `env -u BASH_ENV PATH=<스텁>:$PATH /bin/bash …`, 스텁이 실제로 잡혔는지 스크립트 안에서 `command -v python3` 출력으로 확인. 컨테이너 (bash:3.2 이미지) 는 BASH_ENV 가 없어 무관 (실사고 2026-10-13, secret 출력 차단 hook 리뷰).
- **이 환경의 zsh 에서 `log` 는 셸 함수다** — `log show …` 가 `too many arguments` 로 죽고 unified log 조회가 0건처럼 보인다. 규칙 = **`/usr/bin/log show`** 로 절대경로 호출 (`type log` 로 확인). 같은 가족 = 프로파일 alias, 함수와 이름이 겹치는 외부 명령은 `command <이름>` 또는 절대경로 (실사고 2026-10-13)
- **zsh 엔 `compgen` 이 없다 - `export $(compgen -e | grep '^STAGING_')` 는 인자 없는 `export` 가 돼 환경 전체 (세션 토큰 포함) 를 출력하고, 뒤 체인은 `compgen` 127 로 끊긴다** - 접두 변수 일괄 export 는 `export ${(M)${(k)parameters}:#STAGING_*}` 또는 wrapper 를 자식에서 다시 `source`. 출력에 `PATH=`, `TOKEN=` 줄이 보이면 export 오용부터 의심 (실사고 2026-10-21)
- **BSD grep, ugrep 래퍼 모두 정규식 반복 상한이 255 다** — `grep -oE '.{0,400}'` 류는 `maximum repetition exceeds 255` 로 명령 자체가 죽어 (rc 2) 뒤 파이프가 빈 출력이 된다 ([매치 없음] 오판). 긴 창 추출은 `{0,255}` 이하로 쪼개거나 python `re` 로 (실사고 2026-10-17, 번들 js 안 매처 함수 추출)
