import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { Region } from './api'
import { Icon } from './parts'

export interface Participant { code: string; age: string; region: string; consent: boolean }

export interface InputParams {
  audio?: Blob
  audioName?: string
  sample?: string
  region: string
  isElderly: boolean
  reference: string
  preLoudnorm: boolean
  preVad: boolean
  preTempo: boolean
  preDenoise: boolean
}

const STORAGE_KEY = 'poc.participant'
const EMPTY: Participant = { code: '', age: '', region: '', consent: false }

export function loadParticipant(): Participant {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return { ...EMPTY, ...JSON.parse(raw) }
  } catch { /* storage unavailable */ }
  return EMPTY
}

export function saveParticipant(p: Participant) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(p)) } catch { /* ignore */ }
}

const AGE_GROUPS = ['60대', '70대', '80대 이상', '기타']

const MIC_HELP =
  '마이크를 사용할 수 없습니다. 브라우저의 마이크 권한을 허용했는지 확인해 주세요 ' +
  '(마이크는 http://localhost:5173 처럼 localhost 또는 HTTPS 주소에서만 동작합니다). ' +
  '마이크가 없으면 "음성 파일 올리기"를 사용하세요.'

interface Props {
  participant: Participant
  onParticipant: (p: Participant) => void
  regions: Region[]
  samples: string[]
  busy: boolean
  onRun: (p: InputParams) => void
  onError: (msg: string) => void
  /** 탭별 선택 항목 (상담 상황 / 찾으려던 것) */
  extra?: ReactNode
}

const AUDIO_EXT = /\.(wav|mp3|m4a|webm|ogg|flac|aac|mp4)$/i
function isAudio(f: File) {
  return f.type.startsWith('audio/') || AUDIO_EXT.test(f.name)
}

