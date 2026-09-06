export type RepoStatus =
  | 'queued'
  | 'cloning'
  | 'parsing'
  | 'graphing'
  | 'chunking'
  | 'embedding'
  | 'metrics'
  | 'ready'
  | 'failed'

export type Repo = {
  id: string
  github_url: string
  name: string
  status: RepoStatus
  error_message: string | null
  // Whether a job is actually running right now (the API reads the Redis index lock).
  // `status` alone can't tell "embedding" from "stopped while embedding".
  is_indexing: boolean
}

// The pipeline in order, mirroring RepositoryStatus in backend/core/models.py. `failed` is
// not a stage, so it is not in here. The index of a status inside this array is the whole
// progress mechanism — there is no `progress` column in the database, and this answers the
// same question ("how far along is it?") for free.
export const PIPELINE_STAGES: RepoStatus[] = [
  'queued',
  'cloning',
  'parsing',
  'graphing',
  'chunking',
  'embedding',
  'metrics',
  'ready',
]

const STAGE_LABELS: Record<RepoStatus, string> = {
  queued: 'Queued',
  cloning: 'Cloning',
  parsing: 'Reading code',
  graphing: 'Mapping the graph',
  chunking: 'Chunking',
  embedding: 'Embedding',
  metrics: 'Measuring',
  ready: 'Ready',
  failed: 'Failed',
}

/** Human text for a status, plus a step counter while the pipeline is still running. */
export function describeStatus(repo: Repo): string {
  const label = STAGE_LABELS[repo.status] ?? repo.status
  const step = PIPELINE_STAGES.indexOf(repo.status)
  if (step < 0 || repo.status === 'ready') return label
  if (!repo.is_indexing) return `Stopped at ${label.toLowerCase()}`
  return `${label} · step ${step + 1} of ${PIPELINE_STAGES.length}`
}

/** How far through the pipeline a repo is, 0–1 — drives the progress bar on the repo card. */
export function stageProgress(status: RepoStatus): number {
  const step = PIPELINE_STAGES.indexOf(status)
  if (step < 0) return 0
  return step / (PIPELINE_STAGES.length - 1)
}

/**
 * A repo opens only once it is fully indexed.
 *
 * The pipeline unlocks surfaces progressively (files after cloning, chat once chunks exist)
 * and the API still enforces exactly that, but the product presents one completion state
 * instead of a workspace where half the tabs are inert. Flip this to a set of statuses to
 * go back to staged unlock — nothing else depends on it. See docs/CONCEPTS.md B28.
 */
export function isOpenable(status: RepoStatus): boolean {
  return status === 'ready'
}

/**
 * Which kind of restart to offer, if any.
 *
 * `retry` re-clones a broken repo from scratch; `resume` re-runs only the pipeline's tail on
 * a repo whose index is intact. A repo whose job is still running gets neither — the API
 * would refuse it on the index lock anyway, so offering the button would be a lie.
 */
export function retryAction(repo: Repo): 'retry' | 'resume' | null {
  if (repo.status === 'failed') return 'retry'
  if (repo.is_indexing) return null
  if (repo.status === 'embedding' || repo.status === 'metrics') return 'resume'
  return null
}
