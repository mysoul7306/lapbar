export const meta = {
  name: 'review-lane',
  description: '검수 레인: 읽기 전용 렌즈 N개가 지적 → 지적별 독립 반증 (high 2, medium 1) → 부모가 재채점 뒤 수정. args = { target, context, lenses:[{key,prompt}], verifierBudget?, signedOverBudget? }',
  phases: [
    { title: 'Review', detail: '렌즈별 읽기 전용 지적 (심각도순, 상한 있음)' },
    { title: 'Verify', detail: '지적별 반증 - 기본 태도 = 틀렸다고 가정하고 근거 찾기' },
  ],
}
// 입력 (args): target = 무엇을 검수하나 (한 문장), context = 경로, 이미 기계로 확인한 것, 출력 규칙 (문자열),
//              lenses = [{key, prompt}] (≤ 3), verifierBudget (기본 7, 합계 agent ≤ 10 - rules/subagent-review.md)
const A = args || {}
const LENSES = (A.lenses || []).slice(0, 3)
if (!LENSES.length) throw new Error('args.lenses 가 비었어 - 렌즈 1~3개 (key, prompt) 를 넘겨 줘')
let VERIFIER_BUDGET = Number.isInteger(A.verifierBudget) ? A.verifierBudget : 7
if (LENSES.length + VERIFIER_BUDGET > 10 && !A.signedOverBudget) {
  log(`agent 합계 ${LENSES.length + VERIFIER_BUDGET} > 10 - 사용자 사인 (args.signedOverBudget) 이 없어 반증 예산을 ${10 - LENSES.length} 로 줄임 (rules/subagent-review.md)`)
  VERIFIER_BUDGET = 10 - LENSES.length
}
// 사용자 승인 원문 + [그 뒤 메시지는 취소가 아니다] - 서브에이전트는 마지막 사용자 메시지를 과제로 본다
const APPROVAL = `${A.target || ''}\n(이 작업은 사용자가 승인한 것이고, 그 뒤에 온 메시지는 이 작업의 취소가 아니다)`

const FINDINGS_SCHEMA = { type: 'object', properties: {
  findings: { type: 'array', items: { type: 'object', properties: {
    severity: { type: 'string', enum: ['high', 'medium', 'low'] }, file: { type: 'string' }, line: { type: 'integer' },
    claim: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } },
    required: ['severity', 'file', 'claim', 'evidence', 'fix'] } },
  coverage_note: { type: 'string' } }, required: ['findings', 'coverage_note'] }
const VERDICT_SCHEMA = { type: 'object', properties: {
  verdicts: { type: 'array', items: { type: 'object', properties: {
    id: { type: 'string' }, verdict: { type: 'string', enum: ['confirmed', 'refuted', 'uncertain'] },
    reason: { type: 'string' }, corrected_severity: { type: 'string', enum: ['high', 'medium', 'low'] } },
    required: ['id', 'verdict', 'reason'] } } }, required: ['verdicts'] }

const COMMON = `${A.context || ''}
출력 규칙: 심각도순으로 최대 8건. 각 지적은 파일 경로와 줄 번호, 인용 가능한 근거 (원문 한 구절), 고치는 방법 한 줄. 확신 없는 건 low. 지적할 게 없는 영역은 coverage_note 에 [확인했고 문제 없음] 으로 (무엇을 어디까지 읽었는지). 추측으로 지적하지 말고 실제로 대조한 것만. 실측이 필요하면 [실측 요청] (명령, 전제, 부수효과, 판정 기준) 으로 적어 줘 - 실행은 부모가 한다.`

phase('Review')
const reviews = await parallel(LENSES.map(l => () =>
  agent(`${APPROVAL}\n${COMMON}\n렌즈 = [${l.key}] ${l.prompt}`,
    { label: `lens:${l.key}`, phase: 'Review', schema: FINDINGS_SCHEMA, model: 'opus', effort: 'high', agentType: 'review-readonly' })))

