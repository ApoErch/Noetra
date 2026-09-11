# Model serving — self-hosted LLM as a Noetra provider

**Status: documented, not built.** Nothing in this file has been implemented. It is a plan to
return to, and a study path to work through first. Read on demand — it is not part of V1 and
does not affect the live app.

Noetra's production provider is **OpenAI and stays OpenAI**. This is a parallel learning
track: serve a small Qwen model on an RTX 4060 8GB laptop, plug it into the existing provider
seam, and measure it against the pinned agent-eval baseline. It never goes live, is never
publicly reachable, and does not touch the EC2/Terraform plan, which stays CPU-only.

## Why this repo suits the exercise

Four things that normally take weeks already exist here.

| Already built | Role in this track |
|---|---|
| `core/ai/chat.py::get_chat_model(provider)` → `BaseChatModel` | A local server is one `elif` branch. `langchain-openai` is already a dependency and `ChatOpenAI` takes `base_url`. |
| `eval/agent.py` with pinned answer keys, `retrieved`/`cited`/`rcov`/`ccov` | A real benchmark with a **pinned baseline** (cited 0.91, graph ccov 0.84). Most fine-tuning projects have no eval at all. |
| `eval/out/*.jsonl` — gpt-5.4-mini answers from M7/M8 | Teacher outputs, already collected and free to re-score. |
| `code_entity`, `reference_edge`, `dependency_edge` in Postgres | **Programmatic ground truth** → auto-labelled training questions at scale. Normally the hardest part of building an SFT set; the indexer already solved it. |
| Byte-stable prompt prefix (system → tool schemas → repo map) | Built for OpenAI's prompt cache; llama-server's slot KV reuse pays the same dividend locally. |

**Governing rule, borrowed from the project's own measure-then-buy discipline:** serve the
base model and measure it *before* fine-tuning anything. Fine-tune only the gap that survives
the free fixes, so the LoRA's effect is attributable.

## Decisions

1. **Code lives in `ml/` at repo root** — outside `backend/`, which is the Docker build
   context (`docker-compose.yml`: `build: ./backend`). Structural exclusion from the
   production image, not a `.dockerignore` discipline problem.
2. **Serving: `llama.cpp` (`llama-server`)** as the daily driver; **vLLM run once in WSL2**
   on a small model as a deliberate comparison exercise.
3. **Fine-tune target: format first, then behaviour** — three phases, each a different
   serving exercise, each with an attributable delta.
4. **Training compute: Kaggle free T4** (~30 GPU-h/week, 16 GB, guaranteed quota). The 4060
   stays free to serve. Train-remote / serve-local is also the ordinary production split.

## Why llama.cpp and not vLLM or Ollama

**Not vLLM (as the daily driver).** Its design bet is *many concurrent requests* —
PagedAttention plus continuous batching, paid for by pre-allocating a large KV block pool at
startup (`--gpu-memory-utilization`, default ~0.9). Noetra's eval is strictly serial: one
question at a time. On 8 GB you pay every cost (VRAM grab, slow start, WSL2 since vLLM is
officially Linux-only) and collect none of the benefit. **This is why it is Phase 2, not
Phase 1** — run it deliberately to see the mechanism, then justify the choice from your own
measurement rather than from a blog post.

**Not Ollama.** Three reasons, the third disqualifying for this project specifically:

1. **It hides what this track exists to teach.** Ollama auto-selects quantization, GPU layer
   offload and context; llama.cpp makes you set `-ngl`, `-c` and the quant yourself. That *is*
   study blocks S2/S3 below.
2. **Engine vs. product.** Ollama wraps llama.cpp's engine. Interviews ask about the engine.
3. **Silent context truncation through `/v1` — the disqualifier.** Ollama's default context
   scales with VRAM and is **4k below 24 GiB**; the 4060 has 8 GB. Noetra's prompt (system +
   4 tool schemas + 1,500-token repo map + history + tool results) is **10k+**, so ~60 % of
   every prompt would be silently dropped from the beginning — no error, no warning. Worse,
   `num_ctx` lives on Ollama's *native* `/api/chat`; the **OpenAI-compatible `/v1` endpoint
   has nowhere to put it**, and `/v1` is exactly what `ChatOpenAI(base_url=...)` speaks. The
   result would be a collapsed `cited` score misread as "small models can't do agentic RAG".
   Workable via a Modelfile that bakes in `num_ctx`, but a default that silently corrupts the
   benchmark is the wrong foundation for a measurement project.

