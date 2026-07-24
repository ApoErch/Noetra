export type FileEntry = {
  id: string
  path: string
  is_binary: boolean
}

export type TreeNode =
  | { type: 'folder'; name: string; path: string; children: TreeNode[] }
  | { type: 'file'; name: string; path: string; id: string; is_binary: boolean }

/** Fold a flat list of file paths (as returned by GET /repos/{id}/files) into a nested tree for a VS Code-style sidebar. */
export function buildFileTree(files: FileEntry[]): TreeNode[] {
  const root: TreeNode[] = []

  for (const file of files) {
    const parts = file.path.split('/')
    let level = root
    let pathSoFar = ''

    parts.forEach((part, i) => {
      pathSoFar = pathSoFar ? `${pathSoFar}/${part}` : part
      const isLast = i === parts.length - 1
      let node = level.find((n) => n.name === part)

      if (!node) {
        node = isLast
          ? { type: 'file', name: part, path: pathSoFar, id: file.id, is_binary: file.is_binary }
          : { type: 'folder', name: part, path: pathSoFar, children: [] }
        level.push(node)
      }

      if (node.type === 'folder') {
        level = node.children
      }
    })
  }

  sortTree(root)
  return root
}

function sortTree(nodes: TreeNode[]): void {
  nodes.sort((a, b) => {
    if (a.type !== b.type) return a.type === 'folder' ? -1 : 1
    return a.name.localeCompare(b.name)
  })
  for (const node of nodes) {
    if (node.type === 'folder') sortTree(node.children)
  }
}
