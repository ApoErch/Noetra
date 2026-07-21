import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { API_URL, apiFetch } from './lib/api'

type Me = {
  id: string
  github_id: string
  username: string | null
  avatar_url: string | null
}

function App() {
  const queryClient = useQueryClient()

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
    onSuccess: () => queryClient.setQueryData(['me'], null),
  })

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-white">
      <h1 className="text-3xl font-semibold text-gray-900">Noetra</h1>

      {isLoading ? (
        <p className="text-gray-500">Loading…</p>
      ) : me ? (
        <div className="flex items-center gap-3">
          {me.avatar_url && (
            <img src={me.avatar_url} alt={me.username ?? 'avatar'} className="h-8 w-8 rounded-full" />
          )}
          <span className="text-gray-900">{me.username}</span>
          <button
            onClick={() => logout.mutate()}
            className="rounded bg-gray-200 px-3 py-1 text-sm text-gray-900 hover:bg-gray-300"
          >
            Log out
          </button>
        </div>
      ) : (
        <a
          href={`${API_URL}/api/v1/auth/login`}
          className="rounded bg-gray-900 px-4 py-2 text-white hover:bg-gray-800"
        >
          Log in with GitHub
        </a>
      )}
    </div>
  )
}

export default App
