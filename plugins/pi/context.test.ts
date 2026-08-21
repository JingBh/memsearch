import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import {
  extractCompletedTurns,
  findCapturedTurnIds,
  getRecentMemories,
  MEMSEARCH_SYSTEM_MARKER,
  mergeSystemMemoryContext
} from './context.ts'

test('extracts completed user turns and ignores tool results', () => {
  const turns = extractCompletedTurns([
    { type: 'message', id: 'u1', message: { role: 'user', content: 'Fix auth' } },
    { type: 'message', id: 'a1', parentId: 'u1', message: { role: 'assistant', content: [{ type: 'text', text: 'Inspecting.' }] } },
    { type: 'message', id: 't1', parentId: 'a1', message: { role: 'toolResult', content: [{ type: 'text', text: 'secret output' }] } },
    { type: 'message', id: 'a2', parentId: 't1', message: { role: 'assistant', content: [{ type: 'text', text: 'Fixed auth.' }] } },
    { type: 'message', id: 'u2', parentId: 'a2', message: { role: 'user', content: 'Thanks' } }
  ])

  assert.deepEqual(turns, [{
    turnId: 'u1',
    leafId: 'a2',
    userText: 'Fix auth',
    assistantText: 'Inspecting.\n\nFixed auth.',
    transcript: '[User]: Fix auth\n\n[Pi]: Inspecting.\n\nFixed auth.'
  }])
})

test('loads only dated journals and detects captured turn anchors', () => {
  const dir = mkdtempSync(join(tmpdir(), 'memsearch-pi-context-'))
  try {
    writeFileSync(join(dir, '2026-08-20.md'), '## Session 10:00\n\n### 10:01\n<!-- session:s1 turn:u1 leaf:a1 -->\n- Useful memory.\n')
    writeFileSync(join(dir, 'PROJECT.md'), '## Session 09:00\n- Must not be injected.\n')

    const recent = getRecentMemories(dir)
    assert.match(recent, /Useful memory/)
    assert.doesNotMatch(recent, /Must not be injected/)
    assert.deepEqual([...findCapturedTurnIds(dir)], ['u1'])
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('system memory context is replaced idempotently', () => {
  const first = mergeSystemMemoryContext('Base prompt', `${MEMSEARCH_SYSTEM_MARKER} old`)
  const second = mergeSystemMemoryContext(first, `${MEMSEARCH_SYSTEM_MARKER} new`)

  assert.equal(second, `Base prompt\n\n${MEMSEARCH_SYSTEM_MARKER} new`)
})
