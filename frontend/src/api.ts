import { requestToken } from './components/ui/ask'

const KEY = 'pv_token'
export const withToken = (url: string) => {  // EventSource and download links cannot set headers
  const t = localStorage.getItem(KEY)
  return t ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(t)}` : url
}

export async function api<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  const t = localStorage.getItem(KEY)
  const r = await fetch(`/api${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}) } })
  if (r.status === 401 && !retried) {  // the server was started with PV_API_TOKEN
    const nt = await requestToken()
    if (nt) { localStorage.setItem(KEY, nt); return api<T>(path, init, true) }
  }
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}
