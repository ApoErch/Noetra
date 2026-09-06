import { useState } from 'react'
import type { TreeNode } from '../lib/tree'

type Props = {
  nodes: TreeNode[]
  selectedFileId: string | null
  onSelectFile: (node: Extract<TreeNode, { type: 'file' }>) => void
}

/** File tree sidebar: blue folder icons, chevrons, flat per-depth indent, left-accent bar on the selected file. */
export function FileTree({ nodes, selectedFileId, onSelectFile }: Props) {
  // Folders sort before files at every level (see lib/tree.ts), so at the root the two groups
  // are already contiguous — a thin divider between them is enough to read as two sections.
  const folders = nodes.filter((n) => n.type === 'folder')
  const files = nodes.filter((n) => n.type === 'file')

  return (
    <div>
      <div className="px-4 py-4 text-xs font-semibold uppercase tracking-wider text-zinc-500">Files</div>
      <div className="pb-2 text-[13px]">
        {folders.map((node) => (
          <FileTreeNode key={node.path} node={node} depth={0} selectedFileId={selectedFileId} onSelectFile={onSelectFile} />
        ))}
        {folders.length > 0 && files.length > 0 && <div className="mx-4 my-2 border-t border-zinc-800" />}
        {files.map((node) => (
          <FileTreeNode key={node.path} node={node} depth={0} selectedFileId={selectedFileId} onSelectFile={onSelectFile} />
        ))}
      </div>
    </div>
  )
}

type NodeProps = {
  node: TreeNode
  depth: number
  selectedFileId: string | null
  onSelectFile: (node: Extract<TreeNode, { type: 'file' }>) => void
}

function FileTreeNode({ node, depth, selectedFileId, onSelectFile }: NodeProps) {
  const [open, setOpen] = useState(false)
  const paddingLeft = 16 + depth * 16

  if (node.type === 'file') {
    const isSelected = node.id === selectedFileId
    return (
      <div
        onClick={() => onSelectFile(node)}
        style={{ paddingLeft }}
        className={`relative flex cursor-pointer items-center gap-2 py-2 pr-3 transition ${
          isSelected ? 'bg-zinc-800 font-medium text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'
        }`}
      >
        {isSelected && <span className="absolute inset-y-0 left-0 w-0.5 bg-indigo-500" />}
        {/* Same width as the folder row's chevron, so file names line up with folder names
            at the same depth instead of sitting one icon further left. */}
        <span className="w-3 shrink-0" />
        <span className="shrink-0">
          <FileTypeIcon name={node.name} />
        </span>
        <span className="truncate">{node.name}</span>
      </div>
    )
  }

  return (
    <div>
      <div
        onClick={() => setOpen(!open)}
        style={{ paddingLeft }}
        className="flex cursor-pointer select-none items-center gap-2 py-2 pr-3 text-zinc-300 transition hover:bg-zinc-900 hover:text-white"
      >
        <span className={`w-3 shrink-0 text-zinc-500 transition-transform ${open ? 'rotate-90' : ''}`}>
          <ChevronIcon />
        </span>
        <span className="shrink-0 text-blue-400">
          <FolderIcon />
        </span>
        <span className="truncate">{node.name}</span>
      </div>
      {open &&
        node.children.map((child) => (
          <FileTreeNode
            key={child.path}
            node={child}
            depth={depth + 1}
            selectedFileId={selectedFileId}
            onSelectFile={onSelectFile}
          />
        ))}
    </div>
  )
}

function ChevronIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3 w-3 fill-current">
      <path d="M6 4l4 4-4 4V4z" />
    </svg>
  )
}

function FolderIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-4 w-4 fill-current">
      <path d="M1.75 2A1.75 1.75 0 0 0 0 3.75v8.5C0 13.216.784 14 1.75 14h12.5A1.75 1.75 0 0 0 16 12.25v-7A1.75 1.75 0 0 0 14.25 3.5H7.5L6.03 2.22A1.75 1.75 0 0 0 4.86 1.75H1.75Z" />
    </svg>
  )
}

function FileIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-4 w-4 fill-current">
      <path d="M4 1.5A1.5 1.5 0 0 1 5.5 0h4.086a1.5 1.5 0 0 1 1.06.44l2.914 2.914A1.5 1.5 0 0 1 14 4.414V14.5A1.5 1.5 0 0 1 12.5 16h-7A1.5 1.5 0 0 1 4 14.5v-13Z" />
    </svg>
  )
}

/** Maps a file extension/name to a small colored badge (letters on a rounded square), approximating a per-language icon set without pulling in an icon library. */
const FILE_BADGES: Record<string, { label: string; bg: string; text: string }> = {
  js: { label: 'JS', bg: 'bg-yellow-400', text: 'text-black' },
  jsx: { label: 'JSX', bg: 'bg-yellow-400', text: 'text-black' },
  ts: { label: 'TS', bg: 'bg-blue-500', text: 'text-white' },
  tsx: { label: 'TSX', bg: 'bg-blue-500', text: 'text-white' },
  py: { label: 'PY', bg: 'bg-sky-400', text: 'text-black' },
  json: { label: '{ }', bg: 'bg-orange-400', text: 'text-black' },
  md: { label: 'M↓', bg: 'bg-zinc-400', text: 'text-black' },
}

/** Picks a badge icon by file extension, falling back to a plain gray file glyph for unrecognized types. */
function FileTypeIcon({ name }: { name: string }) {
  const ext = name.includes('.') ? name.split('.').pop()!.toLowerCase() : ''
  const badge = FILE_BADGES[ext]

  if (!badge) {
    return (
      <span className="text-zinc-500">
        <FileIcon />
      </span>
    )
  }

  return (
    <span
      className={`flex h-4 w-4 items-center justify-center rounded-[3px] text-[8px] font-bold leading-none ${badge.bg} ${badge.text}`}
    >
      {badge.label}
    </span>
  )
}