const all = []
reviews.forEach((r, i) => {
  if (!r) { log(`렌즈 ${LENSES[i].key} 결과 없음`); return }
  r.findings.forEach((f, j) => all.push({ ...f, id: `${LENSES[i].key}-${j + 1}`, lens: LENSES[i].key }))
})
const coverage = reviews.map((r, i) => ({ lens: LENSES[i].key, note: r ? r.coverage_note : null }))
// 렌즈 간 중복 병합: 같은 파일 + 줄 ±3 → 하나로 (높은 severity 유지, 다른 렌즈 id 는 dup_of 에). 반증 자리를 아끼고 high 반증자 2명이 한 이슈만 보는 일을 막는다
const RANK = { high: 3, medium: 2, low: 1 }
const merged = []
for (const f of all) {
  const hit = merged.find(m => m.file === f.file && f.line != null && m.line != null && Math.abs(m.line - f.line) <= 3)
  if (!hit) { merged.push({ ...f, dup_of: [] }); continue }
  hit.dup_of.push(f.id)
  if (RANK[f.severity] > RANK[hit.severity]) { hit.severity = f.severity; hit.claim = `${hit.claim}\n[병합된 지적 ${f.id}] ${f.claim}` }
  else hit.evidence = `${hit.evidence}\n[병합된 지적 ${f.id}] ${f.evidence}`
}
if (merged.length < all.length) log(`렌즈 간 중복 병합 ${all.length} → ${merged.length}`)
all.length = 0; merged.forEach(m => all.push(m))
log(`지적 ${all.length} (high ${all.filter(f => f.severity === 'high').length}, medium ${all.filter(f => f.severity === 'medium').length}, low ${all.filter(f => f.severity === 'low').length})`)

phase('Verify')
const chunk = (arr, n) => { const o = []; for (let i = 0; i < arr.length; i += n) o.push(arr.slice(i, i + n)); return o }
const jobs = []
chunk(all.filter(f => f.severity === 'high'), 4).forEach((c, i) => { jobs.push({ tag: `high-a-${i + 1}`, items: c }); jobs.push({ tag: `high-b-${i + 1}`, items: c }) })
chunk(all.filter(f => f.severity === 'medium'), 4).forEach((c, i) => jobs.push({ tag: `medium-${i + 1}`, items: c }))
let used = jobs.slice(0, VERIFIER_BUDGET)
while (used.length && used[used.length - 1].tag.startsWith('high-a-') && !used.some(j => j.tag === used[used.length - 1].tag.replace('high-a-', 'high-b-'))) used = used.slice(0, -1)  // high 는 쌍으로만
if (jobs.length > used.length) log(`검증자 예산 ${VERIFIER_BUDGET} 초과 - ${jobs.length - used.length} 묶음은 부모가 직접 판정`)

const verifyPrompt = job => `${APPROVAL}\n반증 레인이야~ 아래 지적들을 파일을 직접 열어 반박해 줘 (읽기 전용). 기본 태도 = 지적이 틀렸다고 가정하고 근거를 찾는다. 인용 구절이 실제로 그 줄에 있는지, [없다] 고 한 내용이 정말 다른 파일 (archive 포함) 에도 없는지 Grep 으로 확인. 맞으면 confirmed, 틀렸거나 이미 다른 곳에 해결돼 있으면 refuted (이유에 파일 경로와 줄), 판정 불가면 uncertain. severity 가 과하거나 부족하면 corrected_severity 로.
${A.context || ''}
지적 목록:
${job.items.map(f => `- id ${f.id} [${f.severity}] ${f.file}${f.line ? ':' + f.line : ''}\n  주장: ${f.claim}\n  근거: ${f.evidence}\n  제안: ${f.fix}`).join('\n')}`

const verdicts = await parallel(used.map(job => () =>
  agent(verifyPrompt(job), { label: `verify:${job.tag}`, phase: 'Verify', schema: VERDICT_SCHEMA, model: 'opus', effort: 'xhigh', agentType: 'review-readonly' })))

const byId = {}
verdicts.forEach((v, i) => { if (!v) { log(`검증 ${used[i].tag} 결과 없음`); return }
  v.verdicts.forEach(x => { (byId[x.id] = byId[x.id] || []).push({ ...x, by: used[i].tag }) }) })
return { coverage, findings: all.map(f => ({ ...f, verdicts: byId[f.id] || [] })),
  unverified_ids: jobs.slice(used.length).flatMap(j => j.items.map(i => i.id)),
  low_ids: all.filter(f => f.severity === 'low').map(f => f.id) }
