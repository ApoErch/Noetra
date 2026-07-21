import { useState } from 'react'
import type { TreeNode } from '../lib/tree'

type Props = {
  nodes: TreeNode[]
  selectedFileId: string | null
  onSelectFile: (node: Extract<TreeNode, { type: 'file' }>) => void
}

/** File tree sidebar: blue folder icons, chevrons, flat per-depth indent, left-accent bar on the selected file. */
export function FileTree({ nodes, selectedFileId, onSelectFile }: Props) {
  return (
    <div>
      <div className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-zinc-500">Files</div>
      <div className="pb-2 text-[13px]">
        {nodes.map((node) => (
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
        className={`relative flex cursor-pointer items-center gap-2 py-1.5 pr-3 transition ${
          isSelected ? 'bg-zinc-800 font-medium text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'
        }`}
      >
        {isSelected && <span className="absolute inset-y-0 left-0 w-0.5 bg-indigo-500" />}
        <span className="shrink-0 text-zinc-500">
          <FileIcon />
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
        className="flex cursor-pointer select-none items-center gap-2 py-1.5 pr-3 text-zinc-300 transition hover:bg-zinc-900 hover:text-white"
      >
        <span className={`shrink-0 text-zinc-500 transition-transform ${open ? 'rotate-90' : ''}`}>
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
