export interface Seg { text: string; changed: boolean }

const norm = (s: string) => Array.from(s)

/** Char-level LCS diff. Returns segments for `a` (removed chars flagged) and `b` (inserted/changed chars flagged). */
export function charDiff(a: string, b: string): { a: Seg[]; b: Seg[] } {
  const x = norm(a)
  const y = norm(b)
  const n = x.length
  const m = y.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const ma: boolean[] = new Array(n).fill(true)
  const mb: boolean[] = new Array(m).fill(true)
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (x[i] === y[j]) { ma[i] = false; mb[j] = false; i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) i++
    else j++
  }
  return { a: merge(x, ma), b: merge(y, mb) }
}

function merge(chars: string[], flags: boolean[]): Seg[] {
  const out: Seg[] = []
  chars.forEach((c, k) => {
    const last = out[out.length - 1]
    if (last && last.changed === flags[k]) last.text += c
    else out.push({ text: c, changed: flags[k] })
  })
  return out
}
