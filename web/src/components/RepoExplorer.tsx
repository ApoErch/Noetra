import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { buildFileTree, type FileEntry, type TreeNode } from '../lib/tree'
import { FileTree } from './FileTree'
import { FileOverlay } from './FileOverlay'
import { ChatPanel } from './ChatPanel'
import type { Citation, ViewerTarget } from '../lib/chat'
import type { Repo } from './RepoList'

type SelectedFile = Extract<TreeNode, { type: 'file' }>

/** Repo workspace: chat in the main area, file tree in the sidebar, files opened as an overlay. */
export function RepoExplorer({ repo, onBack }: { repo: Repo; onBack: () => void }) {
  // A single target drives the overlay, whether the file was opened from the tree (no
  // highlight) or by following a citation (highlighted range). One piece of state means the
  // two paths can never disagree about what's on screen.
  const [target, setTarget] = useState<ViewerTarget | null>(null)

  const { data: files, isLoading } = useQuery<FileEntry[]>({
    queryKey: ['files', repo.id],
    queryFn: () => apiFetch<FileEntry[]>(`/api/v1/repos/${repo.id}/files`),
  })

  const tree = files ? buildFileTree(files) : []

  const openFromTree = (node: SelectedFile) =>
    setTarget({ fileId: node.id, path: node.path, isBinary: node.is_binary, highlight: null })

  const openFromCitation = (hit: Citation) =>
    setTarget({
      fileId: hit.file_id,
      path: hit.path,
      // A citation doesn't carry is_binary — but only text files are chunked and indexed,
      // so anything retrievable is previewable. Look it up anyway to stay honest if that changes.
      isBinary: files?.find((file) => file.id === hit.file_id)?.is_binary ?? false,
      highlight: { startLine: hit.start_line, endLine: hit.end_line, matchLine: hit.match_line },
    })

  return (
    <div className="flex h-screen w-screen flex-col bg-zinc-950">
      <div className="flex items-center gap-3 border-b border-zinc-800 bg-zinc-950 px-4 py-2.5">
        <button
          onClick={onBack}
          className="flex items-center gap-1 rounded-md px-2 py-1 text-sm text-zinc-400 transition hover:bg-zinc-800 hover:text-white"
        >
          <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 fill-current">
            <path d="M10 3L5 8l5 5V3z" />
          </svg>
          Back
        </button>
        <span className="font-medium text-white">{repo.name}</span>
      </div>

      <div className="flex flex-1 overflow-hidden">
        <div className="w-64 shrink-0 overflow-y-auto border-r border-zinc-800 bg-zinc-950">
          {isLoading ? (
            <p className="px-4 py-3 text-sm text-zinc-500">Loading files…</p>
          ) : (
            <FileTree nodes={tree} selectedFileId={target?.fileId ?? null} onSelectFile={openFromTree} />
          )}
        </div>
        <div className="flex-1 overflow-hidden">
          <ChatPanel repoId={repo.id} onOpenCitation={openFromCitation} />
        </div>
      </div>

      {target && <FileOverlay repoId={repo.id} target={target} onClose={() => setTarget(null)} />}
    </div>
  )
}
