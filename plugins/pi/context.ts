import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

export const MEMSEARCH_SYSTEM_MARKER = '[memsearch] Memory available.'

export type SessionEntry = {
  type: string
  id: string
  parentId?: string | null
  message?: {
    role?: string
    content?: unknown
  }
}

export type CapturedTurn = {
  turnId: string
  leafId: string
  userText: string
  assistantText: string
  transcript: string
}

type ContentBlock = {
  type?: string
  text?: string
}

export function extractText(content: unknown): string {
  if (typeof content === 'string') return content.trim()
  if (!Array.isArray(content)) return ''

  return content
    .filter((part): part is ContentBlock => !!part && typeof part === 'object')
    .filter(part => part.type === 'text' && typeof part.text === 'string')
    .map(part => part.text?.trim() ?? '')
    .filter(Boolean)
    .join('\n')
}

export function extractCompletedTurns(entries: readonly SessionEntry[]): CapturedTurn[] {
  const turns: CapturedTurn[] = []
  let current: {
    turnId: string
    userText: string
    assistantParts: string[]
    leafId: string
  } | undefined

  const flush = () => {
    if (!current || current.assistantParts.length === 0) return
    const assistantText = current.assistantParts.join('\n\n').trim()
    if (!assistantText) return
    turns.push({
      turnId: current.turnId,
      leafId: current.leafId,
      userText: current.userText,
      assistantText,
      transcript: `[User]: ${current.userText}\n\n[Pi]: ${assistantText}`
    })
  }

  for (const entry of entries) {
    if (entry.type !== 'message' || !entry.message?.role) continue

    if (entry.message.role === 'user') {
      flush()
      const userText = extractText(entry.message.content)
      current = userText
        ? { turnId: entry.id, userText, assistantParts: [], leafId: entry.id }
        : undefined
      continue
    }

    if (entry.message.role !== 'assistant' || !current) continue
    const text = extractText(entry.message.content)
    if (!text) continue
    current.assistantParts.push(text)
    current.leafId = entry.id
  }

  flush()
  return turns
}

export function isDailyJournalFile(file: string): boolean {
  return /^\d{4}-\d{2}-\d{2}\.md$/.test(file)
}

function recentMemoryPreviewLines(content: string, maxLines: number): string[] {
  const sections: string[][] = []
  let current: string[] = []
  let hasBody = false

  const flush = () => {
    if (current.length > 0 && hasBody) sections.push(current)
    current = []
    hasBody = false
  }

  for (const rawLine of content.split('\n')) {
    const line = rawLine.trimEnd()
    if (/^##\s/.test(line)) {
      flush()
      current = [line]
      continue
    }
    if (/^#{3,4}\s/.test(line)) {
      current.push(line)
      continue
    }
    if (line.startsWith('- ') || line.startsWith('[User]') || line.startsWith('[Pi]')) {
      current.push(line)
      hasBody = true
    }
  }

  flush()
  return sections.flat().slice(-maxLines)
}

export function getRecentMemories(memoryDir: string, count = 2, maxLinesPerFile = 30): string {
  if (!existsSync(memoryDir)) return ''

  const files = readdirSync(memoryDir)
    .filter(isDailyJournalFile)
    .sort()
    .slice(-count)

  if (files.length === 0) return ''

  const summary: string[] = []
  for (const file of files) {
    try {
      const lines = recentMemoryPreviewLines(readFileSync(join(memoryDir, file), 'utf-8'), maxLinesPerFile)
      if (lines.length > 0) summary.push(`[${file}]`, ...lines)
    } catch {
      // Best-effort cold-start context.
    }
  }

  if (summary.length === 0) {
    return `You have ${files.length} past memory file(s). Use memory_search when historical context could help.`
  }
  return `Recent memories (use memory_search for full search):\n${summary.join('\n')}`
}

export function findCapturedTurnIds(memoryDir: string): Set<string> {
  const ids = new Set<string>()
  if (!existsSync(memoryDir)) return ids

  for (const file of readdirSync(memoryDir).filter(name => name.endsWith('.md'))) {
    try {
      const content = readFileSync(join(memoryDir, file), 'utf-8')
      for (const match of content.matchAll(/<!--\s+session:\S+\s+turn:(\S+)/g)) ids.add(match[1])
    } catch {
      // A single unreadable journal must not disable capture.
    }
  }
  return ids
}

export function mergeSystemMemoryContext(systemPrompt: string, memoryText: string): string {
  const markerIndex = systemPrompt.indexOf(MEMSEARCH_SYSTEM_MARKER)
  const base = markerIndex === -1
    ? systemPrompt.trimEnd()
    : systemPrompt.slice(0, markerIndex).trimEnd()
  return base ? `${base}\n\n${memoryText}` : memoryText
}
