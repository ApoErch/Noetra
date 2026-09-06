from core.agent.repo_map import FileSymbols, pagerank, render_repo_map


def test_pagerank_ranks_the_most_imported_file_highest() -> None:
    """In a→c, b→c, c→d, the file everyone imports (c) outranks the leaves, and d inherits from c."""
    ranks = pagerank(["a", "b", "c", "d"], [("a", "c"), ("b", "c"), ("c", "d")])
    assert ranks["c"] > ranks["a"] == ranks["b"]
    assert ranks["d"] > ranks["a"]
    assert abs(sum(ranks.values()) - 1.0) < 1e-6


def test_pagerank_ignores_edges_to_unknown_nodes_and_self_loops() -> None:
    """Edges outside the node set and self-imports do not break or bias the ranking."""
    ranks = pagerank(["a", "b"], [("a", "zzz"), ("a", "a"), ("a", "b")])
    assert set(ranks) == {"a", "b"} and ranks["b"] > ranks["a"]


def test_render_orders_by_rank_and_lists_symbols() -> None:
    """Central files come first; each line is `path: symbols`."""
    files = [FileSymbols("leaf.py", ["main"]), FileSymbols("core.py", ["Engine", "run"])]
    out = render_repo_map(files, {"core.py": 0.7, "leaf.py": 0.3}, token_budget=1_000)
    assert out == "core.py: Engine, run\nleaf.py: main"


def test_render_respects_token_budget_and_reports_omissions() -> None:
    """When the budget is too small, the lowest-ranked files are dropped and counted."""
    files = [FileSymbols(f"f{i}.py", ["a", "b"]) for i in range(20)]
    out = render_repo_map(files, {}, token_budget=40)
    assert out.endswith("more files (use code_search to find them)")
    assert out.count("\n") < 20
