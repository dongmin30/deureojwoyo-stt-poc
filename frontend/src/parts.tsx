import { forwardRef } from 'react'
import type { Seg } from './diff'
import type { FeedbackTarget } from './api'

export type RunId = number | string
export type FbMap = Record<string, boolean>
export const fbKey = (runId: RunId, target: FeedbackTarget) => `${runId}:${target}`

export const pct = (v: number | null | undefined) => (v == null ? '-' : `${(v * 100).toFixed(1)}%`)

export function speak(text: string) {
  if (!('speechSynthesis' in window)) return
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(text)
  u.lang = 'ko-KR'
  window.speechSynthesis.speak(u)
}

const ICONS = {
  mic: <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></>,
  wave: <path d="M3 12h2M7 8v8M11 5v14M15 8v8M19 10v4M21 12h0" />,
  alert: <><circle cx="12" cy="12" r="9" /><path d="M12 7.5v5.5M12 16.5v.01" /></>,
  chart: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  speaker: <><path d="M11 5 6 9H3v6h3l5 4V5Z" /><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
  chat: <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z" />,
  download: <path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M4 20h16" />,
  refresh: <path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" />,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  target: <><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r=".6" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  sliders: <path d="M4 7h10M18 7h2M4 17h4M12 17h8M16 4.5v5M10 14.5v5" />,
  book: <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5v-15ZM4 20.5A2.5 2.5 0 0 0 6.5 23H20" />,
  spark: <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" />,
  arrow: <path d="M12 5v14m0 0-6-6m6 6 6-6" />,
  upload: <path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />,
}

export function Icon({ name, size = 22 }: { name: keyof typeof ICONS; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name]}
    </svg>
  )
}

/* brand mark: speech bubble carrying a sound wave */
export function Logo({ size = 44 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" className="logo">
      <defs>
        <linearGradient id="lg-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#8b6cff" />
          <stop offset=".55" stopColor="#c0408a" />
          <stop offset="1" stopColor="#ff8a5c" />
        </linearGradient>
      </defs>
      <path d="M24 4c11.6 0 20 7.6 20 17.5S35.6 39 24 39c-2 0-3.9-.2-5.7-.7L9 43.5l1.6-8.4C6.5 32 4 27.1 4 21.5 4 11.6 12.4 4 24 4Z"
        fill="url(#lg-mark)" />
      <path d="M14 21.5v0M19 16.5v10M24 13v17M29 16.5v10M34 21.5v0" stroke="#fff" strokeWidth="3.4" strokeLinecap="round" />
    </svg>
  )
}

export function Feedback({ runId, target, labels, fb, onSend }: {
  runId: RunId
  target: FeedbackTarget
  labels: [string, string]
  fb: FbMap
  onSend: (runId: RunId, target: FeedbackTarget, ok: boolean) => void
}) {
  const cur = fb[fbKey(runId, target)]
  return (
    <div className="feedback" role="group" aria-label="반응 남기기">
      <button type="button" className={'btn fb yes' + (cur === true ? ' sel' : '')} aria-pressed={cur === true}
        onClick={() => onSend(runId, target, true)}>
        <span className="ic" aria-hidden="true">✓</span>{labels[0]}
      </button>
      <button type="button" className={'btn fb no' + (cur === false ? ' sel' : '')} aria-pressed={cur === false}
        onClick={() => onSend(runId, target, false)}>
        <span className="ic" aria-hidden="true">✗</span>{labels[1]}
      </button>
    </div>
  )
}

export function Segs({ segs, kind }: { segs: Seg[]; kind: 'a' | 'b' }) {
  return (
    <>
      {segs.map((s, i) => s.changed
        ? <span key={i} className={kind === 'a' ? 'diff-a' : 'diff-b'}>{s.text}</span>
        : <span key={i}>{s.text}</span>)}
    </>
  )
}

export function Audio({ label, src }: { label: string; src: string }) {
  return (
    <div className="audio">
      <span>{label}</span>
      <audio controls preload="none" src={src} />
    </div>
  )
}

export function SideHead({ kind, meta }: { kind: 'a' | 'b'; meta?: string }) {
  return (
    <div className="side-head">
      <span className="tag" aria-hidden="true">{kind.toUpperCase()}</span>
      <h2><span className="sr-only">{kind.toUpperCase()}. </span>{kind === 'a' ? '일반 STT' : '개선 방식'}</h2>
      <span className="side-sub">{kind === 'a' ? '그대로 받아 적기' : '전처리 + 방언 사전 + 문맥 보정'}</span>
      {meta && <span className="side-meta">{meta}</span>}
    </div>
  )
}

export const STAGES = ['변환', '전처리', '인식', '보정'] as const

export const Progress = forwardRef<HTMLParagraphElement, {
  stage: number            // index currently running; STAGES.length = 마지막 단계(답변/검색)
  busy: boolean
  times: (number | null)[] // STAGES + 마지막 단계
  last: string
}>(function Progress({ stage, busy, times, last }, ref) {
  const names = [...STAGES, last]
  if (busy) {
    return (
      <p className="progress" role="status" ref={ref}>
        <span className="spinner" aria-hidden="true" />
        <span>분석 중…{' '}
          {names.map((n, i) => (
            <span key={n} className={i === stage ? 'cur' : i < stage ? 'past' : ''}>
              {i > 0 && <span className="arrow" aria-hidden="true"> → </span>}{n}
            </span>
          ))}
        </span>
      </p>
    )
  }
  return (
    <p className="progress done" role="status" ref={ref}>
      완료{names.map((n, i) => times[i] != null && <span key={n}> · {n} {Math.round(times[i] as number)}ms</span>)}
    </p>
  )
})
