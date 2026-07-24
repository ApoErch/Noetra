import base64
import hashlib
import shutil
import subprocess
from pathlib import Path

from core.celery_app import celery_app
from core.config import get_settings
from core.db import SessionLocal
from core.models import (
    CodeEntity,
    DependencyEdge,
    EntityKind,
    File,
    Language,
    Repository,
    RepositoryStatus,
    User,
)
from core.redis_client import release_index_lock
from core.security import decrypt_token
from indexer.graph import resolve_dependencies
from indexer.parser import detect_language, extract

CLONE_TIMEOUT_SECONDS = 300
MAX_FILE_SIZE_BYTES = 1_000_000


def _hash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 a file in chunks instead of loading it whole into memory — matters for files too large to store content for."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


@celery_app.task
def clone_repository(repository_id: str) -> None:
    """Clone a queued `Repository` into local storage using its owner's decrypted GitHub token, updating status as it goes."""
    db = SessionLocal()
    try:
        repo = db.get(Repository, repository_id)
        if repo is None:
            return

        user = db.get(User, repo.user_id)
        token = decrypt_token(user.access_token)

        repo.status = RepositoryStatus.CLONING
        db.commit()

        dest = Path(get_settings().clone_storage_dir) / str(repo.id)
        if dest.exists():
            shutil.rmtree(dest)

        # GitHub's git-over-HTTPS server (not the REST API) only understands HTTP
        # Basic auth, not a `Bearer` header — Basic is what a URL like
        # https://<token>@github.com/... gets turned into internally anyway. Any
        # non-empty username works; GitHub itself uses "x-access-token" as the
        # convention. We build the header ourselves (instead of putting the token
        # in the clone URL) so it's never written into the resulting repo's config.
        basic_auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()

        try:
            result = subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "-c",
                    f"http.extraHeader=Authorization: Basic {basic_auth}",
                    repo.github_url,
                    str(dest),
                ],
                capture_output=True,
                text=True,
                timeout=CLONE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            # Caught separately (rather than letting it hit the generic `except
            # Exception` below) because TimeoutExpired's default string form
            # includes the full command list — which contains the Basic-auth
            # header with the token in it. We never want that written into
            # repo.error_message, since that's shown straight to the user.
            repo.status = RepositoryStatus.FAILED
            repo.error_message = f"Clone timed out after {CLONE_TIMEOUT_SECONDS}s"
            db.commit()
            return

        if result.returncode != 0:
            repo.status = RepositoryStatus.FAILED
            repo.error_message = result.stderr.strip()
            db.commit()
            return

        # `git clone -c http.extraHeader=...` persists that setting into the new
        # repo's .git/config (git does this deliberately so later `fetch`/`pull`
        # keep using the same auth) — which would leave the token sitting in
        # plaintext on disk. Strip it now that the clone is done; we don't need
        # this repo's git remote to stay authenticated going forward.
        subprocess.run(
            ["git", "config", "--unset-all", "http.extraHeader"],
            cwd=dest,
            capture_output=True,
        )

        # `git ls-files` lists every tracked path (respects .gitignore) — this is
        # the data source for the file tree browser, independent of the Tree-sitter
        # parsing stage (Milestone 4) that comes later. Content is stored in
        # Postgres, not read from disk at request time, so the API service never
        # needs filesystem access to this clone (see docs/WORKFLOW.md).
        # Collected alongside the db.add() calls below so the parsing stage
        # (after the commit) can walk these same objects directly instead of
        # re-querying — their `id`s aren't populated until the commit's flush,
        # but the Python objects themselves are already the right ones to use.
        file_rows: list[File] = []

        ls_files = subprocess.run(["git", "ls-files"], cwd=dest, capture_output=True, text=True)
        for rel_path in ls_files.stdout.splitlines():
            file_path = dest / rel_path

            # Some tracked paths are symlinks (e.g. the Linux kernel's
            # scripts/dtc/include-prefixes/* point at directories). git's actual
            # blob content for a symlink is the target path string itself, not
            # the linked-to file/directory — reading it a normal way would either
            # read through the link (wrong content) or, if it points at a
            # directory, raise IsADirectoryError. os.readlink reads the link
            # itself, matching what git considers that path's content to be.
            if file_path.is_symlink():
                file_row = File(
                    repository_id=repo.id,
                    path=rel_path,
                    content=None,
                    is_binary=True,
                    content_hash=hashlib.sha256(file_path.readlink().as_posix().encode()).hexdigest(),
                )
                db.add(file_row)
                file_rows.append(file_row)
                continue

            # Skip storing content for very large files — reading a huge file
            # into memory and into one Postgres row isn't worth it for a file
            # tree browser. Still record the file (with its hash) via chunked
            # reading so a giant file never has to sit in memory whole.
            if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
                file_row = File(
                    repository_id=repo.id,
                    path=rel_path,
                    content=None,
                    is_binary=True,
                    content_hash=_hash_file(file_path),
                )
                db.add(file_row)
                file_rows.append(file_row)
                continue

            raw = file_path.read_bytes()
            # A NUL byte is the classic "this is binary" signal (git itself uses it
            # for deciding whether to diff a file) — checked before attempting to
            # decode because NUL is technically valid UTF-8, so decoding alone
            # would let NUL-containing content through, and Postgres text columns
            # reject NUL bytes outright.
            if b"\x00" in raw:
                text = None
                is_binary = True
            else:
                try:
                    text = raw.decode("utf-8")
                    is_binary = False
                except UnicodeDecodeError:
                    text = None
                    is_binary = True

            file_row = File(
                repository_id=repo.id,
                path=rel_path,
                content=text,
                is_binary=is_binary,
                content_hash=hashlib.sha256(raw).hexdigest(),
            )
            db.add(file_row)
            file_rows.append(file_row)
        db.commit()

        # parsing — Tree-sitter over every py/js/jsx/ts/tsx file, extracting the
        # symbol table (functions/classes/methods) into `code_entities`. Each
        # file's raw imports are kept in memory (not persisted) for the
        # graphing stage right below — no reason to persist unresolved import
        # strings when resolution happens in the very next step of this task.
        repo.status = RepositoryStatus.PARSING
        db.commit()

        # (language, [module strings]) per parsed file path — graphing's input.
        file_imports: dict[str, tuple[str, list[str]]] = {}

        for file_row in file_rows:
            if file_row.is_binary or file_row.content is None:
                continue

            language = detect_language(file_row.path)
            if language is None:
                continue

            file_row.language = Language(language)
            file_row.loc = len(file_row.content.splitlines())

            extraction = extract(file_row.content, file_row.path)
            if extraction is None:
                continue

            for entity in extraction.entities:
                db.add(
                    CodeEntity(
                        repository_id=repo.id,
                        file_id=file_row.id,
                        kind=EntityKind(entity.kind),
                        name=entity.name,
                        signature=entity.signature,
                        start_line=entity.start_line,
                        end_line=entity.end_line,
                    )
                )

            file_imports[file_row.path] = (language, [imp.module for imp in extraction.imports])
        db.commit()

        # graphing — pure in-memory resolution (indexer/graph.py) of each
        # file's imports against every path this repo actually has, then
        # persisted as `dependency_edges`. Bare/third-party specifiers (stdlib,
        # npm packages) resolve to nothing and are silently dropped.
        repo.status = RepositoryStatus.GRAPHING
        db.commit()

        known_paths = {file_row.path for file_row in file_rows}
        path_to_file_id = {file_row.path: file_row.id for file_row in file_rows}

        for edge in resolve_dependencies(file_imports, known_paths):
            db.add(
                DependencyEdge(
                    repository_id=repo.id,
                    from_file_id=path_to_file_id[edge.from_path],
                    to_file_id=path_to_file_id[edge.to_path],
                )
            )
        db.commit()

        # Chunking/embedding/metrics don't exist yet, so status stays at
        # GRAPHING — the last stage actually completed — rather than jumping
        # to READY. READY is reserved for "chat + full hybrid search + metrics
        # all unlocked" (docs/WORKFLOW.md); claiming that now would be a lie
        # (flagged as a known gap in a prior session's notes).
    except Exception as exc:
        db.rollback()
        if repo is not None:
            repo.status = RepositoryStatus.FAILED
            repo.error_message = str(exc)
            db.commit()
        raise
    finally:
        db.close()
        release_index_lock(repository_id)
