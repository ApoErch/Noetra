import { API_URL } from './api'

/** One cited location — mirrors `RetrievalHit` in backend/core/retrieval/types.py. */
export type Citation = {
  file_id: string
  path: string
  start_line: number
  end_line: number
  snippet: string
  score: number
  sources: string[]
  /** Absolute file line that matched inside the range; null for agent citations. */
  match_line: number | null
}

/** The lines a citation points at: the block that answers, plus the line that matched inside it. */
export type Highlight = {
  startLine: number
  endLine: number
  matchLine: number | null
}

/** What the file overlay is currently showing, and where in it to look. */
export type ViewerTarget = {
  fileId: string
  path: string
  isBinary: boolean
  /** Null when the file was opened from the tree rather than from a citation. */
  highlight: Highlight | null
}

/** Render a citation the way the agent writes it: `path:12-40` (or `path:12`). */
export function formatCitation(hit: Pick<Citation, 'path' | 'start_line' | 'end_line'>): string {
  const lines = hit.start_line === hit.end_line ? `${hit.start_line}` : `${hit.start_line}-${hit.end_line}`
  return `${hit.path}:${lines}`
}

/** One tool call the agent made while answering, as shown in the "steps" block. */
export type ToolStep = { name: string; args: Record<string, unknown>; summary: string }

export type Conversation = { id: string; title: string; created_at: string; updated_at: string }

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  tool_trace: ToolStep[]
  created_at: string
}

export type ConversationDetail = Conversation & { messages: ChatMessage[] }

/** Events the SSE endpoint emits — one JSON object per `data:` line. */
export type ChatEvent =
  | { type: 'tool_call'; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; name: string; summary: string }
  | { type: 'token'; text: string }
  | { type: 'citations'; items: Citation[] }
  | { type: 'done'; message_id: string }
  | { type: 'error'; detail: string }

/**
 * POST a question and read the SSE stream, calling `onEvent` per event.
 *
 * Uses `fetch` + a stream reader rather than `EventSource`: the latter can only GET, and the
 * question travels in a POST body. Frames are `data: {json}\n\n`; a chunk from the network can
 * split a frame, so bytes are buffered until a blank line closes one.
 */
export async function streamMessage(
  repoId: string,
  conversationId: string,
  content: string,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const res = await fetch(`${API_URL}/api/v1/repos/${repoId}/conversations/${conversationId}/messages`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail
    } catch {
      /* body was not JSON */
    }
    throw new Error(detail)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      for (const line of frame.split('\n')) {
        if (line.startsWith('data: ')) onEvent(JSON.parse(line.slice(6)) as ChatEvent)
      }
      boundary = buffer.indexOf('\n\n')
    }
  }
}
