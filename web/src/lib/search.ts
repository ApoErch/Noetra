/** One hit from GET /repos/{id}/search — mirrors `RetrievalHit` in backend/core/retrieval/types.py. */
export type SearchHit = {
  file_id: string
  path: string
  start_line: number
  end_line: number
  snippet: string
  score: number
  sources: string[]
  entity_name: string | null
  entity_kind: string | null
  /** Absolute file line of `snippet` — the line inside the range that actually matched. */
  match_line: number | null
}

/** The lines a citation points at: the block that answers, plus the line that matched inside it. */
export type Highlight = {
  startLine: number
  endLine: number
  matchLine: number | null
}

/** What the file overlay is currently showing, and where in it to look. */
export type ViewerTarget = {
  fileId: string
  path: string
  isBinary: boolean
  /** Null when the file was opened from the tree rather than from a citation. */
  highlight: Highlight | null
}

/** Render a citation the way it's written in prose and in the agent's answers: `path:12-40`. */
export function formatCitation(hit: Pick<SearchHit, 'path' | 'start_line' | 'end_line'>): string {
  const lines = hit.start_line === hit.end_line ? `${hit.start_line}` : `${hit.start_line}-${hit.end_line}`
  return `${hit.path}:${lines}`
}