**Concession:** `ollama run qwen3:4b` is a fine 20-minute smoke test to confirm the GPU works
and get a first round-trip. Switch to `llama-server` immediately after.

---

## How to use this document

**It is a curriculum, not a build script.** The phases are ordered so each one teaches a
concept by making you hit it, and nothing later depends on understanding everything earlier.
Do not read it all and then start; do Phase 0 + 1, then come back.

When you return, the first executable step is Phase 0 (~15 lines in `backend/`, no new
dependencies). The `ml/` directory does not exist yet.

## Phase -1 — Study path (~14–18 hours to solid interview basics)

Ordered deliberately: **S3 before S4.** Reading about continuous batching before you have
served one model serially is wasted — the problem it solves won't be real to you yet.

| Block | What | Hours |
|---|---|---|
| **S1** | Inference mechanics: prefill vs. decode as two different workloads; the KV cache and why it exists; why decode is memory-*bandwidth*-bound while prefill is compute-bound. **The highest-value concept — everything else hangs off it.** | 2 |
| **S2** | Memory math: VRAM = weights + KV cache + activations. Quantization levels and what Q4_K_M actually trades away. Learn to predict whether a model fits *before* downloading it. | 2 |
| **S3** | **Hands-on (Phase 1).** Install llama.cpp CUDA binaries, serve Qwen3-4B Q4_K_M, `curl` the OpenAI endpoint, watch VRAM in `nvidia-smi`. Then break it on purpose: raise `-c` until it OOMs, drop `-ngl` and watch it collapse to CPU speed. | 3–4 |
| **S4** | Throughput vs. latency: static vs. continuous (iteration-level) batching, PagedAttention, chunked prefill, prefix caching. TTFT vs. TPOT as separate SLOs. | 2–3 |
| **S5** | **Hands-on (Phase 2).** vLLM in WSL2 on a 1.5B model. Fire 8–16 concurrent requests, compare to serial. Observe the pre-allocated KV pool at startup. | 4–6 |
| **S6** | Write your own comparison note in `ml/serve/NOTES.md`, in your own words, with your own numbers. **This is the deliverable** — if you can't write it, you don't have it yet. | 1 |

**Fine-tuning basics (Phases 4–6) are a separate ~15–20 hours** and should not start until
S1–S6 are done. They rest on serving knowledge, not the other way round.

**Reading list, mapped to the blocks:**

