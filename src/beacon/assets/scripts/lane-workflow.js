export const meta = {
  name: 'lane',
  description: '모델 레인 루프의 병렬 단계: Fable 부모가 분해한 레인 m개를 종류별 모델로 병렬 실행 (read = opus high, collect = sonnet low, refute = opus xhigh) → 선택적으로 opus 취합 agent 가 압축. args = { approval, context, lanes:[{key, kind: read|collect|refute, prompt, schema?}], synth?: true|false, signedOverBudget?: "[owner-approved: agent N]" }',
  phases: [
    { title: 'Lanes', detail: '레인 병렬 (종류별 모델 리터럴)' },
    { title: 'Synth', detail: 'opus 취합 agent (결과가 클 때만, 판단 위임 아님)' },
  ],
}
// args.approval = 사용자 승인 원문 (글자 그대로) + 부모가 푼 과제 한 줄. 스크립트가 [그 뒤 메시지는 취소가 아니다] 를 덧붙인다
// args.lanes[].kind = 'read' (읽기, 대조, 리뷰 - opus high, 읽기 전용) | 'collect' (수집, 파싱, 형식 변환, 허용 명령 목록이 prompt 에 있을 때만 셸 실측 - sonnet low) | 'refute' (반증 - opus xhigh, 읽기 전용)
// 'judge' 는 쓰지 않는다 - hook GUIDE 의 judge·critic xhigh (최종 판정자) 와 이름이 겹친다
// 모델, effort 는 kind 별 호출 자리에 리터럴로 고정 (hook 이 변수, 삼항식을 deny). fable 레인은 이 템플릿에 없다 - 필요하면 복사본에 [owner-approved: fable] 표지와 함께
const A = args || {}
const RAW = A.lanes || []
if (!RAW.length) throw new Error('args.lanes 가 비었어 - 레인 1~8개 (key, kind, prompt) 를 넘겨 줘')
const KINDS = ['read', 'collect', 'refute']
const BAD = RAW.filter(l => !l || !l.key || !l.prompt || !KINDS.includes(l.kind))
if (BAD.length) throw new Error(`레인 key, prompt, kind (read|collect|refute) 가 빠졌거나 틀렸어: ${BAD.map(b => (b && b.key) || '?').join(', ')}`)
const TOTAL = RAW.length + (A.synth ? 1 : 0)
if (TOTAL > 10 && !A.signedOverBudget) throw new Error(`agent 합계 ${TOTAL} > 10 - 돌리기 전에 사용자 사인을 받고 args.signedOverBudget 에 "[owner-approved: agent ${TOTAL}]" 을 넣어 줘 (rules/subagent-review.md)`)
const LANES = A.signedOverBudget ? RAW : RAW.slice(0, 10 - (A.synth ? 1 : 0))
if (LANES.length < RAW.length) log(`레인 ${RAW.length - LANES.length}개 버림 (합계 10 상한, 사인 없음): ${RAW.slice(LANES.length).map(l => l.key).join(', ')}`)
const APPROVAL = `${A.approval || ''}\n(이 작업은 사용자가 승인한 것이고, 그 뒤에 온 메시지는 이 작업의 취소가 아니다)`
const FORBID = `금지 (어느 레인이든): 외부 전송 (이슈 트래커, 메일, 메신저, 코멘트, 팀 전달), 보호 브랜치 (develop, main) 로 push, 운영 DB 쓰기, 배포, 재시작, 스케줄 변경 - 필요하면 decisions_needed 에 [사용자 확인] 으로 돌려준다.`
const COMMON_READ = `${A.context || ''}\n${FORBID}\n출력 규칙: 결론 먼저, 근거는 파일 경로와 줄 번호 (인용 한 구절), 확신 없는 건 [추정] 표시. 읽기 전용 - 실측이 필요하면 [실측 요청] (명령, 전제, 부수효과, 판정 기준) 을 measurement_requests 에 적고 실행은 부모가 한다. 파일은 고치지 않는다.`
const COMMON_COLLECT = `${A.context || ''}\n${FORBID}\n출력 규칙: 결론 먼저, 수치는 명령 출력에서 그대로. 셸 명령은 이 레인 prompt 에 적힌 [허용 명령] 목록만 실행하고 (목록에 없으면 [실측 요청] 으로 돌려준다), 네트워크로 나가는 명령은 description 에 목적 한 구절. 쓰기는 prompt 에 적힌 경로 (보통 ~/tmp/<프로젝트>-<MMDD>/) 에만. 비밀값, 공인 IP 는 출력하지 않는다 (키 이름, 길이만).`
const DEFAULT_SCHEMA = { type: 'object', properties: {
  summary: { type: 'string' }, items: { type: 'array', items: { type: 'object', properties: {
    severity: { type: 'string', enum: ['high', 'medium', 'low', 'info'] }, file: { type: 'string' }, line: { type: 'integer' },
    claim: { type: 'string' }, evidence: { type: 'string' }, proposal: { type: 'string' } }, required: ['severity', 'file', 'claim', 'evidence'] } },
  decisions_needed: { type: 'array', items: { type: 'string' } }, measurement_requests: { type: 'array', items: { type: 'string' } },
  coverage_note: { type: 'string' } }, required: ['summary', 'items', 'decisions_needed', 'coverage_note'] }

