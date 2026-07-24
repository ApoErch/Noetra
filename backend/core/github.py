import re
from dataclasses import dataclass

import httpx

from core.config import get_settings

GITHUB_REPO_URL_RE = re.compile(r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(\.git)?/?$")


def parse_repo_slug(github_url: str) -> str:
    """Validate a GitHub repo URL and return its "owner/repo" slug. Raises ValueError if invalid."""
    match = GITHUB_REPO_URL_RE.match(github_url)
    if not match:
        raise ValueError("Must be a GitHub repository URL, e.g. https://github.com/owner/repo")
    return f"{match.group('owner')}/{match.group('repo')}"


AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"

# read:user — profile (id, username, avatar). repo — clone/read private repos (V1 import flow).
SCOPES = "read:user repo"

# GitHub rejects/blocks requests without a User-Agent — see docs.github.com REST API getting-started.
USER_AGENT = "Noetra"


@dataclass
class GithubProfile:
    """The subset of a GitHub user we care about: their stable id, username, and avatar."""

    github_id: int
    username: str
    avatar_url: str | None


def build_authorize_url(state: str) -> str:
    """Build the GitHub consent URL we redirect the user to, carrying our client id, callback, scopes, and CSRF state."""
    settings = get_settings()
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_oauth_callback,
        "scope": SCOPES,
        "state": state,
    }
    query = httpx.QueryParams(params)
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code_for_token(code: str) -> str:
    """Trade the one-time OAuth `code` GitHub handed back for a durable access token."""
    settings = get_settings()
    response = httpx.post(
        TOKEN_URL,
        data={
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
            "redirect_uri": settings.github_oauth_callback,
        },
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if "access_token" not in payload:
        raise RuntimeError(f"GitHub token exchange failed: {payload}")
    return payload["access_token"]


def fetch_github_profile(access_token: str) -> GithubProfile:
    """Call GitHub's /user endpoint with the token to read the authenticated user's profile."""
    response = httpx.get(
        USER_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return GithubProfile(
        github_id=payload["id"],
        username=payload["login"],
        avatar_url=payload.get("avatar_url"),
    )
