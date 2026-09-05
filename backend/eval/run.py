import argparse
import enum
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import SessionLocal
from core.models import File
from core.retrieval import ALL_LEGS, search
from core.retrieval.types import RetrieverSource
from eval.repos import EVAL_REPOS_BY_KEY, select_repos
from eval.seed import eval_id

_LEG_NAMES = {"lexical": RetrieverSource.LEXICAL, "semantic": RetrieverSource.SEMANTIC}


def parse_legs(raw: str | None) -> tuple[RetrieverSource, ...] | None:
    """Parse `--legs lexical,semantic` into what search() expects; None (both legs) if unset."""
    if raw is None:
        return None
    try:
        return tuple(_LEG_NAMES[name.strip()] for name in raw.split(","))
    except KeyError as exc:
        raise ValueError(f"unknown leg {exc.args[0]!r} — choose from {sorted(_LEG_NAMES)}") from exc

_QUESTIONS_PATH = Path(__file__).parent / "questions.yaml"

# The cutoffs the scoreboard reports. 5 is "what the user sees without scrolling";
# 20 is "what the M8 agent can afford to read" — a retriever that only clears @20 is
# still usable by the agent but not by the search UI.
_KS = (5, 20)


class QuestionKind(str, enum.Enum):
    """What kind of retrieval a question exercises — the breakdown the M6/M7 leg measurements are scored by."""

    SYMBOL = "symbol"  # names an identifier that exists verbatim; lexical's job too, just a distinct shape of query
    KEYWORD = "keyword"  # words that literally appear in the source; lexical's job
    CONCEPTUAL = "conceptual"  # the user's words appear nowhere in the code; only embeddings can help
    # Enumeration questions ("every caller of X", "what does Y invoke") whose answer is a
    # SET of locations, not one. They exist because the M7 agent eval saturated at 0.91 with
    # ~1 question of real headroom, and none of the other kinds can move: code_search caps at
    # MAX_CHUNKS_PER_FILE=2 (core/retrieval/types.py), so "list every call site in this file"
    # is out of reach of the current tools by construction, not by ranking. Scored on
    # coverage of the whole key in eval/agent.py, not on a single overlap — this is the
    # bucket M8's graph tools are measured on, tools on vs. off.
    GRAPH = "graph"
    # A guard-rail bucket, not a retrieval target: the answer genuinely lives in prose
    # (README, docs page), so these fail if the non-source rank penalty in
    # core/retrieval/lexical.py is tuned so hard that documentation stops surfacing at all.
    # Kept as its own kind so the three buckets above keep comparable denominators.
    DOCS = "docs"


@dataclass(frozen=True)
class AnswerLocation:
    """One acceptable answer location: a repo-relative path and the line range containing the answer."""

    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class EvalQuestion:
    """One pinned question: the query, the repo it is asked against, and every location that counts as correct."""

    query: str
    repo: str
    kind: QuestionKind
    answers: tuple[AnswerLocation, ...]


@dataclass(frozen=True)
class QuestionResult:
    """Where the first correct hit landed for one question — ranks, so any recall@k falls out of one scan.

    `file_rank` is the rank of the first hit in a correct file; `line_rank` the first whose
    lines also overlap the answer. A question with a file_rank but no line_rank found the
    right file and cited the wrong line.
    """

    question: EvalQuestion
    file_rank: int | None
    line_rank: int | None


def load_questions(path: Path = _QUESTIONS_PATH) -> list[EvalQuestion]:
    """Parse questions.yaml, failing loudly on an unknown repo key or question kind."""
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a top-level list of questions")

    questions: list[EvalQuestion] = []
    for i, item in enumerate(raw, start=1):
        repo = item["repo"]
        if repo not in EVAL_REPOS_BY_KEY:
            raise ValueError(f"question {i}: unknown repo {repo!r} (see eval/repos.py)")
        answers = tuple(
            AnswerLocation(path=a["path"], start_line=a["lines"][0], end_line=a["lines"][1])
            for a in item["answers"]
        )
        if not answers:
            raise ValueError(f"question {i}: needs at least one answer location")
        questions.append(
            EvalQuestion(
                query=item["q"],
                repo=repo,
                kind=QuestionKind(item["kind"]),
                answers=answers,
            )
        )
    return questions


def validate_answers(db: Session, questions: list[EvalQuestion]) -> list[str]:
    """Check every answer path exists in the seeded repo; returns human-readable problems.

    A typo'd path is unhittable by construction, so it drags recall down for a reason that
    has nothing to do with retrieval quality. Catching it here keeps the scoreboard honest.
    """
    problems: list[str] = []
    by_repo: dict[str, list[EvalQuestion]] = defaultdict(list)
    for question in questions:
        by_repo[question.repo].append(question)

    for repo_key, repo_questions in by_repo.items():
        repo_id = eval_id(repo_key)
        paths = set(db.scalars(select(File.path).where(File.repository_id == repo_id)).all())
        if not paths:
            problems.append(f"{repo_key}: not seeded (run `python -m eval.seed`)")
            continue
        for question in repo_questions:
            for answer in question.answers:
                if answer.path not in paths:
                    problems.append(f"{repo_key}: no such file {answer.path!r} ({question.query})")
    return problems


