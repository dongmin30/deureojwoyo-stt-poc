// Typed API client — matches .omc/plans/poc-contract.md exactly.

export interface Status {
  stt_provider: string
  llm_provider: string
  mock: boolean
  stt_model: string
  llm_model: string
  /** 외부 공유 시 접속 코드가 걸려 있는지 / 이 브라우저가 이미 통과했는지 */
  auth_required?: boolean
  authed?: boolean
}

export interface Region { id: string; name: string; count: number }
export interface Scenario { id: string; name: string; system_prompt: string; greeting: string }
export interface SearchDocMeta { id: string; category: string; title: string }
export interface ChatTurn { role: 'user' | 'assistant'; content: string }

export interface TranscribeResult {
  run_id: number | string
  duration_s: number
  a: { text: string; ms: number; cer: number | null }
  b: {
    raw_text: string
    dialect_text: string
    standard_text: string
    /** LLM 이 제안했지만 발음이 달라(지어낸 말·빠뜨린 말) 채택하지 않은 수정 */
    rejected?: { from: string; to: string; reason: string }[]
    ms: number
    cer: number | null
    cer_standard: number | null
    steps_ms: { preprocess: number; stt: number; correct: number }
  }
  convert_ms: number
  audio: { raw_url: string; pre_url: string }
}

export interface SearchResultItem { id: string; title: string; snippet: string; score: number }
export interface SearchResponse { query: string; results: SearchResultItem[]; hit: boolean | null }
export interface CounselResponse { reply: string; user_text: string }

export type FeedbackTarget = 'a_text' | 'b_text' | 'a_result' | 'b_result'

export interface PathSummary {
  text_ok_rate: number | null
  text_fb_n: number
  result_ok_rate: number | null
  result_fb_n: number
  avg_cer: number | null
  cer_n: number
  hit3_rate: number | null
  hit3_n: number
}
export interface Summary {
  total_runs: number
  by_sim: { counsel: number; search: number }
  a: PathSummary
  b: PathSummary
}

export interface TranscribeParams {
  audio?: Blob
  audioName?: string
  sample?: string
  region: string
  isElderly: boolean
  sim: 'counsel' | 'search'
  scenarioId?: string
  reference?: string
  participantCode?: string
  ageGroup?: string
  participantRegion?: string
  consent: boolean
  preLoudnorm: boolean
  preVad: boolean
  preTempo: boolean
  preDenoise: boolean
}

async function parseError(res: Response): Promise<string> {
  try {
    const j = await res.json()
    if (typeof j?.detail === 'string') return j.detail
    if (Array.isArray(j?.detail)) return j.detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join('; ')
    return JSON.stringify(j)
  } catch {
    return `서버 오류 (${res.status})`
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, init)
  } catch {
    throw new Error('서버에 연결할 수 없습니다. 인터넷 연결을 확인하거나, 운영자에게 서버가 켜져 있는지 물어봐 주세요.')
  }
  if (!res.ok) throw new Error(await parseError(res))
  return (await res.json()) as T
}

function postJson<T>(url: string, body: unknown): Promise<T> {
  return request<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export const api = {
  status: () => request<Status>('/api/status'),
  login: (code: string) => postJson<{ ok: boolean }>('/api/login', { code }),
  regions: () => request<Region[]>('/api/regions'),
  scenarios: () => request<Scenario[]>('/api/scenarios'),
  searchDocs: () => request<SearchDocMeta[]>('/api/search_docs'),
  samples: () => request<string[]>('/api/samples'),

  transcribe(p: TranscribeParams): Promise<TranscribeResult> {
    const f = new FormData()
    if (p.audio) f.append('audio', p.audio, p.audioName ?? 'recording.webm')
    if (p.sample) f.append('sample', p.sample)
    f.append('region', p.region)
    f.append('is_elderly', String(p.isElderly))
    f.append('sim', p.sim)
    if (p.scenarioId) f.append('scenario_id', p.scenarioId)
    if (p.reference) f.append('reference', p.reference)
    if (p.participantCode) f.append('participant_code', p.participantCode)
    if (p.ageGroup) f.append('age_group', p.ageGroup)
    if (p.participantRegion) f.append('participant_region', p.participantRegion)
    f.append('consent', String(p.consent))
    f.append('pre_loudnorm', String(p.preLoudnorm))
    f.append('pre_vad', String(p.preVad))
    f.append('pre_tempo', String(p.preTempo))
    f.append('pre_denoise', String(p.preDenoise))
    return request<TranscribeResult>('/api/transcribe', { method: 'POST', body: f })
  },

  counsel: (b: { run_id: number | string; path: 'a' | 'b'; scenario_id: string; history: ChatTurn[] }) =>
    postJson<CounselResponse>('/api/counsel', b),
  search: (b: { run_id: number | string; path: 'a' | 'b'; target_doc_id?: string }) =>
    postJson<SearchResponse>('/api/search', b),
  feedback: (b: { run_id: number | string; target: FeedbackTarget; ok: boolean }) =>
    postJson<{ ok: boolean }>('/api/feedback', b),
  summary: () => request<Summary>('/api/summary'),
}
