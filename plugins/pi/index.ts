import type { ChildProcess } from 'node:child_process'
import { spawn, spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, realpathSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { ExtensionAPI, ExtensionContext } from '@earendil-works/pi-coding-agent'
import { truncateHead } from '@earendil-works/pi-coding-agent'
import { Type } from 'typebox'

import {
  extractCompletedTurns,
  findCapturedTurnIds,
  getRecentMemories,
  MEMSEARCH_SYSTEM_MARKER,
  mergeSystemMemoryContext,
  type SessionEntry
} from './context.ts'

const PLUGIN_DIR = dirname(realpathSync(fileURLToPath(import.meta.url)))
const MAX_OUTPUT_BYTES = 50 * 1024
const MAX_OUTPUT_LINES = 2000

type CommandSpec = {
  command: string
  prefix: string[]
}

type RuntimeState = {
  projectDir: string
  collectionName: string
  memsearchDir: string
  memoryDir: string
  recentContext: string
  skillCandidateHint: string
  capturedTurnIds: Set<string>
  pendingTurnIds: Set<string>
  watcher?: ChildProcess
}

function detectMemsearchCommand(): CommandSpec | undefined {
  const found = spawnSync('which', ['memsearch'], { encoding: 'utf-8' })
  if (found.status === 0 && found.stdout.trim()) {
    return { command: found.stdout.trim(), prefix: [] }
  }

  const localUvx = join(homedir(), '.local', 'bin', 'uvx')
  if (existsSync(localUvx)) {
    return { command: localUvx, prefix: ['--from', 'memsearch[onnx]', 'memsearch'] }
  }

  const uvx = spawnSync('which', ['uvx'], { encoding: 'utf-8' })
  if (uvx.status === 0 && uvx.stdout.trim()) {
    return { command: uvx.stdout.trim(), prefix: ['--from', 'memsearch[onnx]', 'memsearch'] }
  }
  return undefined
}

function expandHome(path: string): string {
  if (path === '~') return homedir()
  return path.startsWith('~/') ? join(homedir(), path.slice(2)) : path
}

function runDetached(command: CommandSpec, args: string[], cwd: string, env: NodeJS.ProcessEnv = process.env): ChildProcess {
  const child = spawn(command.command, [...command.prefix, ...args], {
    cwd,
    detached: true,
    env,
    stdio: 'ignore'
  })
  child.on('error', () => {})
  child.unref()
  return child
}

function getSkillCandidateHint(command: CommandSpec, memsearchDir: string, cwd: string): string {
  const result = spawnSync(command.command, [...command.prefix, 'skills', 'status', '--hint'], {
    cwd,
    encoding: 'utf-8',
    env: { ...process.env, MEMSEARCH_DIR: memsearchDir },
    timeout: 5000
  })
  return result.status === 0 ? result.stdout.trim().split('\n')[0] ?? '' : ''
}

function formatToolOutput(output: string): string {
  const truncated = truncateHead(output, {
    maxBytes: MAX_OUTPUT_BYTES,
    maxLines: MAX_OUTPUT_LINES
  })
  if (!truncated.truncated) return truncated.content
  return `${truncated.content}\n\n[Output truncated to ${MAX_OUTPUT_LINES} lines or ${MAX_OUTPUT_BYTES} bytes.]`
}

export default function (pi: ExtensionAPI) {
  if (process.env.MEMSEARCH_DISABLE === '1') return

  const memsearchCommand = detectMemsearchCommand()
  let state: RuntimeState | undefined

  const runMemsearch = async (args: string[], signal: AbortSignal | undefined, timeout: number) => {
    if (!memsearchCommand) throw new Error('memsearch is unavailable; install it before using MemSearch')
    const result = await pi.exec(memsearchCommand.command, [...memsearchCommand.prefix, ...args], {
      cwd: state?.projectDir,
      signal,
      timeout
    })
    const output = (result.stdout || result.stderr || '').trim()
    if (result.code !== 0) throw new Error(output || `memsearch exited with code ${result.code}`)
    return formatToolOutput(output || 'No results found.')
  }

  const initialize = async (ctx: ExtensionContext) => {
    const gitRoot = await pi.exec('git', ['-C', ctx.cwd, 'rev-parse', '--show-toplevel'], {
      cwd: ctx.cwd,
      timeout: 5000
    })
    const projectDir = gitRoot.code === 0 && gitRoot.stdout.trim()
      ? gitRoot.stdout.trim()
      : resolve(ctx.cwd)
    const explicitMemsearchDir = process.env.MEMSEARCH_DIR
    const scope = explicitMemsearchDir
      ? expandHome(explicitMemsearchDir)
      : projectDir
    const collection = await pi.exec('bash', [join(PLUGIN_DIR, 'scripts', 'derive-collection.sh'), scope], {
      cwd: projectDir,
      timeout: 5000
    })
    if (collection.code !== 0 || !collection.stdout.trim()) {
      throw new Error(collection.stderr.trim() || 'failed to derive MemSearch collection')
    }

    const collectionName = collection.stdout.trim()
    const memsearchDir = explicitMemsearchDir
      ? resolve(expandHome(explicitMemsearchDir))
      : join(homedir(), '.memsearch', 'projects', collectionName)
    const memoryDir = join(memsearchDir, 'memory')
    mkdirSync(memoryDir, { recursive: true })

    state = {
      projectDir,
      collectionName,
      memsearchDir,
      memoryDir,
      recentContext: getRecentMemories(memoryDir),
      skillCandidateHint: memsearchCommand
        ? getSkillCandidateHint(memsearchCommand, memsearchDir, projectDir)
        : '',
      capturedTurnIds: findCapturedTurnIds(memoryDir),
      pendingTurnIds: new Set<string>()
    }

    if (!memsearchCommand) {
      if (ctx.hasUI) ctx.ui.notify('MemSearch is unavailable; memory features are disabled.', 'warning')
      return
    }

    const uri = await pi.exec(memsearchCommand.command, [...memsearchCommand.prefix, 'config', 'get', 'milvus.uri'], {
      cwd: projectDir,
      timeout: 5000
    })
    const backendUri = uri.code === 0 ? uri.stdout.trim() : ''
    if (backendUri.startsWith('http') || backendUri.startsWith('tcp')) {
      state.watcher = runDetached(
        memsearchCommand,
        ['watch', memoryDir, '--collection', collectionName],
        projectDir,
        { ...process.env, MEMSEARCH_DIR: memsearchDir }
      )
    } else {
      runDetached(
        memsearchCommand,
        ['index', memoryDir, '--collection', collectionName],
        projectDir,
        { ...process.env, MEMSEARCH_DIR: memsearchDir }
      )
    }
  }

  const spawnCaptureWorker = (turn: ReturnType<typeof extractCompletedTurns>[number], ctx: ExtensionContext) => {
    if (!state || !memsearchCommand) return

    const sessionId = ctx.sessionManager.getSessionId()
    const payloadDir = join(state.memsearchDir, '.pi-capture')
    mkdirSync(payloadDir, { recursive: true })
    const safeTurnId = turn.turnId.replace(/[^a-zA-Z0-9_-]/g, '_')
    const payloadPath = join(payloadDir, `${sessionId}-${safeTurnId}-${Date.now()}.json`)
    writeFileSync(payloadPath, JSON.stringify({
      projectDir: state.projectDir,
      memsearchDir: state.memsearchDir,
      memoryDir: state.memoryDir,
      collectionName: state.collectionName,
      memsearchCommand: [memsearchCommand.command, ...memsearchCommand.prefix],
      pluginDir: PLUGIN_DIR,
      sessionId,
      turnId: turn.turnId,
      leafId: turn.leafId,
      transcriptPath: ctx.sessionManager.getSessionFile() ?? '',
      transcript: turn.transcript,
      userText: turn.userText,
      assistantText: turn.assistantText,
      capturedAt: Date.now()
    }), 'utf-8')

    const child = spawn('python3', [join(PLUGIN_DIR, 'scripts', 'capture-turn.py'), payloadPath], {
      cwd: state.projectDir,
      detached: true,
      env: {
        ...process.env,
        MEMSEARCH_DIR: state.memsearchDir,
        MEMSEARCH_DISABLE: '1',
        MEMSEARCH_NO_WATCH: '1'
      },
      stdio: 'ignore'
    })
    child.on('error', () => {})
    child.unref()
    state.pendingTurnIds.add(turn.turnId)
  }

  pi.on('resources_discover', () => ({
    skillPaths: [join(PLUGIN_DIR, 'skills')]
  }))

  pi.on('session_start', async (_event, ctx) => {
    try {
      await initialize(ctx)
    } catch (error) {
      if (ctx.hasUI) ctx.ui.notify(`MemSearch initialization failed: ${String(error)}`, 'warning')
    }
  })

  pi.on('before_agent_start', event => {
    if (!state) return
    const parts = [
      `${MEMSEARCH_SYSTEM_MARKER} Use the memory-recall skill or memory_search, memory_get, and memory_transcript tools when historical context could help.`
    ]
    if (state.skillCandidateHint) parts.push(state.skillCandidateHint)
    if (state.recentContext) parts.push(state.recentContext)
    return { systemPrompt: mergeSystemMemoryContext(event.systemPrompt, parts.join('\n\n')) }
  })

  pi.on('agent_settled', (_event, ctx) => {
    if (!state || !memsearchCommand) return
    const entries = ctx.sessionManager.getBranch() as unknown as SessionEntry[]
    for (const turn of extractCompletedTurns(entries)) {
      if (state.capturedTurnIds.has(turn.turnId) || state.pendingTurnIds.has(turn.turnId)) continue
      spawnCaptureWorker(turn, ctx)
    }
  })

  pi.on('session_shutdown', () => {
    if (state?.watcher && !state.watcher.killed) state.watcher.kill('SIGTERM')
    state = undefined
  })

  pi.registerTool({
    name: 'memory_search',
    label: 'Memory Search',
    description: 'Search past project conversations with MemSearch hybrid semantic search. Returns ranked chunks and chunk hashes for memory_get.',
    promptSnippet: 'Search persistent project memories from prior sessions',
    parameters: Type.Object({
      query: Type.String({ description: 'What historical context to find' }),
      topK: Type.Optional(Type.Integer({ minimum: 1, maximum: 20, description: 'Maximum results; defaults to 5' }))
    }),
    async execute(_toolCallId, params, signal) {
      if (!state) throw new Error('MemSearch has not initialized for this session')
      const text = await runMemsearch([
        'search',
        params.query,
        '--top-k',
        String(params.topK ?? 5),
        '--json-output',
        '--collection',
        state.collectionName
      ], signal, 30000)
      return { content: [{ type: 'text', text }], details: {} }
    }
  })

  pi.registerTool({
    name: 'memory_get',
    label: 'Memory Get',
    description: 'Expand a MemSearch chunk hash into its full markdown section and transcript anchor.',
    parameters: Type.Object({
      chunkHash: Type.String({ description: 'Chunk hash returned by memory_search' })
    }),
    async execute(_toolCallId, params, signal) {
      if (!state) throw new Error('MemSearch has not initialized for this session')
      const text = await runMemsearch([
        'expand',
        params.chunkHash,
        '--collection',
        state.collectionName
      ], signal, 15000)
      return { content: [{ type: 'text', text }], details: {} }
    }
  })

  pi.registerTool({
    name: 'memory_transcript',
    label: 'Memory Transcript',
    description: 'Read the original Pi JSONL conversation referenced by a memory anchor. Use turnId and leafId from the anchor to select the exact branch.',
    parameters: Type.Object({
      transcriptPath: Type.String({ description: 'Absolute transcript path from the memory anchor' }),
      turnId: Type.Optional(Type.String({ description: 'User entry ID from the anchor' })),
      leafId: Type.Optional(Type.String({ description: 'Captured branch leaf ID from the anchor' })),
      context: Type.Optional(Type.Integer({ minimum: 0, maximum: 20, description: 'Neighboring turns before and after; defaults to 3' })),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 50, description: 'Recent turns when turnId is omitted; defaults to 20' }))
    }),
    async execute(_toolCallId, params, signal) {
      const args = [join(PLUGIN_DIR, 'scripts', 'parse-transcript.py'), params.transcriptPath]
      if (params.turnId) args.push('--turn', params.turnId)
      if (params.leafId) args.push('--leaf', params.leafId)
      if (params.context !== undefined) args.push('--context', String(params.context))
      if (params.limit !== undefined) args.push('--limit', String(params.limit))
      const result = await pi.exec('python3', args, { signal, timeout: 15000 })
      const output = (result.stdout || result.stderr || '').trim()
      if (result.code !== 0) throw new Error(output || `transcript parser exited with code ${result.code}`)
      return { content: [{ type: 'text', text: formatToolOutput(output) }], details: {} }
    }
  })
}
