"""Agent-mediated eval: run the real chat agent on the pinned questions and score what it retrieved and cited.

Two location metrics, same answer key as `eval.run`:

- **retrieved** — some range the agent's tools returned this turn overlaps an answer range
  (did it ever *see* the answer).
- **cited** — a verified citation in the final answer overlaps an answer range (did it *use* it).

Both are "at least one location", which is the right question for a key with one location.
`kind: graph` keys list *every* correct location, so those are scored on **coverage**
(`rcov` / `ccov`): the share of the whole key reached and cited. Naming two of five call
sites is a wrong answer that the boolean records as a win.

Plus cost: tool calls, tokens, wall-clock, and how often the citation verifier stripped
something. Every question's record is appended to a JSONL file as it finishes, so an
interrupted run (API calls cost money) resumes instead of restarting.

    python -m eval.agent [--repos noetra|all] [--kinds symbol,keyword] [--tag baseline]
                         [--model gpt-5.4-mini] [--provider anthropic] [--budget 8] [--no-repo-map] [--limit N]
"""

import argparse
import json
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from core.ai.chat import get_chat_model
from core.config import get_settings
from core.db import SessionLocal
from core.models import Repository
from core.agent.runner import run_turn
from core.retrieval import RetrievalHit
from eval.repos import select_repos
from eval.run import AnswerLocation, EvalQuestion, QuestionKind, load_questions, validate_answers
from eval.seed import eval_id

_OUT_DIR = Path(__file__).parent / "out"


@dataclass(frozen=True)
class AgentRecord:
    """One question's outcome, as written to the JSONL checkpoint."""

    query: str
    repo: str
    kind: str
    retrieved: bool
    cited: bool
    tool_calls: int
    input_tokens: int
    output_tokens: int
    seconds: float
    stripped: int
    answer: str
    trace: list[str]
    # Share of the answer key's locations covered, not just "at least one". For the
    # single-location keys of the other four kinds this is exactly the boolean above; it
    # exists for `kind: graph`, where "found one of the five call sites" and "found all
    # five" are different answers and the boolean cannot tell them apart. Defaulted so
    # checkpoints written before this field still load (see _load_done).
    retrieved_coverage: float = 0.0
    cited_coverage: float = 0.0


def _covers(hit: RetrievalHit, answer: AnswerLocation) -> bool:
    """Whether one hit shares a path with an answer location and overlaps its lines."""
    return (
        hit.path == answer.path
        and hit.start_line <= answer.end_line
        and hit.end_line >= answer.start_line
    )


def _coverage(hits: list[RetrievalHit], answers: tuple[AnswerLocation, ...]) -> float:
    """Share of the answer key's locations that some hit reached (1.0 = the whole set)."""
    if not answers:
        return 0.0
    return sum(any(_covers(hit, a) for hit in hits) for a in answers) / len(answers)


def _overlaps(hits: list[RetrievalHit], answers: tuple[AnswerLocation, ...]) -> bool:
    """Whether any hit shares a path and overlapping lines with any answer location."""
    return any(_covers(hit, answer) for hit in hits for answer in answers)


def evaluate(question: EvalQuestion, budget: int | None, repo_map: bool, provider: str | None) -> AgentRecord:
    """Run the agent on one question against its seeded repo and score the turn."""
    db = SessionLocal()
    try:
        repo = db.get(Repository, eval_id(question.repo))
        assert repo is not None
        started = time.perf_counter()
        turn = run_turn(db, repo, question.query, provider=provider, budget=budget, repo_map=repo_map)
        seconds = time.perf_counter() - started
    finally:
        db.close()
    return AgentRecord(
        query=question.query,
        repo=question.repo,
        kind=question.kind.value,
        retrieved=_overlaps(turn.hits, question.answers),
        cited=_overlaps(turn.citations, question.answers),
        tool_calls=turn.tool_calls_made,
        input_tokens=turn.input_tokens,
        output_tokens=turn.output_tokens,
        seconds=round(seconds, 2),
        stripped=len(turn.stripped),
        answer=turn.answer,
        trace=[f"{t['name']} → {t['summary']}" for t in turn.tool_trace],
        retrieved_coverage=_coverage(turn.hits, question.answers),
        cited_coverage=_coverage(turn.citations, question.answers),
    )


def _load_done(path: Path) -> dict[tuple[str, str], AgentRecord]:
    """Records already in the checkpoint file, keyed by (repo, query)."""
    done: dict[tuple[str, str], AgentRecord] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                data = json.loads(line)
                # Checkpoints written before the coverage fields existed came from
                # single-location answer keys, where coverage IS the boolean — so this
                # backfill is exact, not an approximation, and old runs stay comparable.
                data.setdefault("retrieved_coverage", float(data["retrieved"]))
                data.setdefault("cited_coverage", float(data["cited"]))
                record = AgentRecord(**data)
                done[(record.repo, record.query)] = record
    return done


def _rate(records: list[AgentRecord], attr: str) -> float:
    """Share of records where the boolean `attr` is true."""
    return sum(1 for r in records if getattr(r, attr)) / len(records) if records else 0.0


def _mean(records: list[AgentRecord], attr: str) -> float:
    """Mean of a float `attr` across records — the coverage columns."""
    return sum(getattr(r, attr) for r in records) / len(records) if records else 0.0