- S1/S2 — [llama.cpp VRAM requirements](https://localllm.in/blog/llamacpp-vram-requirements-for-local-llms) · [Quantization levels explained](https://www.promptquorum.com/local-llms/llm-quantization-explained)
- S3 — [llama-server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) · [Function calling](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)
- S4 — [PagedAttention + continuous batching](https://www.runpod.io/articles/guides/vllm-pagedattention-continuous-batching) · [Serving optimization incl. chunked prefill](https://www.spheron.network/blog/llm-serving-optimization-continuous-batching-paged-attention/) · [KV cache reuse in llama-server](https://github.com/ggml-org/llama.cpp/discussions/13606)
- S5 — [vLLM docs](https://docs.vllm.ai/) · [Engine comparison](https://insiderllm.com/guides/llamacpp-vs-ollama-vs-vllm/)

**Is this shape standard practice?** Yes. *Serve a baseline → measure → fix at the serving
layer if you can → distill/fine-tune only the surviving gap → re-measure against the same
pinned benchmark* is the ordinary industry loop. The two unusual things here are that the
benchmark already exists, and that training labels can be generated from our own database
rather than hand-written.

---

## Phase 0 — The seam (backend, ~15 lines, zero new dependencies)

This is the entire integration. Four files.

**`backend/core/config.py`** — two fields beside the existing AI block:

```python
local_chat_base_url: str = "http://host.docker.internal:8080/v1"
local_chat_model: str = "qwen3-4b"   # llama-server reports whatever you pass; it is a label
```

**`backend/core/ai/chat.py`** — a third branch in `get_chat_model`, mirroring the existing two:

```python
if provider == "local":
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.local_chat_model,
        base_url=settings.local_chat_base_url,
        api_key=SecretStr("local"),   # llama-server ignores it; the client requires one
        temperature=0,                # the eval must be reproducible
    )
```

`get_chat_model` is `@lru_cache`d on the provider string alone — unchanged behaviour, and
`eval/agent.py` already calls `get_chat_model.cache_clear()` after mutating settings.

**`backend/eval/agent.py`** — two small changes:

- The `--model` override (currently `if provider == "anthropic" … else openai`) gains a
  `local` branch writing `settings.local_chat_model`.
- `AgentRecord` gains `model: str = ""` and `provider: str = ""`, with `_load_done()`
  backfilling them via `data.setdefault(...)`. **Follow the existing
  `retrieved_coverage`/`cited_coverage` precedent exactly** — `AgentRecord(**data)` is strict,
  so old checkpoints must keep loading. Today the only record of *which model produced a run*
  is the `--tag` in the filename; a side-by-side comparison needs it in the row.

**`.env.example`** — document `LOCAL_CHAT_BASE_URL` and `LOCAL_CHAT_MODEL`, noting they are
inert unless a run passes `--provider local`, and that `local` is dev-only.

**Not changed:** `backend/pyproject.toml`, `uv.lock`, `docker-compose.yml`, `core/agent/*`,
`api/*`. `stream_turn` already accepts `provider`, so the chat UI can be pointed at the local
model later with no further work.

**Networking.** The eval runs inside the `worker` container; `llama-server` runs on the
Windows host. Docker Desktop for Windows resolves `host.docker.internal` automatically. If it
ever fails, add to the `worker` service: `extra_hosts: ["host.docker.internal:host-gateway"]`.

---

## Phase 1 — Serve the base model and measure it

**`ml/serve/`** — launch scripts and a short notes file.

1. Install the prebuilt llama.cpp Windows CUDA binaries (`llama-b*-bin-win-cuda-*-x64.zip`).
   No compilation.
2. Pull **Qwen3-4B, Q4_K_M GGUF** (~2.5 GB). Fallback if the tool-call template misbehaves:
   **Qwen2.5-7B-Instruct Q4_K_M**, which llama.cpp's function-calling doc lists as having a
   *native* tool-call parser rather than the generic fallback.
3. Launch:

   ```bash
   llama-server -m qwen3-4b-q4_k_m.gguf --jinja -fa -ngl 99 -c 32768 \
                --host 0.0.0.0 --port 8080
   ```

   - `--jinja` is what enables OpenAI-style tool calling. **Without it the agent cannot work
     at all** — the whole loop is tool calls.
   - `--host 0.0.0.0` is required for the container to reach the host. Do **not** open the
     Windows firewall to the LAN; container-to-host is enough.
   - **Do not KV-quantize** (`-ctk q4_0` etc.) to save VRAM — llama.cpp's own docs warn it
     substantially degrades tool-calling. Reduce `-c` instead.
4. Smoke test before touching Noetra: `curl` `/v1/chat/completions` with a `tools` array and
   confirm a `tool_calls` response comes back. If it returns prose the template is wrong —
   fix it with `--chat-template-file` before going further.
5. Benchmark with `llama-bench` and record prefill and decode t/s in `ml/serve/NOTES.md`.
   **These are the interview numbers — measure them, don't quote someone else's.**
6. Run the eval:

   ```bash
   docker compose exec worker python -m eval.agent \
       --provider local --model qwen3-4b --tag local-base --repos all
   ```

   Expect roughly 10–25 s/question (vs ~4 s for gpt-5.4-mini) → a full 58-question run in
   15–25 minutes, at zero cost. Record the real number.

**Deliverable:** a `local-base` row next to the pinned `gpt-5.4-mini` row.

**The diagnostic that decides Phase 3+:** the **gap between `retrieved` and `cited`**. M7
measured gpt-4.1-mini at retrieved 0.87 / cited 0.59 — a 0.28 gap that was almost entirely
citation *format* (it wrote `lines 90-99` instead of `[path:a-b]`), not retrieval failure. If
that pattern reproduces, the model is finding the code and failing to write the citation, and
that is fixable without any training.

---

## Phase 2 — The vLLM comparison (the interview exercise)

Half a day, and it buys the ability to justify the architecture choice from measurement.

1. WSL2 + NVIDIA CUDA driver passthrough (vLLM is officially Linux-only; a community Windows
   fork exists, but WSL2 is the canonical path and the one worth knowing).
2. `vllm serve Qwen/Qwen2.5-1.5B-Instruct --max-model-len 8192 --gpu-memory-utilization 0.85`
   — deliberately a small model; the point is to observe the *mechanism*, not to beat
   llama.cpp.
3. Watch what it does that llama.cpp doesn't: the pre-allocated KV block pool at startup, and
   throughput under concurrent load (fire 8–16 requests at once and compare to serial).
4. Write the comparison in `ml/serve/NOTES.md` in terms of the memory math — *weights + KV
   cache + activations* — and which term binds at 8 GB vs at 80 GB.

**Concepts to be able to explain unprompted afterwards:** prefill vs. decode (compute-bound
vs. memory-bandwidth-bound) · TTFT vs. TPOT · PagedAttention (KV cache in fixed blocks,
killing fragmentation) · continuous / iteration-level batching · chunked prefill · prefix
caching · quantization levels and what Q4_K_M costs · why an OpenAI-compatible API is the
de-facto integration contract.

---

## Phase 3 — Constrained decoding (the free fix, before any training)

If Phase 1 shows `retrieved` ≫ `cited`, fix it at the **serving layer** first.

- Write a **GBNF grammar** constraining the answer to contain well-formed `[path:a-b]`
  citations, and pass it via llama-server's `grammar` field.
- Re-run the eval as `--tag local-grammar`.
- **State the known trade-off in the notes:** grammar-constrained decoding guarantees format
  but is measured to degrade task accuracy — it masks tokens the model wanted. If `cited`
  rises while `retrieved` falls, that is the trade-off showing up, and saying so is the point.

Whatever gap survives Phase 3 is the fine-tuning target. Nothing before this point is wasted
if the gap closes here — that would mean learning the serving stack and saving a training run,
which is the correct engineering outcome, not a disappointment.

---

## Phase 4 — Build the dataset

**`ml/data/`**

**Source A — synthesize auto-labelled questions from the indexed DB (the main source).**
Free, and the strongest asset here. Query the seeded eval repos' existing rows:

- `code_entity` → *"Where is `X` defined?"* — the answer is exactly that row's file and
  `start_line`/`end_line`.
- `reference_edge` → *"List every call site of `X`"* — the answer is the **complete** caller
  set, precisely the enumeration shape the M8 `graph` bucket measures.
- `dependency_edge` → *"What does `Y` import / what imports `Y`?"*

Target ~1,000–2,000 questions across the three seeded repos. Answer keys are generated, not
hand-written, so they are exact by construction.

**Source B — teacher trajectories via rejection sampling.** Run gpt-5.4-mini through
`run_turn` on the synthesized questions and keep **only** trajectories whose citations overlap
the known answer — reuse `_covers()` / `_coverage()` from `eval/agent.py` verbatim as the
verifier. This is textbook **rejection-sampling fine-tuning (RFT)**: train only on traces that
passed an external check.

> **Known limitation — check before planning around it.** The existing `eval/out/*.jsonl`
> stores `trace` as `f"{name} → {summary}"`, which is lossy. Enough for *answer-format* SFT,
> **not** for full multi-turn trajectory SFT, which needs verbatim tool arguments and results.
> For the behaviour phase the teacher must be re-run with full message capture (a small
> wrapper around `run_turn` dumping `state["messages"]`), not mined from the old files.

**Source C — public tool-calling sets as a format stabilizer, only if needed.**
`Salesforce/xlam-function-calling-60k`, `NousResearch/hermes-function-calling-v1`,
`glaiveai/glaive-function-calling-v2`. Mix in a small fraction *only* if the base model's
tool-call JSON is shaky. Noetra-specific behaviour must come from Sources A/B.

**Cost:** ~1 ¢/question on gpt-5.4-mini → **~$10–20 for 1,000–2,000 teacher trajectories**.
The only real spend in the whole track, and it is one-off.

**Integrity rule — non-negotiable.** The pinned eval questions (`eval/questions.yaml`) **must
never enter training**, and the synthesized set must be split by repo/entity so a train entity
cannot appear in a test question. Without this the final number is meaningless. Write the
exclusion as a hard assertion in the data script, not a comment.

---

## Phase 5 — Train

**`ml/train/`** — a Kaggle notebook plus its own `requirements.txt` (torch, unsloth, trl,
peft, datasets). **Nothing here goes into `backend/pyproject.toml`.**

- **QLoRA 4-bit on Qwen3-4B with Unsloth** — 4-bit NF4 base, 16-bit LoRA adapters, paged
  optimizer. Comfortably inside a 16 GB T4.
- Format the data in the model's own chat template (tool calls as the template expects them),
  not a generic Alpaca shape — a mismatched template is the most common silent failure.
- Start small: rank 16, ~2–3 epochs, and *read the loss curve before scaling anything*.
- Export with `save_pretrained_gguf(..., quantization_method="q4_k_m")`, and keep the raw
  adapter too — both artifacts are needed for Phase 6.

---

## Phase 6 — Serve the adapter and take the final measurement

Two ways to serve a LoRA, and running both is the last serving lesson:

1. **Merged** — `llama-export-lora` bakes the adapter into the base weights, producing one
   GGUF. Simple, fastest inference, one model per adapter.
2. **Hot-swapped** — `convert_lora_to_gguf.py` produces a standalone GGUF adapter that
   llama-server applies at runtime with a scale factor, switchable without reloading the base.
   This is the mechanism behind multi-adapter serving (one base, many tenants) — worth
   understanding even at n=1.

Then:

```bash
docker compose exec worker python -m eval.agent \
    --provider local --model qwen3-4b-ft --tag local-ft --repos all
```

**Final scoreboard — four rows, one table. This is the deliverable, and the interview artifact.**

| tag | retrieved | cited | rcov | ccov | calls/q | s/q | $/q |
|---|---|---|---|---|---|---|---|
| `gpt-5.4-mini` (pinned baseline) | 0.93 | 0.91 | — | 0.84 | 3.3 | 4.0 | ~$0.01 |
| `local-base` | | | | | | | $0 |
| `local-grammar` | | | | | | | $0 |
| `local-ft` | | | | | | | $0 |

---

## EC2 / Terraform non-interference — the proof, not the promise

1. **Build context.** `docker-compose.yml` declares `build: ./backend` for both `api` and
   `worker`. `ml/` sits at repo root, outside that context — it **cannot** enter the image.
2. **Dependencies.** Phase 0 adds no packages; `ChatOpenAI` comes from the already-installed
   `langchain-openai`. `backend/pyproject.toml` and `uv.lock` are untouched, so the image
   changes only by the ~15 edited lines. No torch, no CUDA, no model weights.
3. **Config.** The two new settings have defaults and are read **only** inside the
   `provider == "local"` branch. With `DEFAULT_CHAT_PROVIDER=openai` in SSM, nothing reads
   them.
4. **Terraform.** Per `DEPLOYMENT.md` the Terraform is not yet written; this plan adds no
   resource, no GPU instance type, no security-group rule. The deployment stays CPU-only.
5. **The one real failure mode** — someone sets `DEFAULT_CHAT_PROVIDER=local` in production
   and the API tries to reach `host.docker.internal`. Mitigated by the existing explicit
   `raise ValueError(f"unknown chat provider: {provider!r}")` and by keeping the prod `.env`
   explicit, plus the dev-only note in `.env.example`.

---

## Verification (when Phase 0 is eventually built)

- `cd backend && uv run pytest -q` — existing tests still pass (Phase 0 touches no tested
  logic).
- `uv run mypy core/ai/chat.py core/config.py eval/agent.py` — clean under `strict`.
- `uv run ruff check` — no new findings beyond the 3 pre-existing ones.
- **Old checkpoints still load:** `python -m eval.agent --tag m8-graph-on --limit 1` must read
  the existing JSONL without a `TypeError` after `AgentRecord` gains fields.
- **OpenAI path unaffected:** re-run `eval.agent --repos noetra` with no flags and confirm it
  still reports ~0.91 cited. This is the regression gate for the whole change.
- **Image unchanged:** `docker compose build api`, and confirm `uv.lock` is untouched in
  `git status`.
- **Local path works end to end:** `eval.agent --provider local --limit 3` returns non-error
  records with `tool_calls > 0` — proof that tool calling actually crossed the wire.

---

## Sources

**Serving**

- [llama.cpp function calling](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md) — flags, native parsers, the KV-quant warning
- [llama-server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [KV cache reuse with llama-server](https://github.com/ggml-org/llama.cpp/discussions/13606)
- [GBNF grammars](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)
- [vLLM PagedAttention / continuous batching](https://www.runpod.io/articles/guides/vllm-pagedattention-continuous-batching)
- [Serving optimization: continuous batching, chunked prefill](https://www.spheron.network/blog/llm-serving-optimization-continuous-batching-paged-attention/)
- [vLLM vs llama.cpp memory behaviour](https://insiderllm.com/guides/llamacpp-vs-ollama-vs-vllm/)
- [Constrained decoding guide](https://www.aidancooper.co.uk/constrained-decoding/) · [its accuracy cost (arXiv 2502.14969)](https://arxiv.org/html/2502.14969v1)
- Ollama truncation: [num_ctx fix](https://runaihome.com/blog/ollama-truncating-input-prompt-num-ctx-fix-2026/) · [silent truncation writeup](https://repofold.dev/blog/ollama-silently-truncates-your-prompts)

**Models**

- [Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B) · [GGUF builds](https://huggingface.co/bartowski/Qwen_Qwen3-4B-GGUF)
- [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) (262K context)

**Fine-tuning**

- [Unsloth Qwen3.5 fine-tuning guide](https://unsloth.ai/docs/models/qwen3.5/fine-tune)
- [Unsloth GGUF export](https://unsloth.ai/docs/basics/inference-and-deployment/saving-to-gguf)
- [QLoRA + function calling on a free T4](https://datahacker.rs/llm_log-015-fine-tuning-llms-teach-a-3b-model-to-call-functions-with-qlora-unsloth-on-free-colab-t4/)
- [LoRA → GGUF conversion](https://github.com/ggml-org/llama.cpp/blob/master/convert_lora_to_gguf.py)
- [Rejection-sampling fine-tuning (RFT)](https://www.emergentmind.com/topics/rejection-sampling-fine-tuning-rft-ad4c417c-416b-40b6-bf9a-4653b83ddcfb)
- [Step Rejection Fine-Tuning (arXiv 2605.10674)](https://arxiv.org/pdf/2605.10674) · [JetBrains writeup](https://blog.jetbrains.com/research/2026/06/step-rejection-fine-tuning/)

**Datasets**

- [xLAM function calling 60k](https://huggingface.co/datasets/Salesforce/xlam-function-calling-60k)
- [Hermes function calling v1](https://huggingface.co/datasets/NousResearch/hermes-function-calling-v1)
- [Glaive function calling v2](https://huggingface.co/datasets/glaiveai/glaive-function-calling-v2)
- [Fine-tuning on xLAM (HF cookbook)](https://huggingface.co/learn/cookbook/function_calling_fine_tuning_llms_on_xlam)

**Compute**

- [Kaggle vs Colab free-tier limits](https://www.spheron.network/blog/google-colab-alternatives-8-gpu-clouds-compared-2026/)
