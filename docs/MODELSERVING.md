# Model serving — self-hosted, fine-tuned LLM as a Noetra provider

**Status: planned, not built (2026-09-15).** A 3-day track. Read on demand. It is not part of
V1 and does not affect the live app.

Noetra's production provider is **OpenAI and stays OpenAI**. This track does four things:
- **Fine-tune** Qwen3-8B with **QLoRA** (Unsloth + Hugging Face) on a free **Kaggle T4**.
- **Serve** it with **vLLM** on an RTX 4060 8 GB laptop.
- **Plug it into** the existing provider seam.
- **Score it** against the pinned agent-eval baseline.

It never goes live and is never publicly reachable, and it doesn't touch the EC2/Terraform
plan, which stays CPU-only.

## Why this repo suits the exercise

| Already built | Role in this track |
|---|---|
| `core/ai/chat.py::get_chat_model(provider)` → `BaseChatModel` | A local server is one `elif`. `langchain-openai` is already a dependency and `ChatOpenAI` takes `base_url`; vLLM speaks the OpenAI API. |
| `eval/agent.py` with pinned answer keys, `retrieved`/`cited`/`rcov`/`ccov` | A real benchmark with a pinned baseline (cited 0.91, graph ccov 0.84). Most fine-tuning projects have no eval at all. |
| `code_entity`, `reference_edge`, `dependency_edge` in Postgres | **Programmatic ground truth** → auto-labelled training questions. Normally the hardest part of building a training set. |
| `_covers()` / `_coverage()` in `eval/agent.py` | A ready-made verifier for rejection sampling. |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **Code in `ml/` at repo root** | Outside `backend/`, the Docker build context: structurally excluded from the production image. |
| 2 | **vLLM** for serving (not llama.cpp, not Ollama) | See below. |
| 3 | **Qwen3-8B**, thinking mode **off** | Stronger tool use than 4B. Hybrid thinking must be disabled in training *and* serving, or `<think>` tokens eat the budget and the two diverge. |
| 4 | **QLoRA**, not LoRA | 16-bit LoRA of 8B needs ~16 GB for weights alone; QLoRA's 4-bit NF4 base is ~5.5 GB. |
| 5 | **Training on Kaggle T4** (free, 16 GB) | 8B QLoRA doesn't fit the 4060's 8 GB. Train-remote / serve-local is also the ordinary production split. |
| 6 | **Serve the same NF4 base that was trained against** (`unsloth/Qwen3-8B-bnb-4bit`) | The adapter learned corrections relative to those exact weights, so there's no train/serve mismatch. |

### Why vLLM

"Fewer settings than llama.cpp" is **not** the reason. The knobs are the same concepts under
different names:

| concept | llama.cpp | vLLM |
|---|---|---|
| context that fits | `-c` | `--max-model-len` |
| VRAM to use | `-ngl` | `--gpu-memory-utilization` |
| weight precision | GGUF quant file | `--quantization bitsandbytes` / AWQ |
| tool calling | `--jinja` | `--enable-auto-tool-choice --tool-call-parser hermes` |

**The real reasons:**
1. **HF-format parity.** It serves the base and the Unsloth adapter folder as they are, with no
   GGUF conversion.
2. **Native multi-LoRA.** `--enable-lora` loads the base once and picks the adapter by model
   name, so base and fine-tune run side by side on one server (the S-LoRA / Punica idea).
3. **It's the production-standard engine.**

**Costs, stated honestly:**
- It runs only on Linux, so WSL2.
- It has a slow cold start.
- It pre-allocates the KV-cache pool, so a wrong `--max-model-len` means it won't start at all.
- Its core strength (continuous batching across many users) goes unused on a serial eval.
  *Chosen for LoRA serving and HF-format parity, not throughput.*

**Not Ollama:** its default context is 4k below 24 GB of VRAM, and the OpenAI-compatible `/v1`
endpoint can't raise it. Noetra's 10k+ prompts would be silently truncated. vLLM, by contrast,
returns a 400 for an oversized prompt, so an overflow shows up as an error row in the eval
instead of a quietly wrong score.

