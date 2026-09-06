import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import type { Conversation } from '../lib/chat'

type Props = {
  repoId: string
  activeId: string | null
  onSelect: (id: string | null) => void
}

/**
 * The conversation picker, as a header dropdown.
 *
 * This list used to be a permanent sidebar between the file tree and the messages, which
 * spent a whole column on something read once per conversation. Collapsed to a menu: the
 * trigger still shows which chat is open, so nothing is hidden that the user needs at a
 * glance, and switching costs one extra click.
 */
export function ConversationMenu({ repoId, activeId, onSelect }: Props) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const { data: conversations } = useQuery<Conversation[]>({
    queryKey: ['conversations', repoId],
    queryFn: () => apiFetch<Conversation[]>(`/api/v1/repos/${repoId}/conversations`),
  })

  const deleteConversation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/api/v1/repos/${repoId}/conversations/${id}`, { method: 'DELETE' }),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['conversations', repoId] })
      if (activeId === id) onSelect(null)
    },
  })

  // A menu that only closes via its own items is a trap once it overlaps the content below
  // it, so dismiss on any outside click and on Escape.
  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const items = conversations ?? []
  const active = items.find((conv) => conv.id === activeId)
  const label = active ? active.title : 'Chats'

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={label}
        className="flex max-w-56 items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-500"
      >
        <span className="truncate">{label}</span>
        <svg viewBox="0 0 16 16" className="h-3 w-3 shrink-0 fill-current text-white/70">
          <path d="M4 6l4 4 4-4z" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-1 max-h-80 w-72 overflow-y-auto rounded-md border border-zinc-800 bg-zinc-900 py-1 shadow-xl"
        >
          {items.length === 0 && (
            <p className="px-3 py-2 text-sm text-zinc-500">No conversations yet.</p>
          )}
          {items.map((conv) => (
            <div
              key={conv.id}
              className={`group flex items-center gap-1 px-1 ${conv.id === activeId ? 'bg-zinc-800' : 'hover:bg-zinc-800/60'}`}
            >
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  onSelect(conv.id)
                  setOpen(false)
                }}
                className="min-w-0 flex-1 truncate px-2 py-1.5 text-left text-sm text-zinc-300"
                title={conv.title}
              >
                {conv.title}
              </button>
              <button
                type="button"
                onClick={() => deleteConversation.mutate(conv.id)}
                disabled={deleteConversation.isPending}
                className="hidden shrink-0 px-1.5 text-zinc-600 hover:text-red-400 group-hover:block"
                aria-label={`Delete conversation ${conv.title}`}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
