import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { formatCitation, type SearchHit } from '../lib/search'

type Props = {
  repoId: string
  onOpenHit: (hit: SearchHit) => void
}

/** Hybrid search over an indexed repo: query box plus the ranked file:line hits it returns. */
export function SearchPanel({ repoId, onOpenHit }: Props) {
  const [input, setInput] = useState('')
  // Submitted separately from `input` so every keystroke doesn't fire a query — search runs
  // a full-text scan plus a relaxation pass, which is too much to do per character.
  const [query, setQuery] = useState('')

  const {
    data: hits,
    isFetching,
    error,
  } = useQuery<SearchHit[]>({
    queryKey: ['search', repoId, query],
    queryFn: () =>
      apiFetch<SearchHit[]>(`/api/v1/repos/${repoId}/search?q=${encodeURIComponent(query)}`),
    enabled: query.length > 0,
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    setQuery(input.trim())
  }

  return (
    <div className="flex h-full flex-col">
      <form onSubmit={onSubmit} className="border-b border-zinc-800 px-6 py-4">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Search this repo — a symbol name, a phrase, or a question"
            className="flex-1 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={!input.trim()}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-30"
          >
            Search
          </button>
        </div>
      </form>

      <div className="flex-1 overflow-y-auto">
        {!query && (
          <p className="px-6 py-8 text-sm text-zinc-500">
            Results cite a file and line range. Click one to open it here without losing your search.
          </p>
        )}
        {error && <p className="px-6 py-8 text-sm text-red-400">Search failed: {error.message}</p>}
        {query && isFetching && <p className="px-6 py-8 text-sm text-zinc-500">Searching…</p>}
        {query && !isFetching && hits?.length === 0 && (
          <p className="px-6 py-8 text-sm text-zinc-500">No matches for “{query}”.</p>
        )}
        {!isFetching &&
          hits?.map((hit) => (
            <SearchResult
              key={`${hit.file_id}:${hit.start_line}-${hit.end_line}`}
              hit={hit}
              onOpen={() => onOpenHit(hit)}
            />
          ))}
      </div>
    </div>
  )
}

function SearchResult({ hit, onOpen }: { hit: SearchHit; onOpen: () => void }) {
  return (
    <div
      onClick={onOpen}
      className="cursor-pointer border-b border-zinc-900 px-6 py-3 transition hover:bg-zinc-900"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-indigo-400">{formatCitation(hit)}</span>
        {hit.entity_name && (
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">
            {hit.entity_kind} {hit.entity_name}
          </span>
        )}
        <span className="ml-auto flex gap-1">
          {hit.sources.map((source) => (
            <span
              key={source}
              // Which retriever found it — worth surfacing, since a hit both retrievers
              // agree on is the strongest signal the fusion produces.
              className={`rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
                source === 'structural'
                  ? 'bg-violet-500/10 text-violet-400 ring-1 ring-violet-500/20'
                  : 'bg-sky-500/10 text-sky-400 ring-1 ring-sky-500/20'
              }`}
            >
              {source}
            </span>
          ))}
        </span>
      </div>
      <pre className="mt-1.5 truncate font-mono text-[13px] text-zinc-400">{hit.snippet}</pre>
    </div>
  )
}
