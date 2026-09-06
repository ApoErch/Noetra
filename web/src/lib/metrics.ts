import { apiFetch } from './api'

export type LanguageSlice = { language: string; files: number; loc: number }
export type LargestFile = { path: string; loc: number }

/**
 * The five dashboard aggregates, written once by the pipeline's metrics stage.
 *
 * `loc` and the language breakdown cover *source* files only — the backend only records
 * lines and a language for the parsed languages, so Markdown, JSON and binaries are counted
 * in `file_count.total` and nowhere else. The UI labels them accordingly.
 */
export type RepoMetrics = {
  file_count: { total: number; source: number }
  function_count: { function: number; class: number; method: number }
  total_loc: { source_loc: number }
  language_breakdown: LanguageSlice[]
  largest_files: LargestFile[]
}

export function fetchRepoMetrics(repoId: string): Promise<RepoMetrics> {
  return apiFetch<RepoMetrics>(`/api/v1/repos/${repoId}/metrics`)
}
