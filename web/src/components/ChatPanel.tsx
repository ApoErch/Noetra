import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import {
  streamMessage,
  type ChatMessage,
  type Citation,
  type Conversation,
  type ConversationDetail,
  type ToolStep,
} from '../lib/chat'
import { MessageBubble } from './MessageBubble'

type Props = {
  repoId: string
  onOpenCitation: (citation: Citation) => void
  // Which conversation is open is owned by RepoExplorer, because the picker that changes it
  // lives in the workspace header rather than inside this panel.
  activeId: string | null
  onActiveIdChange: (id: string | null) => void
}

/** The assistant message currently being streamed, before it has an id or is persisted. */
type Draft = { content: string; steps: ToolStep[]; citations: Citation[] }

/** Chat with the repo: the message list and the composer. */
export function ChatPanel({ repoId, onOpenCitation, activeId, onActiveIdChange }: Props) {
  const queryClient = useQueryClient()
  const [input, setInput] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const { data: detail } = useQuery<ConversationDetail>({
    queryKey: ['conversation', repoId, activeId],
    queryFn: () => apiFetch<ConversationDetail>(`/api/v1/repos/${repoId}/conversations/${activeId}`),
    enabled: activeId !== null,
  })

  const createConversation = useMutation({
    mutationFn: () => apiFetch<Conversation>(`/api/v1/repos/${repoId}/conversations`, { method: 'POST' }),
    onSuccess: (conv) => {
      queryClient.invalidateQueries({ queryKey: ['conversations', repoId] })
      onActiveIdChange(conv.id)
    },
  })

  const messages: ChatMessage[] = detail?.messages ?? []
  const busy = draft !== null

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [messages.length, draft?.content, draft?.steps.length])

  /** Send the composer text: create a conversation if needed, then stream the answer into `draft`. */
  const send = async (event: FormEvent) => {
    event.preventDefault()
    const content = input.trim()
    if (!content || busy) return
    setError(null)
    setInput('')

    let conversationId = activeId
    if (conversationId === null) {
      const conv = await createConversation.mutateAsync()
      conversationId = conv.id
    }

    setPendingUser(content)
    setDraft({ content: '', steps: [], citations: [] })
    try {
      await streamMessage(repoId, conversationId, content, (ev) => {
        setDraft((d) => {
          if (!d) return d
          switch (ev.type) {
            case 'tool_call':
              return { ...d, steps: [...d.steps, { name: ev.name, args: ev.args, summary: '…' }] }
            case 'tool_result': {
              const steps = d.steps.slice()
              for (let i = steps.length - 1; i >= 0; i--) {
                if (steps[i].name === ev.name && steps[i].summary === '…') {
                  steps[i] = { ...steps[i], summary: ev.summary }
                  break
                }
              }
              return { ...d, steps }
            }
            case 'token':
              return { ...d, content: d.content + ev.text }
            case 'citations':
              return { ...d, citations: ev.items }
            case 'error':
              setError(ev.detail)
              return d
            default:
              return d
          }
        })
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      // The persisted messages replace the local draft once the refetch lands.
      await queryClient.invalidateQueries({ queryKey: ['conversation', repoId, conversationId] })
      queryClient.invalidateQueries({ queryKey: ['conversations', repoId] })
      setDraft(null)
      setPendingUser(null)
    }
  }

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 space-y-7 overflow-y-auto px-8 py-6">
          {messages.length === 0 && !pendingUser && (
            <div className="mt-16 text-center text-sm text-zinc-500">
              <p className="text-zinc-300">Ask anything about this repository.</p>
              <p className="mt-1">“How does authentication work?” · “Where is Redis used?” · “What calls decrypt_token?”</p>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble
              key={m.id}
              role={m.role}
              content={m.content}
              citations={m.citations}
              toolTrace={m.tool_trace}
              onOpenCitation={onOpenCitation}
            />
          ))}
          {pendingUser && (
            <MessageBubble role="user" content={pendingUser} citations={[]} toolTrace={[]} onOpenCitation={onOpenCitation} />
          )}
          {draft && (
            <MessageBubble
              role="assistant"
              content={draft.content}
              citations={draft.citations}
              toolTrace={draft.steps}
              streaming
              onOpenCitation={onOpenCitation}
            />
          )}
          {error && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-400">{error}</p>}
          <div ref={bottomRef} />
        </div>

        <form onSubmit={send} className="border-t border-zinc-800 px-8 py-5">
          <div className="flex gap-3">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about this codebase…"
              disabled={busy}
              className="flex-1 rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-3 text-[15px] text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={busy || input.trim().length === 0}
              className="flex items-center gap-2 rounded-xl bg-indigo-600 px-5 py-3 text-sm font-medium text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-30"
            >
              <SendIcon />
              {busy ? 'Answering…' : 'Send'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function SendIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 fill-current">
      <path d="M15.44.29a.75.75 0 0 1 .225.809l-4.5 14.25a.75.75 0 0 1-1.37.113L7.06 9.94.328 8.198a.75.75 0 0 1 .113-1.371l14.25-4.5a.75.75 0 0 1 .75.963ZM8.31 8.94l2.32 4.176L13.977 2.98 8.31 8.94Zm4.71-6.917L2.98 6.023l4.176 2.32 5.864-5.864Z" />
    </svg>
  )
}
