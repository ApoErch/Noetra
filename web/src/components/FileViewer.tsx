import Editor from '@monaco-editor/react'
import { languageForPath } from '../lib/language'

export type FileContent = {
  id: string
  path: string
  content: string | null
  is_binary: boolean
}

/** Read-only Monaco pane for the currently selected file; shows placeholders when nothing is selected or the file is binary. */
export function FileViewer({ file }: { file: FileContent | null }) {
  if (!file) {
    return (
      <div className="flex h-full items-center justify-center bg-zinc-950 text-sm text-zinc-500">
        Select a file to view it
      </div>
    )
  }

  if (file.is_binary) {
    return (
      <div className="flex h-full flex-col">
        <FileHeader path={file.path} />
        <div className="flex flex-1 items-center justify-center bg-zinc-950 text-sm text-zinc-500">
          Can't preview this file
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <FileHeader path={file.path} />
      <div className="flex-1">
        <Editor
          key={file.id}
          height="100%"
          path={file.path}
          language={languageForPath(file.path)}
          value={file.content ?? ''}
          theme="vs-dark"
          options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13 }}
        />
      </div>
    </div>
  )
}

function FileHeader({ path }: { path: string }) {
  return <div className="border-b border-zinc-800 bg-zinc-900 px-4 py-2 text-xs text-zinc-400">{path}</div>
}
