import { beforeEach, expect, test } from 'vitest'
import { withToken } from '../api'
import { columnsOf, lineDiff, templateVars } from './setup'

test('placeholders are detected once each, in order, with or without spaces', () => {
  expect(templateVars('a {{user_message}} b {{ plan_tier }} c {{user_message}}', 'x {{extra}}')).toEqual(['user_message', 'plan_tier', 'extra'])
  expect(templateVars('no placeholders {single} braces')).toEqual([])
})

test('column detection for CSV (quoted commas) and JSONL', () => {
  expect(columnsOf('input,expected\n"a, b",c', 'csv')).toEqual(['input', 'expected'])
  expect(columnsOf('"my input","my, expected"\nx,y', 'csv')).toEqual(['my input', 'my, expected'])
  expect(columnsOf('{"input": "hi", "output": "yo", "expected": "pass"}\n{"input": "x"}', 'jsonl')).toEqual(['input', 'output', 'expected'])
  expect(columnsOf('not json', 'jsonl')).toEqual([])
})

test('line diff marks additions, removals and unchanged lines', () => {
  const d = lineDiff(['a', 'b', 'c'], ['a', 'x', 'c', 'd'])
  expect(d.map((x) => x.t + x.s)).toEqual([' a', '+x', '-b', ' c', '+d'])
  expect(d.filter((x) => x.t === '+').map((x) => x.s).sort()).toEqual(['d', 'x'])
  expect(d.filter((x) => x.t === '-').map((x) => x.s)).toEqual(['b'])
  expect(lineDiff(['same'], ['same']).every((x) => x.t === ' ')).toBe(true)
})

beforeEach(() => {
  const store: Record<string, string> = {}
  ;(globalThis as any).localStorage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => (store[k] = v), removeItem: (k: string) => delete store[k] }
})

test('download and event-stream links carry the API token only when one is set', () => {
  expect(withToken('/api/x')).toBe('/api/x')
  localStorage.setItem('pv_token', 'a b&c')
  expect(withToken('/api/x')).toBe('/api/x?token=a%20b%26c')
  expect(withToken('/api/x?format=md')).toBe('/api/x?format=md&token=a%20b%26c')
})
