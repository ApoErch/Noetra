import hashlib
import subprocess
import time
from pathlib import Path

from celery.utils.log import get_task_logger
from sqlalchemy.orm import Session

from core.models import (
    Chunk,
    CodeEntity,
    DependencyEdge,
    EntityKind,
    File,
    Language,
    Repository,
    RepositoryStatus,
)
from indexer.chunker import chunk_file
from indexer.graph import resolve_dependencies
from indexer.parser import ExtractedEntity, detect_language, extract

MAX_FILE_SIZE_BYTES = 1_000_000

logger = get_task_logger(__name__)


def _hash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 a file in chunks instead of loading it whole into memory — matters for files too large to store content for."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def index_repository_files(db: Session, repo: Repository, repo_dir: Path) -> None:
    """Walk a cloned repo's tracked files, extract symbols, resolve the import graph, chunk, and persist.

    The shared indexing core called by BOTH the production clone task and the eval seed, so both
    produce identical File/CodeEntity/DependencyEdge/Chunk rows. Assumes `repo_dir` is already a
    cloned git checkout and `repo.status` is CLONING; advances it through PARSING, GRAPHING, CHUNKING.
    """
    stage_start = time.perf_counter()

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

    ls_files = subprocess.run(["git", "ls-files"], cwd=repo_dir, capture_output=True, text=True)
    for rel_path in ls_files.stdout.splitlines():
        file_path = repo_dir / rel_path

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

    logger.info("repo %s: files stage took %.2fs", repo.id, time.perf_counter() - stage_start)
    stage_start = time.perf_counter()

    # parsing — Tree-sitter over every py/js/jsx/ts/tsx file, extracting the
    # symbol table (functions/classes/methods) into `code_entities`. Each
    # file's raw imports are kept in memory (not persisted) for the
    # graphing stage right below — no reason to persist unresolved import
    # strings when resolution happens in the very next step.
    repo.status = RepositoryStatus.PARSING
    db.commit()

    # (language, [module strings]) per parsed file path — graphing's input.
    file_imports: dict[str, tuple[str, list[str]]] = {}
    # (extracted entities, their persisted rows) per parsed file path — chunking's input.
    # It needs both: the entities to align chunk boundaries to real syntax, and the rows to
    # link each chunk back to its symbol. Kept parallel, so index i matches in both lists.
    file_entities: dict[str, tuple[list[ExtractedEntity], list[CodeEntity]]] = {}

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

        entity_rows = [
            CodeEntity(
                repository_id=repo.id,
                file_id=file_row.id,
                kind=EntityKind(entity.kind),
                name=entity.name,
                signature=entity.signature,
                start_line=entity.start_line,
                end_line=entity.end_line,
            )
            for entity in extraction.entities
        ]
        for entity_row in entity_rows:
            db.add(entity_row)

        file_entities[file_row.path] = (extraction.entities, entity_rows)
        file_imports[file_row.path] = (language, [imp.module for imp in extraction.imports])
    db.commit()

    logger.info("repo %s: parse stage took %.2fs", repo.id, time.perf_counter() - stage_start)
    stage_start = time.perf_counter()

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

    logger.info("repo %s: graph stage took %.2fs", repo.id, time.perf_counter() - stage_start)
    stage_start = time.perf_counter()

    # chunking — split every text file into AST-aligned retrieval units
    # (indexer/chunker.py). This runs over *all* text files, not just parsed
    # py/js/ts ones: a file with no entities (markdown, JSON, config) still
    # chunks into gap chunks, so nothing in the repo becomes unsearchable.
    # Chunks are what retrieval returns, and each one carries a real line
    # range — which is what makes a citation exact instead of guessed.
    repo.status = RepositoryStatus.CHUNKING
    db.commit()

    for file_row in file_rows:
        if file_row.is_binary or file_row.content is None:
            continue

        entities, entity_rows = file_entities.get(file_row.path, ([], []))
        for chunk in chunk_file(file_row.content, file_row.path, entities):
            db.add(
                Chunk(
                    repository_id=repo.id,
                    file_id=file_row.id,
                    entity_id=(
                        entity_rows[chunk.entity_index].id if chunk.entity_index is not None else None
                    ),
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                    embed_text=chunk.embed_text,
                )
            )
    db.commit()

    logger.info("repo %s: chunk stage took %.2fs", repo.id, time.perf_counter() - stage_start)
