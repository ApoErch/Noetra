from core.ai import embeddings


def test_batches_split_under_request_cap(monkeypatch) -> None:
    """Texts are grouped consecutively so no request exceeds the per-request token cap."""
    monkeypatch.setattr(embeddings, "_truncate", lambda t: (t, len(t)))
    monkeypatch.setattr(embeddings, "_MAX_REQUEST_TOKENS", 10)
    batches = embeddings._batches(["aaaa", "bbbb", "cc", "dddddddd", "e"])
    assert batches == [["aaaa", "bbbb", "cc"], ["dddddddd", "e"]]


def test_single_oversized_text_still_gets_its_own_request(monkeypatch) -> None:
    """A text at the per-input cap is sent alone rather than dropped."""
    monkeypatch.setattr(embeddings, "_truncate", lambda t: (t, len(t)))
    monkeypatch.setattr(embeddings, "_MAX_REQUEST_TOKENS", 3)
    assert embeddings._batches(["aaaaa", "b"]) == [["aaaaa"], ["b"]]
