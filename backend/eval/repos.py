from dataclasses import dataclass


@dataclass(frozen=True)
class EvalRepo:
    """One pinned repository the eval harness indexes and scores retrieval against."""

    key: str  # short name referenced by questions.yaml (e.g. "noetra")
    github_url: str  # canonical repo URL, fetched at `sha` and stored on the Repository row
    sha: str  # the exact commit pinned so answer-key line numbers never drift
    private: bool = False  # True → clone needs auth (EVAL_GITHUB_TOKEN); noetra is private


# Every repo is pinned to an immutable commit SHA (not a branch) so the answer
# locations in questions.yaml stay valid forever — a branch tip moves upstream and
# shifts line numbers out from under the keys. All three are fetched from GitHub at
# their SHA; noetra is private so its fetch is token-authenticated, and indexing a
# frozen snapshot keeps its keys valid while we keep editing `develop`.
EVAL_REPOS: list[EvalRepo] = [
    EvalRepo(
        key="noetra",
        github_url="https://github.com/ApoErch/Noetra",
        sha="31fc158c7e3e713d200e06ef51e4cee69a912050",
        private=True,
    ),
    EvalRepo(
        key="requests",
        github_url="https://github.com/psf/requests",
        sha="69f84847045bef7a849cc994a26fe7ba8a169e95",
    ),
    EvalRepo(
        key="zod",
        github_url="https://github.com/colinhacks/zod",
        sha="912f0f51b0ced654d0069741e7160834dca742ee",
    ),
]

EVAL_REPOS_BY_KEY: dict[str, EvalRepo] = {repo.key: repo for repo in EVAL_REPOS}

# What `--repos` means when omitted. Day-to-day runs are demonstrated on noetra alone;
# `--repos all` (or an explicit list) brings the other two back in.
DEFAULT_EVAL_REPOS: tuple[str, ...] = ("noetra",)


def select_repos(raw: str | None) -> list[EvalRepo]:
    """Resolve a `--repos` value: None → DEFAULT_EVAL_REPOS, "all" → every repo, else comma-separated keys."""
    if raw is None:
        keys: tuple[str, ...] = DEFAULT_EVAL_REPOS
    elif raw.strip() == "all":
        keys = tuple(EVAL_REPOS_BY_KEY)
    else:
        keys = tuple(k.strip() for k in raw.split(","))
    unknown = [k for k in keys if k not in EVAL_REPOS_BY_KEY]
    if unknown:
        raise ValueError(f"unknown repo(s) {unknown} — choose from {sorted(EVAL_REPOS_BY_KEY)} or 'all'")
    return [EVAL_REPOS_BY_KEY[k] for k in keys]
