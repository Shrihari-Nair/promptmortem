/** Minimal line diff (LCS) so the old/new change is visible while typing. */
export function lineDiff(a: string[], b: string[]) {
  const dp = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const out: { t: ' ' | '+' | '-'; s: string }[] = []
  let i = 0, j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { out.push({ t: ' ', s: a[i] }); i++; j++ }
    else if (j < b.length && (i === a.length || dp[i][j + 1] >= dp[i + 1][j])) out.push({ t: '+', s: b[j++] })
    else out.push({ t: '-', s: a[i++] })
  }
  return out
}

export const templateVars = (...texts: string[]) => [...new Set(texts.flatMap((t) => [...t.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1])))]

/** Column names of a CSV header line or the keys of the first JSONL object. */
export function columnsOf(text: string, format: 'csv' | 'jsonl'): string[] {
  const first = text.split('\n').find((l) => l.trim()) ?? ''
  if (format === 'jsonl') { try { return Object.keys(JSON.parse(first)) } catch { return [] } }
  return [...first.matchAll(/("([^"]*)"|[^,]+)(,|$)/g)].map((m) => (m[2] ?? m[1]).trim())
}
