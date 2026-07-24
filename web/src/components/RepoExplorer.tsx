import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { buildFileTree, type FileEntry, type TreeNode } from '../lib/tree'
import { FileTree } from './FileTree'
import { FileViewer, type FileContent } from './FileViewer'
import type { Repo } from './RepoList'

type SelectedFile = Extract<TreeNode, { type: 'file' }>

/** Repo file tree browser: fetches the flat path list once, folds it into a tree, lazy-loads content on click. */
export function RepoExplorer({ repo, onBack }: { repo: Repo; onBack: () => void }) {
  const [selected, setSelected] = useState<SelectedFile | null>(null)

  const { data: files, isLoading } = useQuery<FileEntry[]>({
    queryKey: ['files', repo.id],
    queryFn: () => apiFetch<FileEntry[]>(`/api/v1/repos/${repo.id}/files`),
  })

  const { data: fileContent } = useQuery<FileContent>({
    queryKey: ['file', repo.id, selected?.id],
    queryFn: () => apiFetch<FileContent>(`/api/v1/repos/${repo.id}/files/${selected!.id}`),
    enabled: !!selected && !selected.is_binary,
  })

  const tree = files ? buildFileTree(files) : []
  const viewerFile: FileContent | null = !selected
    ? null
    : selected.is_binary
      ? { id: selected.id, path: selected.path, content: null, is_binary: true }
      : (fileContent ?? null)

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
            <FileTree nodes={tree} selectedFileId={selected?.id ?? null} onSelectFile={setSelected} />
          )}
        </div>
        <div className="flex-1">
          <FileViewer file={viewerFile} />
        </div>
      </div>
    </div>
  )
}
