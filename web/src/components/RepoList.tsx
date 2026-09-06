import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { describeStatus, isOpenable, retryAction, stageProgress, type Repo, type RepoStatus } from '../lib/repos'

// Each pipeline stage gets its own hue (in pipeline order) so the badge
// itself communicates progress, not just "still working" vs. "done".
const STATUS_STYLES: Record<RepoStatus, string> = {
  queued: 'bg-zinc-800 text-zinc-300 ring-zinc-700',
  cloning: 'bg-amber-500/10 text-amber-400 ring-amber-500/20',
  parsing: 'bg-blue-500/10 text-blue-400 ring-blue-500/20',
  graphing: 'bg-violet-500/10 text-violet-400 ring-violet-500/20',
  chunking: 'bg-fuchsia-500/10 text-fuchsia-400 ring-fuchsia-500/20',
  embedding: 'bg-cyan-500/10 text-cyan-400 ring-cyan-500/20',
  metrics: 'bg-teal-500/10 text-teal-400 ring-teal-500/20',
  ready: 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/20',
  failed: 'bg-red-500/10 text-red-400 ring-red-500/20',
}

function StatusBadge({ repo }: { repo: Repo }) {
  const style = STATUS_STYLES[repo.status] ?? STATUS_STYLES.queued
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}>
      {describeStatus(repo)}
    </span>
  )
}

/** A thin bar filling as the repo moves through the pipeline — the wait is long enough to need one. */
function StageProgress({ repo }: { repo: Repo }) {
  return (
    <div className="mt-2 h-0.5 w-full overflow-hidden rounded-full bg-zinc-800">
      <div
        className={`h-full rounded-full transition-all duration-500 ${repo.is_indexing ? 'bg-indigo-500' : 'bg-zinc-600'}`}
        style={{ width: `${Math.round(stageProgress(repo.status) * 100)}%` }}
      />
    </div>
  )
}

/** Minimal repo picker: paste a GitHub URL to import, watch it index, open it once it's ready. */
export function RepoList({ onOpen }: { onOpen: (repo: Repo) => void }) {
  const [url, setUrl] = useState('')
  const queryClient = useQueryClient()

  const { data: repos, isLoading } = useQuery<Repo[]>({
    queryKey: ['repos'],
    queryFn: () => apiFetch<Repo[]>('/api/v1/repos'),
    refetchInterval: 3000,
  })

  const importRepo = useMutation({
    mutationFn: () =>
      apiFetch('/api/v1/repos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ github_url: url }),
      }),
    onSuccess: () => {
      setUrl('')
      queryClient.invalidateQueries({ queryKey: ['repos'] })
    },
  })

  const retryRepo = useMutation({
    mutationFn: (repoId: string) => apiFetch(`/api/v1/repos/${repoId}/retry`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['repos'] }),
  })

  const removeRepo = useMutation({
    mutationFn: (repoId: string) => apiFetch(`/api/v1/repos/${repoId}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['repos'] }),
  })

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-10">
      <h2 className="text-xl font-semibold tracking-tight text-white">Your repositories</h2>
      <p className="mt-1 text-sm text-zinc-400">Import a public or private GitHub repo to browse and chat with it.</p>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          importRepo.mutate()
        }}
        className="mt-6 flex gap-2"
      >
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://github.com/owner/repo"
          className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2 text-sm text-white placeholder:text-zinc-500 shadow-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
        />
        <button
          type="submit"
          disabled={importRepo.isPending || !url}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {importRepo.isPending ? 'Importing…' : 'Import'}
        </button>
      </form>
      {importRepo.isError && <p className="mt-2 text-sm text-red-400">{(importRepo.error as Error).message}</p>}

      {isLoading ? (
        <p className="mt-8 text-sm text-zinc-500">Loading…</p>
      ) : (
        <ul className="mt-8 flex flex-col gap-2">
          {repos?.length === 0 && (
            <li className="rounded-xl border border-dashed border-zinc-700 px-4 py-8 text-center text-sm text-zinc-500">
              No repositories yet — import one above to get started.
            </li>
          )}
          {repos?.map((repo) => {
            const restart = retryAction(repo)
            return (
              <li
                key={repo.id}
                className="rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-3 shadow-sm transition hover:border-zinc-700"
              >
                <div className="flex items-center justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-white">{repo.name}</p>
                    <div className="mt-1">
                      <StatusBadge repo={repo} />
                    </div>
                    {repo.status !== 'ready' && repo.status !== 'failed' && <StageProgress repo={repo} />}
                  </div>
                  <div className="flex shrink-0 gap-2">
                    {restart && (
                      <button
                        onClick={() => retryRepo.mutate(repo.id)}
                        disabled={retryRepo.isPending}
                        className="rounded-md bg-zinc-800 px-2.5 py-1.5 text-xs font-medium text-zinc-200 transition hover:bg-zinc-700 disabled:opacity-40"
                      >
                        {restart === 'retry' ? 'Retry' : 'Resume'}
                      </button>
                    )}
                    <button
                      onClick={() => removeRepo.mutate(repo.id)}
                      disabled={removeRepo.isPending}
                      className="rounded-md bg-red-500/10 px-2.5 py-1.5 text-xs font-medium text-red-400 transition hover:bg-red-500/20 disabled:opacity-40"
                    >
                      Remove
                    </button>
                    <button
                      onClick={() => onOpen(repo)}
                      disabled={!isOpenable(repo.status)}
                      title={isOpenable(repo.status) ? undefined : 'Available once indexing finishes'}
                      className="rounded-md bg-indigo-600 px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-30"
                    >
                      Open
                    </button>
                  </div>
                </div>
                {repo.status === 'failed' && repo.error_message && (
                  <p className="mt-2 truncate text-xs text-red-400" title={repo.error_message}>
                    {repo.error_message}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