def evaluate_question(
    db: Session,
    question: EvalQuestion,
    repo_id: uuid.UUID,
    legs: tuple[RetrieverSource, ...] | None = None,
) -> QuestionResult:
    """Run one question through core.retrieval and record where the first correct hit ranked."""
    hits = search(db, repo_id, question.query, limit=max(_KS), legs=legs)

    file_rank: int | None = None
    line_rank: int | None = None
    for rank, hit in enumerate(hits, start=1):
        for answer in question.answers:
            if hit.path != answer.path:
                continue
            if file_rank is None:
                file_rank = rank
            # Overlap, not containment: a one-line lexical hit anywhere inside the answer's
            # enclosing definition still counts as "found it".
            overlaps = hit.start_line <= answer.end_line and hit.end_line >= answer.start_line
            if overlaps and line_rank is None:
                line_rank = rank
        if line_rank is not None:
            break  # ranks only grow — the first line-level match is the best one
    return QuestionResult(question=question, file_rank=file_rank, line_rank=line_rank)


def _recall(results: list[QuestionResult], k: int, *, line_level: bool) -> float:
    """Share of questions whose first correct hit landed at rank k or better."""
    if not results:
        return 0.0
    found = sum(
        1
        for r in results
        if (rank := (r.line_rank if line_level else r.file_rank)) is not None and rank <= k
    )
    return found / len(results)


def _format_row(label: str, results: list[QuestionResult]) -> str:
    """One scoreboard line: line-level recall at each k, with file-level recall in parentheses."""
    line = "  ".join(f"@{k} {_recall(results, k, line_level=True):.2f}" for k in _KS)
    file = "  ".join(f"@{k} {_recall(results, k, line_level=False):.2f}" for k in _KS)
    return f"  {label:<12} n={len(results):<3}  {line}   (file-level: {file})"


def report(results: list[QuestionResult]) -> None:
    """Print the scoreboard: overall recall, the by-kind and by-repo breakdowns, then every miss."""
    print("\nrecall (line-level = right file AND overlapping lines)")
    print(_format_row("overall", results))

    print("\nby kind")
    for kind in QuestionKind:
        subset = [r for r in results if r.question.kind is kind]
        if subset:
            print(_format_row(kind.value, subset))

    print("\nby repo")
    by_repo: dict[str, list[QuestionResult]] = defaultdict(list)
    for result in results:
        by_repo[result.question.repo].append(result)
    for repo_key, subset in sorted(by_repo.items()):
        print(_format_row(repo_key, subset))

    # The miss list is the working input to whichever leg builds next (M7's graph leg) —
    # these are the questions it has to fix to justify the cost.
    misses = [r for r in results if r.line_rank is None or r.line_rank > max(_KS)]
    print(f"\nmisses — no correct location in top {max(_KS)} ({len(misses)}/{len(results)})")
    for result in misses:
        wrong_line = " [right file, wrong line]" if result.file_rank is not None else ""
        print(f"  [{result.question.kind.value}/{result.question.repo}] {result.question.query}{wrong_line}")


def run(
    questions_path: Path = _QUESTIONS_PATH,
    legs: tuple[RetrieverSource, ...] | None = None,
    repos: str | None = None,
) -> None:
    """Score the pinned questions for the selected repos (default: noetra) and print the scoreboard."""
    selected = {repo.key for repo in select_repos(repos)}
    # `kind: graph` questions are deliberately not scored here. They are answered by the
    # agent's graph tools walking call edges, never by search() — RRF has no graph leg and
    # is not getting one (docs/RETRIEVAL.md). Letting them in would drag this scoreboard
    # below its pinned 0.85/0.91 baseline for a reason that has nothing to do with
    # retrieval quality, and break comparability with every earlier run. They are scored
    # in eval/agent.py, on coverage.
    questions = [
        q
        for q in load_questions(questions_path)
        if q.repo in selected and q.kind is not QuestionKind.GRAPH
    ]
    db = SessionLocal()
    try:
        problems = validate_answers(db, questions)
        if problems:
            print("answer key problems — fix these before trusting the score:")
            for problem in problems:
                print(f"  {problem}")
            raise SystemExit(1)

        results = [evaluate_question(db, q, eval_id(q.repo), legs) for q in questions]
    finally:
        db.close()

    ran = [leg.value for leg in (legs if legs is not None else ALL_LEGS)]
    print(f"legs: {', '.join(ran)}")
    print(f"repos: {', '.join(sorted(selected))}")
    print("kinds: symbol, keyword, conceptual, docs (graph questions are scored by eval.agent)")
    report(results)


def main() -> None:
    """CLI entry point: `python -m eval.run [--questions PATH] [--legs lexical,semantic] [--repos noetra|all]`."""
    parser = argparse.ArgumentParser(description="Score retrieval against the pinned eval questions.")
    parser.add_argument("--questions", type=Path, default=_QUESTIONS_PATH, help="path to questions.yaml")
    parser.add_argument(
        "--legs", type=str, default=None, help="comma-separated legs to run, e.g. lexical or lexical,semantic (default: both)"
    )
    parser.add_argument(
        "--repos", type=str, default=None, help="comma-separated repo keys, or 'all' (default: noetra)"
    )
    args = parser.parse_args()
    run(args.questions, parse_legs(args.legs), args.repos)


if __name__ == "__main__":
    main()
