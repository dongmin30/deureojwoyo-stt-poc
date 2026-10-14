import { useState } from 'react'
import { api } from './api'

/** 외부 공유 시 접속 코드를 받는 화면. QR/링크에 ?code= 가 있으면 이 화면 없이 바로 들어간다. */
export default function AccessGate({ onPass }: { onPass: () => void }) {
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!code.trim()) return
    setBusy(true)
    setError('')
    try {
      await api.login(code.trim())
      onPass()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="page">
      <form className="card gate" onSubmit={submit}>
        <h1>접속 코드를 입력해 주세요</h1>
        <p className="hint">발표·체험용으로 공개된 화면입니다. 안내받은 코드를 넣어 주세요.</p>
        <input className="gate-input" inputMode="numeric" autoComplete="one-time-code" autoFocus
          value={code} onChange={e => setCode(e.target.value)} aria-label="접속 코드" placeholder="예: 1234" />
        {error && <p className="gate-error" role="alert">{error}</p>}
        <button type="submit" className="big" disabled={busy || !code.trim()}>{busy ? '확인 중…' : '들어가기'}</button>
      </form>
    </main>
  )
}