### LoRA vs QLoRA in one paragraph

**LoRA** freezes the model and trains two small matrices per layer, `ΔW = B·A`. At d=4096 and
r=16, a 4096×4096 matrix (16.8M params) becomes 2×4096×16 = 131K trainable params.
**QLoRA** trains the same adapters over a base stored in **4-bit NF4**, de-quantized on the fly,
plus double quantization and paged optimizers. The QLoRA paper matched 16-bit fine-tuning
quality; the costs are ~20–30 % slower steps and a small quality loss in practice.
Analogy: the same sticky notes, on a compressed PDF of the textbook instead of the hardcover.
[LoRA](https://arxiv.org/abs/2106.09685) · [QLoRA](https://arxiv.org/abs/2305.14314)

## Hardware facts

**Kaggle T4 (Turing, 16 GB): training.**
- **Limits:** 30 GPU-h/week, 12 h max per session.
- **No bf16 and no FlashAttention 2.** Unsloth falls back to fp16 on its own. Don't force bf16.
- **Single GPU** under free Unsloth.
- **Checkpoint every ~100 steps** to `/kaggle/working`, since a session can end mid-run.

**RTX 4060 laptop (Ada, 8 GB): serving.** Qwen3-8B uses 36 layers, 8 KV heads, head_dim 128.

```
KV per token = 2 (K and V) × 36 layers × 8 heads × 128 dims × bytes
             = ~147 KB in fp16  ·  ~74 KB in fp8
weights ≈ 5.5–6 GB  →  ~1 GB left for KV  →  ~6k tokens fp16  ·  ~12k tokens fp8
```

- **Noetra's prompts run 10k+ tokens**, so **fp8 KV cache is required, and it's still marginal.**
- **vLLM's startup log is the real answer.** It prints `Available KV cache memory` and
  `Maximum concurrency for N tokens`. Record both in `ml/serve/NOTES.md` next to the estimate.

---

## Day 1 — seam, vLLM, base-model baseline

**1. The seam (backend, ~15 lines, no new dependencies).**

- `backend/core/config.py`:
  ```python
  local_chat_base_url: str = "http://host.docker.internal:8001/v1"   # 8001: 8000 is Noetra's api
  local_chat_model: str = "qwen3-8b"
  ```
- `backend/core/ai/chat.py`, a third branch:
  ```python
  if provider == "local":
      from langchain_openai import ChatOpenAI

      return ChatOpenAI(
          model=settings.local_chat_model,
          base_url=settings.local_chat_base_url,
          api_key=SecretStr("local"),   # vLLM ignores it; the client requires one
          temperature=0,                # the eval must be reproducible
          extra_body={"chat_template_kwargs": {"enable_thinking": False}},
      )
  ```
- `backend/eval/agent.py`: `--provider` accepts `local`, and the `--model` override gets a
  `local` branch. `AgentRecord` gains `model`/`provider`, backfilled in `_load_done()` via
  `setdefault`. **Follow the `retrieved_coverage` precedent exactly** so old checkpoints keep
  loading.
- `.env.example`: `LOCAL_CHAT_BASE_URL`, `LOCAL_CHAT_MODEL`, dev-only and inert unless a run
  passes `--provider local`.

**2. Serve (WSL2).** `ml/serve/serve.sh`:

```bash
vllm serve unsloth/Qwen3-8B-bnb-4bit --quantization bitsandbytes \
  --max-model-len 12288 --gpu-memory-utilization 0.92 \
  --kv-cache-dtype fp8 --enforce-eager --port 8001 \
  --served-model-name qwen3-8b \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --enable-lora --max-lora-rank 16
```

- `--kv-cache-dtype fp8` halves the KV cache. It's a mild quality trade-off, much milder than
  q4 KV, but still record it.
- `--enforce-eager` skips CUDA graphs, saving ~0.5 GB at the cost of slower decoding.
- `--enable-auto-tool-choice --tool-call-parser hermes` turns Qwen's tool-call output into
  OpenAI `tool_calls`. **Without it the agent can't work at all.**

**3. Smoke test.**
- `curl /v1/chat/completions` with a `tools` array must return `tool_calls` and no `<think>`.
- Then `eval.agent --provider local --limit 3` must show `tool_calls > 0`.
- **Check early:**
  - The container reaches WSL2 via `host.docker.internal`. If not, add
    `extra_hosts: ["host.docker.internal:host-gateway"]`.
  - bitsandbytes + LoRA load together.

**4. If the context doesn't fit**, try in this order:
- (a) `AGENT_REPO_MAP_TOKENS=800`, used for **both** training data and eval.
- (b) keep `--max-model-len` as is and report the 400 error rows as a measured limit.
- (c) as a last resort, run the eval on a short paid GPU session.

**5. Baseline.**

```bash
docker compose exec worker python -m eval.agent --provider local --tag local-base --repos all
```

**The diagnostic that matters: `retrieved − cited`.** gpt-4.1-mini scored 0.87 / 0.59 in M7,
and that gap was almost entirely citation *format*. A large gap here means the model finds
the code but can't write `[path:a-b]`, which is exactly what fine-tuning targets.

## Day 2 — dataset (local), train (Kaggle)

**6. `ml/data/synth.py`** generates questions from the seeded eval repos' DB rows. Target
~600–800:
- `code_entity` → *"Where is `X` defined?"* (answer: that row's file and lines)
- `reference_edge` → *"List every call site of `X`"* (answer: the complete caller set)
- `dependency_edge` → *"What does `Y` import / what imports `Y`?"*

**Integrity rule, non-negotiable:** no entity or path range from `eval/questions.yaml` may enter
training. Enforce it with a hard `assert` in the script, not a comment. Without it the final
number means nothing.

**7. `ml/data/teacher.py` does rejection-sampling fine-tuning (RFT).**
- Run gpt-5.4-mini through `run_turn` on the synthesized questions and dump the **full**
  `state["messages"]`. The old `eval/out/*.jsonl` traces are lossy (`name → summary`), so they
  can't be reused.
- Keep only trajectories whose citations pass `_covers()` / `_coverage()`.
- Cost ~$10, 1–2 h. Upload the JSONL as a **private Kaggle Dataset**.

**8. `ml/train/train.ipynb`** + `ml/train/requirements.txt` (unsloth, trl, peft, datasets).
**Never added to `backend/pyproject.toml`.**

| setting | value | why |
|---|---|---|
| base | `unsloth/Qwen3-8B-bnb-4bit`, `load_in_4bit=True` | the same NF4 weights vLLM serves |
| `max_seq_length` | 12288 | matches serving |
| LoRA | r=16, α=16, q/k/v/o + gate/up/down | standard starting point; read the loss curve before changing |
| chat template | Qwen3's, **with tools**, `enable_thinking=False` | a mismatched template is the most common silent failure |
| loss masking | `train_on_responses_only` | learn the assistant turns (tool calls + answer), never tool results or the repo map |
| batch | 1, grad accumulation 8, fp16 (auto on T4) | fits 16 GB with gradient checkpointing `"unsloth"` |
| checkpoints | `save_steps=100`, `resume_from_checkpoint=True` | survives a Kaggle session cutoff |
| epochs | 1–2, 10 % validation | **time the first 20 steps and extrapolate first**; past ~10 h → 1 epoch or fewer samples |

Output: the adapter folder (~170 MB of safetensors), downloaded to the laptop.

## Day 3 — serve the adapter, measure, write up

**9.** Restart vLLM with `--lora-modules noetra-ft=/path/to/adapter`. `qwen3-8b` (base) and
`noetra-ft` (fine-tuned) are now served **at the same time**, by model name.

**10.** Run the fine-tuned model through the eval:

```bash
docker compose exec worker python -m eval.agent --provider local --model noetra-ft --tag local-ft --repos all
```

**11.** One concurrency measurement: 8 simultaneous requests vs. serial. Expect a small gain,
because the fp8 KV pool leaves little room to batch. Explain that in `NOTES.md` in terms of the
memory math.

**12.** Replace this plan with what was actually built. Add a `CONCEPTS.md` entry for the vLLM
decision and the fp8 KV trade-off.

## Scoreboard — fill with measured numbers only

| tag | model | retrieved | cited | ccov | calls/q | s/q | errors | $/q |
|---|---|---|---|---|---|---|---|---|
| pinned baseline | gpt-5.4-mini | 0.93 | 0.91 | 0.84 | 3.3 | 4.0 | 0 | ~$0.01 |
| `local-base` | Qwen3-8B QLoRA base | | | | | | | $0 |
| `local-ft` | Qwen3-8B + `noetra-ft` | | | | | | | $0 |

## Cut to fit 3 days

- **llama.cpp comparison:** out.
- **GBNF / constrained decoding:** out. vLLM structured outputs are the later equivalent.
- **Public tool-calling datasets:** out.
- **GGUF export:** out.
- **Separate study blocks:** read while training and eval runs are going.

## Risks

- **8B may not fit Noetra's context on 8 GB, even with fp8 KV.** Report that as a measured limit.
- **A Kaggle session can end mid-run.** That's what the checkpoints are for.
- **The fine-tune may stay well below 0.91.** An honest measured result is still the deliverable.

## EC2 / Terraform non-interference

1. **Build context:** `ml/` is outside `./backend`, so it can't enter the image.
2. **Dependencies:** no new backend packages. `pyproject.toml` / `uv.lock` untouched.
3. **Config:** the two new settings are read only inside `provider == "local"`.
4. **Terraform:** no GPU instance, no new resource.
5. **The one failure mode:** `DEFAULT_CHAT_PROVIDER=local` in production would point at
   `host.docker.internal`. Mitigated by keeping the prod `.env` explicit and the dev-only note
   in `.env.example`.

## Verification

- `cd backend && uv run pytest -q`, `uv run mypy core/ai/chat.py core/config.py eval/agent.py`,
  and `uv run ruff check`: only the 3 known ruff findings.
- **Old checkpoints load:** `eval.agent --tag m8-graph-on --limit 1` runs with no `TypeError`.
- **OpenAI path unchanged:** `eval.agent --repos noetra` with no flags still gives ~0.91 cited.
- **Local path:** `--provider local --limit 3` returns `tool_calls > 0` and no `<think>`.
- **Integrity check:** inject one eval entity into `synth.py` and confirm the assertion fires.
- **Resume check:** stop the Kaggle notebook after the first checkpoint, restart, and confirm it
  continues rather than restarting from step 0.

## Sources

- vLLM: [LoRA adapters](https://docs.vllm.ai/en/latest/features/lora.html) · [tool calling](https://docs.vllm.ai/en/latest/features/tool_calling.html) · [quantization](https://docs.vllm.ai/en/latest/features/quantization/index.html) · [quantized KV cache](https://docs.vllm.ai/en/latest/features/quantization/quantized_kvcache.html)
- Unsloth: [docs](https://docs.unsloth.ai/) · [Qwen3 fine-tuning](https://docs.unsloth.ai/basics/qwen3-how-to-run-and-fine-tune)
- Hugging Face: [TRL SFTTrainer](https://huggingface.co/docs/trl/sft_trainer) · [PEFT LoRA](https://huggingface.co/docs/peft/conceptual_guides/lora) · [chat templates with tools](https://huggingface.co/docs/transformers/chat_templating)
- Models: [Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B)
- Papers: [LoRA](https://arxiv.org/abs/2106.09685) · [QLoRA](https://arxiv.org/abs/2305.14314) · [PagedAttention / vLLM](https://arxiv.org/abs/2309.06180) · [S-LoRA](https://arxiv.org/abs/2311.03285)
- Compute: [Kaggle GPU docs](https://www.kaggle.com/docs/efficient-gpu-usage)
