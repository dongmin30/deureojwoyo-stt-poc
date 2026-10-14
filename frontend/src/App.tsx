import { useCallback, useEffect, useRef, useState } from 'react'
import AccessGate from './AccessGate'
import { api } from './api'
import type {
  ChatTurn, FeedbackTarget, Region, Scenario, SearchDocMeta, SearchResponse, Status, TranscribeResult,
} from './api'
import { charDiff } from './diff'
import InputPanel, { loadParticipant, saveParticipant } from './InputPanel'
import type { InputParams, Participant } from './InputPanel'
import Summary from './Summary'
import { Audio, Feedback, Icon, Logo, Progress, STAGES, Segs, SideHead, fbKey, pct, speak } from './parts'
import type { FbMap, RunId } from './parts'

type Tab = 'counsel' | 'search'
type Path = 'a' | 'b'

interface Bubble {
  id: string
  role: 'user' | 'assistant'
  text: string
  greeting?: boolean
  runId?: RunId
  standard?: string   // B only: standard-language sentence
}

interface SearchState { runId: RunId; targetId?: string; a: SearchResponse; b: SearchResponse }

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))
let seq = 0
const uid = () => `b${++seq}`

const STT_NAMES: Record<string, string> = { openai: 'OpenAI', local: '로컬 Whisper' }
const LLM_NAMES: Record<string, string> = { anthropic: 'Claude', openai: 'OpenAI', ollama: '로컬 LLM' }

