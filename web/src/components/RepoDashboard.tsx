import { useQuery } from '@tanstack/react-query'
import { fetchRepoMetrics, type LanguageSlice, type RepoMetrics } from '../lib/metrics'

// Categorical hues in fixed order — assigned to the language, never to its rank, so a repo
// that is mostly TypeScript doesn't repaint Python. Validated against the zinc-900 card
// surface in dark mode (lightness band, chroma, CVD separation, contrast). Deliberately not
// the indigo used for actions or the violet reserved for the wordmark.
const LANGUAGE_COLORS: Record<string, string> = {
  python: '#0284c7',
  javascript: '#d97706',
  typescript: '#ec4899',
}
const FALLBACK_COLOR = '#71717a'

const LANGUAGE_LABELS: Record<string, string> = {
  python: 'Python',
  javascript: 'JavaScript',
  typescript: 'TypeScript',
}

function format(value: number): string {
  return value.toLocaleString()
}

/** One headline number with its label, and a quieter second line for the caveat it needs. */
function StatTile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-3">
      <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-white">{value}</p>
      {note && <p className="mt-0.5 text-xs text-zinc-500">{note}</p>}
    </div>
  )
}

/** Stacked share-of-total bar plus its legend — the legend carries the identity, not the colour alone. */
function LanguageBreakdown({ slices }: { slices: LanguageSlice[] }) {
  const totalLoc = slices.reduce((sum, slice) => sum + slice.loc, 0)
  if (totalLoc === 0) {
    return <p className="text-sm text-zinc-500">No Python, JavaScript or TypeScript source was found in this repo.</p>
  }

  return (
    <>
      {/* 2px gaps between segments so adjacent fills stay separable without relying on hue. */}
      <div className="flex h-2.5 w-full gap-0.5 overflow-hidden rounded-full">
        {slices.map((slice) => (
          <div
            key={slice.language}
            title={`${LANGUAGE_LABELS[slice.language] ?? slice.language} — ${format(slice.loc)} lines`}
            style={{
              width: `${(slice.loc / totalLoc) * 100}%`,
              backgroundColor: LANGUAGE_COLORS[slice.language] ?? FALLBACK_COLOR,
            }}
          />
        ))}
      </div>
      <ul className="mt-4 flex flex-col gap-2">
        {slices.map((slice) => (
          <li key={slice.language} className="flex items-center gap-2.5 text-sm">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: LANGUAGE_COLORS[slice.language] ?? FALLBACK_COLOR }}
            />
            <span className="text-zinc-200">{LANGUAGE_LABELS[slice.language] ?? slice.language}</span>
            <span className="ml-auto tabular-nums text-zinc-400">
              {Math.round((slice.loc / totalLoc) * 100)}%
            </span>
            <span className="w-28 shrink-0 text-right tabular-nums text-zinc-500">
              {format(slice.loc)} lines
            </span>
            <span className="w-20 shrink-0 text-right tabular-nums text-zinc-500">
              {format(slice.files)} files
            </span>
          </li>
        ))}
      </ul>
    </>
  )
}

/** Ranked list with a magnitude bar behind each row — one hue, because this encodes size, not identity. */
function LargestFiles({ files, onOpenPath }: { files: RepoMetrics['largest_files']; onOpenPath: (path: string) => void }) {
  if (files.length === 0) {
    return <p className="text-sm text-zinc-500">Nothing to rank — no source files were parsed.</p>
  }
  const max = files[0].loc

  return (
    <ul className="flex flex-col gap-1">
      {files.map((file) => (
        <li key={file.path}>
          <button
            onClick={() => onOpenPath(file.path)}
            className="group relative flex w-full items-center justify-between gap-4 overflow-hidden rounded-md px-2 py-1.5 text-left transition hover:bg-zinc-800/60"
          >
            <span
              aria-hidden
              className="absolute inset-y-0 left-0 rounded-md bg-indigo-500/10"
              style={{ width: `${(file.loc / max) * 100}%` }}
            />
            <span className="relative truncate font-mono text-xs text-zinc-300 group-hover:text-white">
              {file.path}
            </span>
            <span className="relative shrink-0 tabular-nums text-xs text-zinc-500">{format(file.loc)}</span>
          </button>
        </li>
      ))}
    </ul>
  )
}

/** Repository dashboard: the aggregates the metrics pipeline stage wrote at the end of indexing. */
export function RepoDashboard({ repoId, onOpenPath }: { repoId: string; onOpenPath: (path: string) => void }) {
  const { data, isLoading, error } = useQuery<RepoMetrics>({
    queryKey: ['metrics', repoId],
    queryFn: () => fetchRepoMetrics(repoId),
  })

  if (isLoading) return <p className="p-6 text-sm text-zinc-500">Loading metrics…</p>
  if (error || !data) return <p className="p-6 text-sm text-red-400">Could not load metrics for this repository.</p>

  const functions = data.function_count.function + data.function_count.method

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            label="Files"
            value={format(data.file_count.total)}
            note={`${format(data.file_count.source)} source`}
          />
          <StatTile
            label="Functions"
            value={format(functions)}
            note={`${format(data.function_count.method)} methods`}
          />
          <StatTile label="Classes" value={format(data.function_count.class)} />
          <StatTile
            label="Lines of code"
            value={format(data.total_loc.source_loc)}
            note="source files only"
          />
        </div>

        <section className="rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-4">
          <h3 className="text-sm font-semibold text-white">Languages</h3>
          <p className="mt-0.5 mb-4 text-xs text-zinc-500">
            By lines of code. Markdown, JSON and other unparsed files are excluded.
          </p>
          <LanguageBreakdown slices={data.language_breakdown} />
        </section>

        <section className="rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-4">
          <h3 className="text-sm font-semibold text-white">Largest source files</h3>
          <p className="mt-0.5 mb-3 text-xs text-zinc-500">Click one to open it.</p>
          <LargestFiles files={data.largest_files} onOpenPath={onOpenPath} />
        </section>
      </div>
    </div>
  )
}