export default function InputPanel({ participant, onParticipant, regions, samples, busy, onRun, onError, extra }: Props) {
  const [region, setRegion] = useState('none')
  const [isElderly, setIsElderly] = useState(true)
  const [reference, setReference] = useState('')
  const [pre, setPre] = useState({ loudnorm: false, vad: true, tempo: false, denoise: false })
  const [recording, setRecording] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const [nudge, setNudge] = useState(false)
  const recRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<number | undefined>(undefined)
  const fileRef = useRef<HTMLInputElement>(null)
  const consentRef = useRef<HTMLInputElement>(null)
  const paramsRef = useRef<() => InputParams>(() => ({}) as InputParams)
  const runRef = useRef(onRun)
  runRef.current = onRun

  const consent = participant.consent

  const base = (): InputParams => ({
    region, isElderly, reference: reference.trim(),
    preLoudnorm: pre.loudnorm, preVad: pre.vad, preTempo: pre.tempo, preDenoise: pre.denoise,
  })
  paramsRef.current = base

  useEffect(() => () => {
    window.clearInterval(timerRef.current)
    recRef.current?.stream.getTracks().forEach(t => t.stop())
  }, [])

  const setP = (patch: Partial<Participant>) => {
    if (patch.consent) setNudge(false)
    onParticipant({ ...participant, ...patch })
  }

  /** 동의가 없으면 체크박스를 강조하고 false */
  function ensureConsent() {
    if (consent) return true
    setNudge(true)
    consentRef.current?.focus()
    return false
  }

  async function startRec() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      onError(MIC_HELP)
      return
    }
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      onError(MIC_HELP)
      return
    }
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'].find(m => MediaRecorder.isTypeSupported(m))
    const rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
    chunksRef.current = []
    rec.ondataavailable = e => { if (e.data.size) chunksRef.current.push(e.data) }
    rec.onstop = () => {
      stream.getTracks().forEach(t => t.stop())
      window.clearInterval(timerRef.current)
      setRecording(false)
      const blob = new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' })
      if (blob.size === 0) { onError('녹음된 소리가 없습니다. 다시 시도해 주세요.'); return }
      runRef.current({ ...paramsRef.current(), audio: blob, audioName: 'recording.webm' })
    }
    recRef.current = rec
    rec.start()
    setSeconds(0)
    setRecording(true)
    timerRef.current = window.setInterval(() => setSeconds(s => s + 1), 1000)
  }

  function toggleRec() {
    if (recording) recRef.current?.stop()
    else if (ensureConsent()) void startRec()
  }

  function pickFile() {
    if (ensureConsent()) fileRef.current?.click()
  }

  function onFile(f: File | null) {
    if (fileRef.current) fileRef.current.value = ''  // 같은 파일을 다시 골라도 동작하도록
    if (!f || !ensureConsent()) return
    onRun({ ...base(), audio: f, audioName: f.name })
  }

  // ---- 끌어서 놓기: 페이지 어디에 놓아도 분석한다 (작은 상자를 정확히 겨냥하지 않아도 되게)
  const [dragging, setDragging] = useState(false)
  const dropRef = useRef({ busy, recording, onFile, onError })
  dropRef.current = { busy, recording, onFile, onError }
  useEffect(() => {
    let depth = 0
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files')
    const enter = (e: DragEvent) => { if (!hasFiles(e)) return; depth++; setDragging(true) }
    const leave = (e: DragEvent) => { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) setDragging(false) }
    const over = (e: DragEvent) => { if (hasFiles(e)) e.preventDefault() }  // 브라우저가 파일을 열어 버리지 않도록
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth = 0
      setDragging(false)
      const { busy, recording, onFile, onError } = dropRef.current
      const f = e.dataTransfer?.files?.[0]
      if (!f) return
      if (busy || recording) { onError('분석이나 녹음이 끝난 뒤에 다시 놓아 주세요.'); return }
      if (!isAudio(f)) { onError(`음성 파일만 넣을 수 있어요 (wav, mp3, m4a 등). 넣은 파일: ${f.name}`); return }
      onFile(f)
    }
    window.addEventListener('dragenter', enter)
    window.addEventListener('dragleave', leave)
    window.addEventListener('dragover', over)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragenter', enter)
      window.removeEventListener('dragleave', leave)
      window.removeEventListener('dragover', over)
      window.removeEventListener('drop', drop)
    }
  }, [])

  function onSample(name: string) {
    if (!name || !ensureConsent()) return
    onRun({ ...base(), sample: name })
  }

  const mmss = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

  return (
    <section className="card input" aria-label="음성 입력">
      <div className={'consent' + (consent ? ' on' : '') + (nudge ? ' nudge' : '')}>
        <label>
          <input ref={consentRef} type="checkbox" checked={consent} onChange={e => setP({ consent: e.target.checked })} />
          <span>제 음성이 외부 AI 서비스로 전송되는 것에 동의합니다 <span className="req">(필수)</span></span>
        </label>
        {nudge && <span className="nudge-msg" role="alert">먼저 여기에 체크해 주세요</span>}
      </div>

      <div className="opts">
        <label>화자 지역
          <select value={region} onChange={e => setRegion(e.target.value)}>
            {regions.length === 0 && <option value="none">모름/표준어</option>}
            {regions.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </label>
        <div className="field">
          <span>고령자</span>
          <button type="button" role="switch" aria-checked={isElderly} className={'toggle' + (isElderly ? ' on' : '')}
            onClick={() => setIsElderly(v => !v)}>
            <span className="knob" aria-hidden="true" />
            {isElderly ? '예' : '아니요'}
          </button>
        </div>
        {extra}
      </div>

      <div className="actions">
        <button type="button" className={'big rec' + (recording ? ' on' : '')} disabled={busy && !recording}
          onClick={toggleRec} aria-pressed={recording}>
          {recording
            ? <><span className="rec-stop" aria-hidden="true" />녹음 끝내기 <span className="mono">{mmss}</span></>
            : <><Icon name="mic" size={28} />녹음하기</>}
        </button>
        <button type="button" className="big file" disabled={busy || recording} onClick={pickFile}>
          <Icon name="upload" size={28} />
          <span className="file-label">음성 파일 올리기<small>또는 여기로 끌어다 놓기</small></span>
        </button>
        <input ref={fileRef} className="file-input" type="file" accept=".wav,.mp3,.m4a,.webm,.ogg,.flac,audio/*"
          aria-label="음성 파일 선택" tabIndex={-1}
          onChange={e => onFile(e.target.files?.[0] ?? null)} />
      </div>
      {dragging && (
        <div className="drop-overlay" aria-hidden="true">
          <div className="drop-box">
            <Icon name="upload" size={48} />
            <b>여기에 음성 파일을 놓으세요</b>
            <span>{consent ? '놓으면 바로 분석합니다' : '먼저 위의 동의 체크가 필요합니다'}</span>
          </div>
        </div>
      )}
      {recording && <p className="hint center">말씀이 끝나면 "녹음 끝내기"를 누르세요.</p>}

      {samples.length > 0 && (
        <label className="sample">샘플 음성으로 해 보기
          <select value="" disabled={busy || recording} onChange={e => onSample(e.target.value)}>
            <option value="">골라 주세요</option>
            {samples.map(s => <option key={s}>{s}</option>)}
          </select>
        </label>
      )}

      <details className="more">
        <summary>추가 설정</summary>
        <div className="more-body">
          <label><span>정답 문장 <small>(적으면 정확도 CER 계산)</small></span>
            <input value={reference} onChange={e => setReference(e.target.value)} placeholder="실제로 말한 문장" />
          </label>
          <div className="row3">
            <label>참여자 코드
              <input value={participant.code} onChange={e => setP({ code: e.target.value })} placeholder="예: P01" />
            </label>
            <label>연령대
              <select value={participant.age} onChange={e => setP({ age: e.target.value })}>
                <option value="">선택 안 함</option>
                {AGE_GROUPS.map(a => <option key={a}>{a}</option>)}
              </select>
            </label>
            <label>참여자 지역
              <input value={participant.region} onChange={e => setP({ region: e.target.value })} placeholder="예: 대구" />
            </label>
          </div>
          <fieldset className="checks">
            <legend>B 전처리</legend>
            <label><input type="checkbox" checked={pre.loudnorm} onChange={e => setPre({ ...pre, loudnorm: e.target.checked })} /> 음량 정규화</label>
            <label><input type="checkbox" checked={pre.vad} onChange={e => setPre({ ...pre, vad: e.target.checked })} /> 긴 무음 줄이기</label>
            <label><input type="checkbox" checked={pre.tempo} onChange={e => setPre({ ...pre, tempo: e.target.checked })} /> 말 속도 보정</label>
            <label><input type="checkbox" checked={pre.denoise} onChange={e => setPre({ ...pre, denoise: e.target.checked })} /> 잡음 제거</label>
          </fieldset>
        </div>
      </details>
    </section>
  )
}
