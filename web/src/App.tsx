import { useState } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { API_URL, apiFetch } from './lib/api'
import { RepoList } from './components/RepoList'
import type { Repo } from './lib/repos'
import { RepoExplorer } from './components/RepoExplorer'

type Me = {
  id: string
  github_id: string
  username: string | null
  avatar_url: string | null
}

function App() {
  const queryClient = useQueryClient()
  const [openRepo, setOpenRepo] = useState<Repo | null>(null)

  const { data: me, isLoading } = useQuery<Me | null>({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await apiFetch<Me>('/api/v1/auth/me')
      } catch {
        return null
      }
    },
  })

  const logout = useMutation({
    mutationFn: () => apiFetch('/api/v1/auth/logout', { method: 'POST' }),
    onSuccess: () => {
      // Clear the whole cache, not just ['me'] — repos, file trees and conversations all
      // belong to the user who just left, and the next login in this tab would render them
      // before its own fetches land.
      queryClient.clear()
      setOpenRepo(null)
    },
  })

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    )
  }

  if (!me) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-gradient-to-b from-zinc-950 to-black px-4">
        <div className="flex flex-col items-center gap-2">
          <img src="/Logo.png" alt="Noetra" className="h-64 w-64 object-contain drop-shadow-lg drop-shadow-indigo-600/30" />
          <h1 className="text-2xl font-semibold tracking-tight text-violet-400">Noetra</h1>
          <p className="text-sm text-zinc-400">Chat with and search any codebase</p>
        </div>
        <a
          href={`${API_URL}/api/v1/auth/login`}
          className="flex items-center gap-2 rounded-lg bg-white px-5 py-2.5 text-sm font-medium text-zinc-900 shadow-sm transition hover:bg-zinc-200"
        >
          <svg viewBox="0 0 16 16" className="h-4 w-4 fill-current">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
          </svg>
          Log in with GitHub
        </a>
      </div>
    )
  }

  if (openRepo) {
    return <RepoExplorer repo={openRepo} onBack={() => setOpenRepo(null)} />
  }

  return (
    <div className="min-h-screen bg-zinc-950">
      <header className="sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-2">
            <img src="/Logo.png" alt="Noetra" className="h-20 w-20 object-contain" />
            <span className="font-semibold text-violet-400">Noetra</span>
          </div>
          <div className="flex items-center gap-3">
            {me.avatar_url && (
              <img src={me.avatar_url} alt={me.username ?? 'avatar'} className="h-7 w-7 rounded-full ring-1 ring-zinc-700" />
            )}
            <span className="text-sm text-zinc-300">{me.username}</span>
            <button
              onClick={() => logout.mutate()}
              className="rounded-md px-2.5 py-1 text-sm text-zinc-400 transition hover:bg-zinc-800 hover:text-white"
            >
              Log out
            </button>
          </div>
        </div>
      </header>
      <RepoList onOpen={setOpenRepo} />
    </div>
  )
}

export default App