def _row(label: str, records: list[AgentRecord]) -> str:
    """One scoreboard line: retrieved / cited rates plus mean cost."""
    n = len(records)
    calls = sum(r.tool_calls for r in records) / n if n else 0.0
    tokens = sum(r.input_tokens + r.output_tokens for r in records) / n if n else 0.0
    secs = sum(r.seconds for r in records) / n if n else 0.0
    stripped = sum(r.stripped for r in records)
    return (
        f"  {label:<12} n={n:<3} retrieved {_rate(records, 'retrieved'):.2f}  cited {_rate(records, 'cited'):.2f}"
        f"  rcov {_mean(records, 'retrieved_coverage'):.2f}  ccov {_mean(records, 'cited_coverage'):.2f}"
        f"   calls {calls:.1f}  tokens {tokens:,.0f}  {secs:.1f}s  stripped {stripped}"
    )


def report(records: list[AgentRecord]) -> None:
    """Print overall, by-kind and by-repo rows, then every question the agent did not cite correctly."""
    print("\nagent eval (retrieved = answer range seen by a tool; cited = verified citation overlaps it)")
    print("           (rcov/ccov = share of the WHOLE answer key reached/cited — the kind: graph metric)")
    print(_row("overall", records))
    print("\nby kind")
    for kind in QuestionKind:
        subset = [r for r in records if r.kind == kind.value]
        if subset:
            print(_row(kind.value, subset))
    print("\nby repo")
    by_repo: dict[str, list[AgentRecord]] = defaultdict(list)
    for record in records:
        by_repo[record.repo].append(record)
    for repo_key, subset in sorted(by_repo.items()):
        print(_row(repo_key, subset))
    misses = [r for r in records if not r.cited]
    print(f"\nnot cited ({len(misses)}/{len(records)})")
    for record in misses:
        why = "retrieved but not cited" if record.retrieved else "never retrieved"
        print(f"  [{record.kind}/{record.repo}] {record.query}  — {why}; {' · '.join(record.trace) or 'no tool calls'}")

    # A boolean "cited" hides the failure these questions exist to expose: an enumeration
    # answer that names two of five call sites scores cited=True and is still wrong.
    partial = [r for r in records if r.cited and r.cited_coverage < 1.0]
    if partial:
        print(f"\ncited but incomplete ({len(partial)})")
        for record in sorted(partial, key=lambda r: r.cited_coverage):
            print(
                f"  [{record.kind}/{record.repo}] ccov {record.cited_coverage:.2f}"
                f" (rcov {record.retrieved_coverage:.2f})  {record.query}"
            )


def main() -> None:
    """CLI entry point; see the module docstring for flags."""
    parser = argparse.ArgumentParser(description="Score the chat agent against the pinned eval questions.")
    parser.add_argument("--repos", type=str, default=None, help="comma-separated repo keys, or 'all' (default: noetra)")
    parser.add_argument("--kinds", type=str, default=None, help="comma-separated question kinds to run (default: all)")
    parser.add_argument("--tag", type=str, default="baseline", help="checkpoint name: eval/out/agent-<tag>.jsonl")
    parser.add_argument("--model", type=str, default=None, help="override OPENAI_CHAT_MODEL / ANTHROPIC_CHAT_MODEL")
    parser.add_argument("--provider", type=str, default=None, help="openai | anthropic (default: DEFAULT_CHAT_PROVIDER)")
    parser.add_argument("--budget", type=int, default=None, help="tool-call budget per question (default: AGENT_TOOL_BUDGET)")
    parser.add_argument("--no-repo-map", action="store_true", help="ablation: omit the repo map from the prompt")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N selected questions (smoke test)")
    parser.add_argument("--fresh", action="store_true", help="ignore and overwrite the checkpoint for this tag")
    args = parser.parse_args()

    if args.model:
        settings = get_settings()
        provider = args.provider or settings.default_chat_provider
        if provider == "anthropic":
            settings.anthropic_chat_model = args.model
        else:
            settings.openai_chat_model = args.model
        get_chat_model.cache_clear()

    selected = {repo.key for repo in select_repos(args.repos)}
    kinds = {QuestionKind(k.strip()) for k in args.kinds.split(",")} if args.kinds else set(QuestionKind)
    questions = [q for q in load_questions() if q.repo in selected and q.kind in kinds]
    if args.limit:
        questions = questions[: args.limit]

    db = SessionLocal()
    try:
        problems = validate_answers(db, questions)
    finally:
        db.close()
    if problems:
        print("answer key problems — fix these before trusting the score:")
        for problem in problems:
            print(f"  {problem}")
        raise SystemExit(1)

    _OUT_DIR.mkdir(exist_ok=True)
    out_path = _OUT_DIR / f"agent-{args.tag}.jsonl"
    if args.fresh and out_path.exists():
        out_path.unlink()
    done = _load_done(out_path)
    print(f"checkpoint: {out_path} ({len(done)} done)")

    records: list[AgentRecord] = []
    with out_path.open("a", encoding="utf-8") as out:
        for i, question in enumerate(questions, start=1):
            key = (question.repo, question.query)
            if key in done:
                records.append(done[key])
                continue
            try:
                record = evaluate(question, args.budget, not args.no_repo_map, args.provider)
            except Exception as exc:  # one provider error must not lose a whole paid run
                record = AgentRecord(
                    query=question.query, repo=question.repo, kind=question.kind.value,
                    retrieved=False, cited=False, tool_calls=0, input_tokens=0, output_tokens=0,
                    seconds=0.0, stripped=0, answer=f"ERROR: {exc}", trace=[],
                )
            out.write(json.dumps(asdict(record)) + "\n")
            out.flush()
            records.append(record)
            status = "cited" if record.cited else ("retrieved" if record.retrieved else "MISS")
            print(f"[{i}/{len(questions)}] {status:<9} calls={record.tool_calls} {record.seconds}s  {question.query}")

    report(records)


if __name__ == "__main__":
    main()
