import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { FileViewer, type FileContent } from './FileViewer'
import type { ViewerTarget } from '../lib/chat'

type Props = {
  repoId: string
  target: ViewerTarget
  onClose: () => void
}

/** Modal file viewer layered over the chat, so following a citation never loses the conversation behind it. */
export function FileOverlay({ repoId, target, onClose }: Props) {
  const { data: fileContent, isLoading } = useQuery<FileContent>({
    queryKey: ['file', repoId, target.fileId],
    queryFn: () => apiFetch<FileContent>(`/api/v1/repos/${repoId}/files/${target.fileId}`),
    enabled: !target.isBinary,
  })

  // Escape closes, matching what every other modal on the web does. Bound on the document
  // rather than a focused element so it works no matter where Monaco has put the caret.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const file: FileContent | null = target.isBinary
    ? { id: target.fileId, path: target.path, content: null, is_binary: true }
    : (fileContent ?? null)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={target.path}
    >
      {/* Stop clicks inside the panel from reaching the backdrop's close handler. */}
      <div
        onClick={(event) => event.stopPropagation()}
        className="flex h-[88vh] w-full max-w-6xl flex-col overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950 shadow-2xl"
      >
        <div className="flex items-center gap-3 border-b border-zinc-800 bg-zinc-900 px-4 py-2.5">
          <span className="truncate font-mono text-xs text-zinc-300">{target.path}</span>
          {target.highlight && (
            <span className="shrink-0 rounded bg-indigo-500/15 px-1.5 py-0.5 font-mono text-[11px] text-indigo-300 ring-1 ring-indigo-500/30">
              {target.highlight.startLine === target.highlight.endLine
                ? `line ${target.highlight.startLine}`
                : `lines ${target.highlight.startLine}-${target.highlight.endLine}`}
            </span>
          )}
          <button
            onClick={onClose}
            aria-label="Close file"
            className="ml-auto shrink-0 rounded-md px-2 py-1 text-xs text-zinc-400 transition hover:bg-zinc-800 hover:text-white"
          >
            Close <span className="text-zinc-600">Esc</span>
          </button>
        </div>
        <div className="flex-1 overflow-hidden">
          {isLoading && !target.isBinary ? (
            <div className="flex h-full items-center justify-center text-sm text-zinc-500">Loading…</div>
          ) : (
            <FileViewer file={file} highlight={target.highlight} />
          )}
        </div>
      </div>
    </div>
  )
}