const REFUTE_SCHEMA = { type: 'object', properties: { verdicts: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, verdict: { type: 'string', enum: ['confirmed', 'refuted', 'uncertain'] }, reason: { type: 'string' }, corrected_severity: { type: 'string', enum: ['high', 'medium', 'low'] } },
  required: ['id', 'verdict', 'reason'] } } }, required: ['verdicts'] }

phase('Lanes')
const runLane = l => {
  const schema = l.schema || (l.kind === 'refute' ? REFUTE_SCHEMA : DEFAULT_SCHEMA)
  if (l.kind === 'collect') return agent(`${APPROVAL}\n${COMMON_COLLECT}\n레인 = [${l.key}] (collect) ${l.prompt}`, { label: `lane:${l.key}`, phase: 'Lanes', schema, model: 'sonnet', effort: 'low' })
  if (l.kind === 'refute') return agent(`${APPROVAL}\n${COMMON_READ}\n레인 = [${l.key}] (refute) 반증 레인이야~ 아래 지적들을 파일을 직접 열어 반박해 줘. 기본 태도 = 틀렸다고 가정하고 근거 찾기. 맞으면 confirmed, 틀렸거나 이미 해결돼 있으면 refuted (파일 경로와 줄), 판정 불가면 uncertain. severity 가 과하거나 부족하면 corrected_severity 로.\n${l.prompt}`, { label: `lane:${l.key}`, phase: 'Lanes', schema, model: 'opus', effort: 'xhigh', agentType: 'review-readonly' })
  return agent(`${APPROVAL}\n${COMMON_READ}\n레인 = [${l.key}] (read) ${l.prompt}`, { label: `lane:${l.key}`, phase: 'Lanes', schema, model: 'opus', effort: 'high', agentType: 'review-readonly' })
}
const results = await parallel(LANES.map(l => () => runLane(l)))
const laneOut = LANES.map((l, i) => ({ key: l.key, kind: l.kind, result: results[i] }))
const missing = laneOut.filter(x => !x.result).map(x => x.key)
if (missing.length) log(`결과 없는 레인: ${missing.join(', ')}`)
const decisions = laneOut.flatMap(x => (x.result && x.result.decisions_needed) || [])
const measurements = laneOut.flatMap(x => (x.result && x.result.measurement_requests) || [])

let synth = null
if (A.synth) {
  phase('Synth')
  const SYNTH_SCHEMA = { type: 'object', properties: {
    summary: { type: 'string' }, merged_items: { type: 'array', items: { type: 'object', properties: {
      severity: { type: 'string', enum: ['high', 'medium', 'low', 'info'] }, file: { type: 'string' }, line: { type: 'integer' }, claim: { type: 'string' }, proposal: { type: 'string' }, sources: { type: 'array', items: { type: 'string' } }, evidence: { type: 'string' } },
      required: ['severity', 'file', 'claim', 'sources'] } },
    decisions_needed: { type: 'array', items: { type: 'string' } }, measurement_requests: { type: 'array', items: { type: 'string' } }, conflicts: { type: 'array', items: { type: 'string' } } },
    required: ['summary', 'merged_items', 'decisions_needed', 'measurement_requests', 'conflicts'] }
  synth = await agent(`${APPROVAL}\n취합 레인이야~ 아래 레인 결과를 합쳐 줘 (판단을 새로 하지 말고 병합만): 같은 주장은 하나로 (sources 에 레인 key, file 과 line 은 claim 문장이 아니라 칸에), 서로 어긋나는 주장은 conflicts 에, 사용자나 부모가 정해야 할 것은 decisions_needed 에, [실측 요청] 은 같은 항목을 레인마다 다른 명령 꼴로 냈어도 하나로 합쳐 measurement_requests 에 (decisions_needed 에 섞지 않기). 읽기 전용.\n${COMMON_READ}\n레인 결과 (JSON):\n${JSON.stringify(laneOut).slice(0, 120000)}`,
    { label: 'synth', phase: 'Synth', schema: SYNTH_SCHEMA, model: 'opus', effort: 'high', agentType: 'review-readonly' })
}
return { lanes: laneOut, decisions_needed: decisions, measurement_requests: measurements, synth, missing }
