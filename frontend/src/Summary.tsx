import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { Summary as S, PathSummary } from './api'
import { Icon, pct } from './parts'

// bar length for a rate; CER can exceed 100%, so clamp
const bar = (v: number | null) => `${v === null ? 0 : Math.min(Math.max(v, 0), 1) * 100}%`

export default function Summary() {
  const [data, setData] = useState<S | null>(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => {
    setErr('')
    api.summary().then(setData).catch(e => setErr(e instanceof Error ? e.message : String(e)))
  }, [])
  useEffect(load, [load])

  const rows: { label: string; v: (p: PathSummary) => number | null; n: (p: PathSummary) => number; low?: boolean }[] = [
    { label: '인식 "맞아요" 비율', v: p => p.text_ok_rate, n: p => p.text_fb_n },
    { label: '결과 "원하던 결과" 비율', v: p => p.result_ok_rate, n: p => p.result_fb_n },
    { label: '평균 CER', v: p => p.avg_cer, n: p => p.cer_n, low: true },
    { label: '검색 hit@3 비율', v: p => p.hit3_rate, n: p => p.hit3_n },
  ]

  return (
    <div className="summary">
      <div className="summary-head">
        <h1>결과 요약</h1>
        <div className="row">
          <button className="btn" onClick={load}><Icon name="refresh" size={20} />새로고침</button>
          <a className="btn primary" href="/api/export.csv" download><Icon name="download" size={20} />CSV 내보내기</a>
        </div>
      </div>

      {err && (
        <div className="error" role="alert"><Icon name="alert" /><span>{err}</span></div>
      )}

      {data && (
        <>
          <div className="kpis">
            <div className="card kpi">
              <span className="k-label">전체 실행 수</span>
              <span className="k-value">{data.total_runs}</span>
              <span className="k-sub">상담 {data.by_sim.counsel} · 검색 {data.by_sim.search}</span>
            </div>
            <div className="card kpi">
              <span className="k-label">인식 "맞아요" 비율</span>
              <span className="k-value"><span className="k-b">B {pct(data.b.text_ok_rate)}</span></span>
              <span className="k-sub">A {pct(data.a.text_ok_rate)}</span>
            </div>
            <div className="card kpi">
              <span className="k-label">평균 CER <small>(낮을수록 정확)</small></span>
              <span className="k-value"><span className="k-b">B {pct(data.b.avg_cer)}</span></span>
              <span className="k-sub">A {pct(data.a.avg_cer)}</span>
            </div>
          </div>

          <div className="card tablewrap">
            <table>
              <thead>
                <tr><th scope="col">항목</th><th scope="col">A. 일반 STT</th><th scope="col" className="bcol">B. 개선 방식</th></tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.label}>
                    <th scope="row">{r.label}{r.low && <small> (낮을수록 정확)</small>}</th>
                    {(['a', 'b'] as const).map(k => (
                      <td key={k} className={k === 'b' ? 'bcol' : undefined}>
                        <div className="cell">
                          <strong>{pct(r.v(data[k]))}</strong>
                          <small>n={r.n(data[k])}</small>
                        </div>
                        <span className={'mini ' + k} aria-hidden="true"><span style={{ width: bar(r.v(data[k])) }} /></span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
