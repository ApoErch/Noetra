import { useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { buildFileTree, type FileEntry, type TreeNode } from '../lib/tree'
import { FileTree } from './FileTree'
import { FileOverlay } from './FileOverlay'
import { ChatPanel } from './ChatPanel'
import { ConversationMenu } from './ConversationMenu'
import { RepoDashboard } from './RepoDashboard'
import type { Citation, ViewerTarget } from '../lib/chat'
import { displayName, type Repo } from '../lib/repos'

type SelectedFile = Extract<TreeNode, { type: 'file' }>
type Tab = 'chat' | 'dashboard'

type Props = {
  repo: Repo
  onBack: () => void
}

/** Repo workspace: chat or dashboard in the main area, file tree in the sidebar, files opened as an overlay. */
export function RepoExplorer({ repo, onBack }: Props) {
  // A single target drives the overlay, whether the file was opened from the tree (no
  // highlight) or by following a citation (highlighted range). One piece of state means the
  // two paths can never disagree about what's on screen.
  const [target, setTarget] = useState<ViewerTarget | null>(null)
  const [tab, setTab] = useState<Tab>('chat')
  // Owned here, not in ChatPanel, because the picker and the New chat button live in the
  // workspace header while the messages they control live in the panel below it.
  const [conversationId, setConversationId] = useState<string | null>(null)

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

  // The dashboard knows a path but not a file id, so it hands the path back here — the same
  // file list the tree is built from resolves it, rather than adding a lookup endpoint.
  const openFromPath = (path: string) => {
    const file = files?.find((entry) => entry.path === path)
    if (file) setTarget({ fileId: file.id, path: file.path, isBinary: file.is_binary, highlight: null })
  }

  return (
    <div className="flex h-screen w-screen flex-col bg-zinc-950">
      {/* One toolbar spanning the full width, above everything. The workspace is a fixed
          h-screen column with the panels below scrolling inside it, so the header is pinned
          without needing `sticky` — nothing scrolls past it. */}
      <header className="flex shrink-0 items-center gap-4 border-b border-zinc-800 bg-zinc-950 px-6 py-4">
        <button
          onClick={onBack}
          title="Back to your repositories"
          className="flex items-center gap-2 rounded-lg px-1.5 py-1 transition hover:bg-zinc-900"
        >
          <span className="text-2xl font-semibold text-violet-400">Noetra</span>
        </button>
        <span className="text-zinc-700">/</span>
        <span className="text-xl font-bold text-white">{displayName(repo.name)}</span>

        <div className="ml-6 flex items-center gap-1.5">
          <TabButton active={tab === 'dashboard'} onClick={() => setTab('dashboard')}>
            Dashboard
          </TabButton>
          <TabButton active={tab === 'chat'} onClick={() => setTab('chat')}>
            Chat
          </TabButton>
        </div>

        {/* Chat actions only while the Chat tab is showing — on the Dashboard they would be
            buttons that change nothing the user can see. */}
        {tab === 'chat' && (
          <div className="ml-auto flex items-center gap-3">
            <ConversationMenu repoId={repo.id} activeId={conversationId} onSelect={setConversationId} />
            <button
              type="button"
              onClick={() => setConversationId(null)}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-500"
            >
              <PlusIcon />
              New chat
            </button>
          </div>
        )}
      </header>

      <div className="flex flex-1 overflow-hidden">
        <div className="w-64 shrink-0 overflow-y-auto border-r border-zinc-800 bg-zinc-950">
          {isLoading ? (
            <p className="px-4 py-4 text-sm text-zinc-500">Loading files…</p>
          ) : (
            <FileTree nodes={tree} selectedFileId={target?.fileId ?? null} onSelectFile={openFromTree} />
          )}
        </div>
        <div className="flex-1 overflow-hidden">
          {/* Both panels stay mounted: switching tabs must not throw away an in-flight chat
              stream or the conversation the user is reading. */}
          <div className={tab === 'chat' ? 'h-full' : 'hidden'}>
            <ChatPanel
              repoId={repo.id}
              onOpenCitation={openFromCitation}
              activeId={conversationId}
              onActiveIdChange={setConversationId}
            />
          </div>
          <div className={tab === 'dashboard' ? 'h-full' : 'hidden'}>
            <RepoDashboard repoId={repo.id} onOpenPath={openFromPath} />
          </div>
        </div>
      </div>

      {target && <FileOverlay repoId={repo.id} target={target} onClose={() => setTarget(null)} />}
    </div>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full px-4 py-1.5 text-sm transition ${
        active ? 'bg-zinc-800 font-medium text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'
      }`}
    >
      {children}
    </button>
  )
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 fill-current">
      <path d="M8 1a1 1 0 0 1 1 1v5h5a1 1 0 1 1 0 2H9v5a1 1 0 1 1-2 0V9H2a1 1 0 1 1 0-2h5V2a1 1 0 0 1 1-1Z" />
    </svg>
  )
}