export default function App() {
  const [path, setPath] = useState(window.location.pathname)
  const [tab, setTab] = useState<Tab>('counsel')
  const [status, setStatus] = useState<Status | null>(null)
  const [participant, setParticipant] = useState<Participant>(loadParticipant)
  const [regions, setRegions] = useState<Region[]>([])
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [docs, setDocs] = useState<SearchDocMeta[]>([])
  const [samples, setSamples] = useState<string[]>([])
  const [scenarioId, setScenarioId] = useState('')
  const [targetDoc, setTargetDoc] = useState('')
  const [error, setError] = useState('')

  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState(0)
  const [times, setTimes] = useState<(number | null)[]>([])
  const progressRef = useRef<HTMLParagraphElement>(null)

  // 탭별 마지막 인식 결과 (탭을 오가도 유지)
  const [results, setResults] = useState<Partial<Record<Tab, TranscribeResult>>>({})
  const [panes, setPanes] = useState<{ a: Bubble[]; b: Bubble[] }>({ a: [], b: [] })
  const panesRef = useRef(panes)
  panesRef.current = panes
  const [search, setSearch] = useState<SearchState | null>(null)
  const [fb, setFb] = useState<FbMap>({})

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // 외부 공유 시 접속 코드: QR/링크의 ?code= 로 자동 통과, 아니면 코드 입력 화면
  const [locked, setLocked] = useState(false)

  const loadAll = useCallback(() => {
    api.regions().then(setRegions).catch(e => setError(errMsg(e)))
    api.samples().then(setSamples).catch(() => setSamples([]))
    api.searchDocs().then(setDocs).catch(e => setError(errMsg(e)))
    api.scenarios().then(s => { setScenarios(s); if (s[0]) setScenarioId(s[0].id) }).catch(e => setError(errMsg(e)))
  }, [])

  const boot = useCallback(async () => {
    const url = new URL(window.location.href)
    const code = url.searchParams.get('code')
    if (code) {
      await api.login(code).catch(() => undefined)  // 틀린 코드면 아래에서 입력 화면이 뜬다
      url.searchParams.delete('code')
      window.history.replaceState(null, '', url.pathname + url.search + url.hash)  // 주소창에 코드를 남기지 않음
    }
    const s = await api.status().catch(() => null)
    setStatus(s)
    if (s?.auth_required && !s.authed) { setLocked(true); return }
    setLocked(false)
    loadAll()
  }, [loadAll])

  useEffect(() => { void boot() }, [boot])

  const greetingPanes = useCallback((id: string) => {
    const sc = scenarios.find(s => s.id === id)
    const mk = (): Bubble[] => (sc ? [{ id: uid(), role: 'assistant', text: sc.greeting, greeting: true }] : [])
    return { a: mk(), b: mk() }
  }, [scenarios])

  useEffect(() => {
    if (scenarioId) setPanes(greetingPanes(scenarioId))
  }, [scenarioId, greetingPanes])

  const updateParticipant = (p: Participant) => { setParticipant(p); saveParticipant(p) }

  const nav = (to: string) => {
    window.history.pushState(null, '', to)
    setPath(to)
  }

  const resetConversation = () => {
    if (scenarioId) setPanes(greetingPanes(scenarioId))
    setResults(r => ({ ...r, counsel: undefined }))
  }

  const sendFeedback = async (runId: RunId, target: FeedbackTarget, ok: boolean) => {
    const key = fbKey(runId, target)
    const prev = fb[key]
    setFb(m => ({ ...m, [key]: ok }))
    try {
      await api.feedback({ run_id: runId, target, ok })
    } catch (e) {
      setFb(m => {
        const n = { ...m }
        if (prev === undefined) delete n[key]; else n[key] = prev
        return n
      })
      setError(errMsg(e))
    }
  }

  const toHistory = (bs: Bubble[]): ChatTurn[] =>
    bs.filter(b => !b.greeting).map(b => ({
      role: b.role,
      content: b.role === 'user' && b.standard !== undefined ? b.standard : b.text,
    }))

  async function run(p: InputParams) {
    if (!participant.consent) { setError('먼저 음성 전송 동의 체크박스를 눌러 주세요.'); return }
    setError('')
    setBusy(true)
    setStage(0)
    setTimes([])
    // 진행 표시가 화면 밖이면 보이도록 (특히 모바일)
    window.setTimeout(() => progressRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50)
    const currentTab = tab
    const timer = window.setInterval(() => setStage(s => Math.min(s + 1, STAGES.length - 1)), 1200)
    let result: TranscribeResult
    try {
      result = await api.transcribe({
        audio: p.audio, audioName: p.audioName, sample: p.sample,
        region: p.region, isElderly: p.isElderly, sim: currentTab,
        scenarioId: currentTab === 'counsel' ? scenarioId : undefined,
        reference: p.reference || undefined,
        participantCode: participant.code || undefined,
        ageGroup: participant.age || undefined,
        participantRegion: participant.region || undefined,
        consent: participant.consent,
        preLoudnorm: p.preLoudnorm, preVad: p.preVad, preTempo: p.preTempo, preDenoise: p.preDenoise,
      })
    } catch (e) {
      window.clearInterval(timer)
      setBusy(false)
      setError(errMsg(e))
      return
    }
    window.clearInterval(timer)
    setResults(r => ({ ...r, [currentTab]: result }))
    setStage(STAGES.length)
    const base = [result.convert_ms, result.b.steps_ms.preprocess, result.b.steps_ms.stt, result.b.steps_ms.correct]
    setTimes(base)
    const t0 = performance.now()
    try {
      if (currentTab === 'counsel') await runCounsel(result)
      else await runSearch(result)
    } catch (e) {
      setError(errMsg(e))
    }
    setTimes([...base, performance.now() - t0])
    setBusy(false)
  }

  async function runCounsel(result: TranscribeResult) {
    const cur = panesRef.current
    const histA = toHistory(cur.a)
    const histB = toHistory(cur.b)
    const userA: Bubble = { id: uid(), role: 'user', text: result.a.text, runId: result.run_id }
    const userB: Bubble = {
      id: uid(), role: 'user', text: result.b.dialect_text, standard: result.b.standard_text, runId: result.run_id,
    }
    setPanes(p => ({ a: [...p.a, userA], b: [...p.b, userB] }))
    const call = (path: Path, history: ChatTurn[]) =>
      api.counsel({ run_id: result.run_id, path, scenario_id: scenarioId, history })
    const [ra, rb] = await Promise.allSettled([call('a', histA), call('b', histB)])
    const add = (r: PromiseSettledResult<{ reply: string }>): Bubble | null =>
      r.status === 'fulfilled' ? { id: uid(), role: 'assistant', text: r.value.reply, runId: result.run_id } : null
    const ba = add(ra)
    const bb = add(rb)
    setPanes(p => ({ a: ba ? [...p.a, ba] : p.a, b: bb ? [...p.b, bb] : p.b }))
    const fail = [ra, rb].find((r): r is PromiseRejectedResult => r.status === 'rejected')
    if (fail) throw fail.reason
  }

  async function runSearch(result: TranscribeResult) {
    const target = targetDoc || undefined
    const [a, b] = await Promise.all([
      api.search({ run_id: result.run_id, path: 'a', target_doc_id: target }),
      api.search({ run_id: result.run_id, path: 'b', target_doc_id: target }),
    ])
    setSearch({ runId: result.run_id, targetId: target, a, b })
  }

  const top = (
    <>
      <Header path={path} nav={nav} tab={tab} setTab={setTab} status={status} />
      {status?.mock && (
        <div className="mock-banner" role="note">
          <span aria-hidden="true">⚠</span>
          <p><b>테스트 모드:</b> 실제 음성을 인식하지 않고 정해진 예시 문장을 보여 줍니다.
            실제로 쓰려면 <code>.env</code> 에 <code>STT_PROVIDER=local</code>(무료, PC에서 인식)을 넣고 다시 실행하세요 (README 참고).</p>
        </div>
      )}
    </>
  )

  if (locked) {
    return <>{top}<AccessGate onPass={() => void boot()} /></>
  }

  if (path === '/summary') {
    return <>{top}<main className="page"><Summary /></main></>
  }

  const tr = results[tab]
  const extra = tab === 'counsel' ? (
    <label>상담 상황
      <select value={scenarioId} onChange={e => setScenarioId(e.target.value)}>
        {scenarios.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
    </label>
  ) : (
    <label><span>찾으려던 것 <small>(선택)</small></span>
      <select value={targetDoc} onChange={e => setTargetDoc(e.target.value)}>
        <option value="">선택 안 함</option>
        {docs.map(d => <option key={d.id} value={d.id}>[{d.category}] {d.title}</option>)}
      </select>
    </label>
  )

  return (
    <>
      {top}
      <main className="page">
        {error && (
          <div className="error" role="alert">
            <Icon name="alert" />
            <span>{error}</span>
            <button className="x" onClick={() => setError('')} aria-label="닫기">×</button>
          </div>
        )}

        <InputPanel participant={participant} onParticipant={updateParticipant} regions={regions} samples={samples}
          busy={busy} onRun={run} onError={setError} extra={extra} />

        {(busy || times.length > 0) && (
          <Progress ref={progressRef} stage={stage} busy={busy} times={times} last={tab === 'counsel' ? '답변' : '검색'} />
        )}

        {!tr && !busy && <p className="empty">녹음하거나 음성 파일을 올리면 결과가 여기에 나옵니다.</p>}

        {tr && <Results tr={tr} tab={tab} panes={panes} search={search} fb={fb} onSend={sendFeedback} busy={busy}
          onReset={resetConversation} />}
      </main>
    </>
  )
}

function Header({ path, nav, tab, setTab, status }: {
  path: string; nav: (to: string) => void; tab: Tab; setTab: (t: Tab) => void; status: Status | null
}) {
  const onSummary = path === '/summary'
  const go = (t: Tab) => { setTab(t); if (onSummary) nav('/') }
  return (
    <header className="topbar">
      <div className="topbar-in">
        <a className="brand" href="/" onClick={e => { e.preventDefault(); nav('/') }}>
          <Logo size={32} />
          <span>들어줘요</span>
        </a>
        <nav className="tabs" aria-label="시뮬레이션 선택">
          <button className={!onSummary && tab === 'counsel' ? 'on' : ''} aria-current={!onSummary && tab === 'counsel' ? 'page' : undefined}
            onClick={() => go('counsel')}>AI 상담사</button>
          <button className={!onSummary && tab === 'search' ? 'on' : ''} aria-current={!onSummary && tab === 'search' ? 'page' : undefined}
            onClick={() => go('search')}>검색엔진</button>
          <a className={onSummary ? 'on' : ''} href="/summary" aria-current={onSummary ? 'page' : undefined}
            onClick={e => { e.preventDefault(); nav('/summary') }}>결과 요약</a>
        </nav>
        {status && (
          status.mock
            ? <span className="status warn">테스트 모드</span>
            : <span className="status">
                음성인식: {STT_NAMES[status.stt_provider] ?? status.stt_provider} {status.stt_model}
                <span className="sep" aria-hidden="true"> · </span>
                보정: {status.llm_provider === 'mock'
                  ? '방언 사전 (무료)'
                  : <>{LLM_NAMES[status.llm_provider] ?? status.llm_provider} {status.llm_model}</>}
              </span>
        )}
      </div>
    </header>
  )
}

function Results({ tr, tab, panes, search, fb, onSend, busy, onReset }: {
  tr: TranscribeResult
  tab: Tab
  panes: { a: Bubble[]; b: Bubble[] }
  search: SearchState | null
  fb: FbMap
  onSend: (runId: RunId, target: FeedbackTarget, ok: boolean) => void
  busy: boolean
  onReset: () => void
}) {
  const same = tr.a.text === tr.b.dialect_text
  const d = same ? null : charDiff(tr.a.text, tr.b.dialect_text)
  const searchData = search && search.runId === tr.run_id ? search : null
  return (
    <>
      {same && <p className="note">A와 B 결과가 같습니다 (화자 지역을 선택하면 B가 방언 사전을 사용합니다).</p>}
      <section className="cols" aria-label="A·B 결과">
        {(['a', 'b'] as const).map(k => (
          <article key={k} className={'side ' + k}>
            <SideHead kind={k} meta={`${Math.round(tr[k].ms)}ms`} />
            <div className="heard">
              <span className="lbl">AI가 들은 문장</span>
              <p className="said">
                {d ? <Segs segs={d[k]} kind={k} /> : (k === 'a' ? tr.a.text : tr.b.dialect_text) || '(인식된 내용 없음)'}
              </p>
              {k === 'b' && <p className="std"><b>표준어</b> {tr.b.standard_text}</p>}
              {k === 'b' && (
                <details className="fold"><summary>보정 전 인식 원문</summary><p>{tr.b.raw_text}</p></details>
              )}
              {k === 'b' && !!tr.b.rejected?.length && (
                <details className="fold guard">
                  <summary>AI 보정 중 {tr.b.rejected.length}곳은 발음이 달라 원문을 유지했어요</summary>
                  <ul>
                    {tr.b.rejected.map((r, i) => (
                      <li key={i}>
                        <span>{r.from || '(없음)'}</span> → <s>{r.to || '(삭제)'}</s> <small>{r.reason}</small>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {tr[k].cer != null && (
                <p className="cer">CER <strong>{pct(tr[k].cer)}</strong>
                  {k === 'b' && tr.b.cer_standard != null && <span> (표준어 기준 {pct(tr.b.cer_standard)})</span>}
                </p>
              )}
              <Audio label={k === 'a' ? '원본 소리' : '전처리 후 소리'} src={k === 'a' ? tr.audio.raw_url : tr.audio.pre_url} />
              <Feedback runId={tr.run_id} target={k === 'a' ? 'a_text' : 'b_text'}
                labels={['제가 한 말이 맞아요', '아니에요']} fb={fb} onSend={onSend} />
            </div>
            {tab === 'counsel'
              ? <ChatPane kind={k} bubbles={panes[k]} fb={fb} onSend={onSend} pending={busy} />
              : <SearchPane kind={k} data={searchData?.[k]} runId={searchData?.runId} target={searchData?.targetId ?? ''}
                  fb={fb} onSend={onSend} busy={busy} />}
          </article>
        ))}
      </section>
      {tab === 'counsel' && (
        <div className="center">
          <button className="btn" onClick={onReset} disabled={busy}><Icon name="refresh" size={20} />대화 처음부터</button>
        </div>
      )}
    </>
  )
}

function ChatPane({ kind, bubbles, fb, onSend, pending }: {
  kind: Path
  bubbles: Bubble[]
  fb: FbMap
  onSend: (runId: RunId, target: FeedbackTarget, ok: boolean) => void
  pending: boolean
}) {
  // 창 전체가 아니라 채팅 영역 안에서만 맨 아래로 스크롤 (scrollIntoView 는 페이지까지 움직임)
  const chatRef = useRef<HTMLDivElement>(null)
  useEffect(() => { const el = chatRef.current; if (el) el.scrollTop = el.scrollHeight }, [bubbles.length])
  return (
    <div className="pane-body">
      <span className="lbl">상담사 대화</span>
      <div className="chat" aria-live="polite" ref={chatRef}>
        {bubbles.map(b => (
          <div key={b.id} className={'msg ' + b.role}>
            {b.role === 'user' ? (
              <div className="bubble">
                <p className="text">{b.text || '(인식된 내용 없음)'}</p>
                {b.standard !== undefined && b.standard !== b.text && <p className="std"><b>표준어</b> {b.standard}</p>}
              </div>
            ) : (
              <>
                <div className="bubble"><p className="text">{b.text}</p></div>
                <div className="msg-actions">
                  <button className="btn speak" onClick={() => speak(b.text)}><Icon name="speaker" size={20} />읽어주기</button>
                  {b.runId !== undefined && (
                    <Feedback runId={b.runId} target={kind === 'a' ? 'a_result' : 'b_result'}
                      labels={['원하던 답이에요', '아니에요']} fb={fb} onSend={onSend} />
                  )}
                </div>
              </>
            )}
          </div>
        ))}
        {pending && (
          <div className="msg assistant">
            <div className="bubble"><span className="wait"><span className="spinner" aria-hidden="true" />답변 만드는 중…</span></div>
          </div>
        )}
      </div>
    </div>
  )
}

function SearchPane({ kind, data, runId, target, fb, onSend, busy }: {
  kind: Path
  data?: SearchResponse
  runId?: RunId
  target: string
  fb: FbMap
  onSend: (runId: RunId, target: FeedbackTarget, ok: boolean) => void
  busy: boolean
}) {
  if (!data || runId === undefined) {
    return <div className="pane-body"><p className="hint">{busy ? '검색 중…' : '검색 결과를 불러오지 못했습니다.'}</p></div>
  }
  return (
    <div className="pane-body">
      <div className="res-head">
        <span className="lbl">검색 결과</span>
        {target && data.hit !== null && (
          <span className={'badge ' + (data.hit ? 'hit' : 'miss')}>{data.hit ? '✓ 적중' : '✗ 미적중'}</span>
        )}
      </div>
      <p className="query">검색어: {data.query || '(없음)'}</p>
      {data.results.length === 0 && <p className="hint">검색 결과가 없어요</p>}
      <ol className="results">
        {data.results.map(r => (
          <li key={r.id}>
            <b>{r.title}</b>
            <span>{r.snippet}</span>
          </li>
        ))}
      </ol>
      <Feedback runId={runId} target={kind === 'a' ? 'a_result' : 'b_result'}
        labels={['원하던 결과예요', '아니에요']} fb={fb} onSend={onSend} />
    </div>
  )
}
