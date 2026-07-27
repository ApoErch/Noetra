import { useEffect, useRef, useState } from 'react'
import Editor from '@monaco-editor/react'
import { languageForPath } from '../lib/language'
import type { Highlight } from '../lib/search'

export type FileContent = {
  id: string
  path: string
  content: string | null
  is_binary: boolean
}

// Only the slice of Monaco's API this component touches, declared here rather than importing
// from `monaco-editor` — that package isn't installed (the React wrapper loads Monaco from a
// CDN at runtime), so pulling it in just for its types would add ~80MB of devDependency.
type DecorationsCollection = { clear: () => void }
type Decoration = { range: unknown; options: { isWholeLine: boolean; className: string } }
type CodeEditor = {
  revealLinesInCenter: (start: number, end: number) => void
  createDecorationsCollection: (decorations: Decoration[]) => DecorationsCollection
}
type MonacoApi = {
  Range: new (startLine: number, startCol: number, endLine: number, endCol: number) => unknown
}

/** Read-only Monaco pane; scrolls to and highlights a cited range when one is given. */
export function FileViewer({ file, highlight }: { file: FileContent | null; highlight?: Highlight | null }) {
  const editorRef = useRef<CodeEditor | null>(null)
  const monacoRef = useRef<MonacoApi | null>(null)
  const decorationsRef = useRef<DecorationsCollection | null>(null)
  // Bumped on every mount. Monaco loads asynchronously, so the highlight effect can't just
  // depend on the file — it has to re-run once the editor instance actually exists, and
  // again for each new instance (switching files remounts via `key`).
  const [editorGeneration, setEditorGeneration] = useState(0)

  useEffect(() => {
    const editor = editorRef.current
    const monaco = monacoRef.current
    if (!editor || !monaco) return

    decorationsRef.current?.clear()
    decorationsRef.current = null
    if (!highlight) return

    const { startLine, endLine, matchLine } = highlight
    const decorations: Decoration[] = [
      {
        range: new monaco.Range(startLine, 1, endLine, 1),
        options: { isWholeLine: true, className: 'noetra-cited-range' },
      },
    ]
    if (matchLine !== null) {
      decorations.push({
        range: new monaco.Range(matchLine, 1, matchLine, 1),
        options: { isWholeLine: true, className: 'noetra-match-line' },
      })
    }
    decorationsRef.current = editor.createDecorationsCollection(decorations)
    editor.revealLinesInCenter(startLine, endLine)
  }, [highlight, editorGeneration])

  if (!file) {
    return (
      <div className="flex h-full items-center justify-center bg-zinc-950 text-sm text-zinc-500">
        Select a file to view it
      </div>
    )
  }

  if (file.is_binary) {
    return (
      <div className="flex h-full items-center justify-center bg-zinc-950 text-sm text-zinc-500">
        Can't preview this file
      </div>
    )
  }

  return (
    <Editor
      key={file.id}
      height="100%"
      path={file.path}
      language={languageForPath(file.path)}
      value={file.content ?? ''}
      theme="vs-dark"
      options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false }}
      onMount={(editor, monaco) => {
        editorRef.current = editor as unknown as CodeEditor
        monacoRef.current = monaco as unknown as MonacoApi
        setEditorGeneration((generation) => generation + 1)
      }}
    />
  )
}
