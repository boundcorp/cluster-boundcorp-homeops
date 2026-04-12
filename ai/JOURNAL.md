# On-Prem Inference Journal

## 2026-04-10 — Phase 0 Kickoff, Rigel Benchmarks

### Summary
First day of the on-prem inference project. Got llama.cpp building with CUDA on rigel,
ran a full sweep across 7 models to find the performance cliff. Confirmed the "underpowered
hardware = cliff edge" hypothesis the hard way. Installed lm-evaluation-harness for
intelligence benchmarking (not yet run).

### Hardware Inventory (confirmed)
| Machine | CPU | RAM | GPU | Status |
|---------|-----|-----|-----|--------|
| Rigel | i9-10900K 10c/20t | 32GB | **RTX 3070 Ti 8GB** (was guessed as 3070) | Gaming PC, testing |
| Octo | Xeon W-2145 8c/16t @ 3.7GHz | 125GB (54GB free) | None (ASPEED BMC) | Hetzner, SSH port 2133 |
| Iota | HP DL380 ~40 Xeon cores | 128GB | None | Out of service, available |
| ThinkCenters x4 | Intel (unknown) | 32GB | iGPU only | k8s cluster — not inference-capable |

Octo free RAM caveat: 70GB already used by existing services, 54GB free — enough for
models up to ~30B Q4 but leaves no headroom for other workloads.

**Octo disk layout (important!):** Root filesystem is only 460GB (156GB free), but there's
an 8.4TB ZFS pool at `/data` — use this for everything:
- `/data/ai/llama.cpp` — source + build
- `/data/ai/models` — GGUF files
- `/data/ai/bench` — scripts + prompts + results
- `/data/k8s-local-path` — used by k3s PVCs
- `/data/postgres` — postgres data
- `/data/backups` — backups

### Key Decisions

1. **llama.cpp over Ollama** — Based on Vitalik's April 2026 article
   (https://vitalik.eth.limo/general/2026/04/02/secure_llms.html). Ollama couldn't fit
   Qwen3.5:35B on a 5090 as efficiently as llama-server. More control over quantization
   and VRAM allocation.

2. **CUDA build over Vulkan/Docker** — Docker CUDA images from ghcr.io/ggerganov/llama.cpp
   are stale (b4719, ~11 months old, no Qwen3 support). Vulkan binary exists but is ~80-90%
   of CUDA perf. Built llama.cpp from source with CUDA 13.0 toolkit. Binary at:
   `/home/linked/p/boundcorp/llama.cpp/build/bin/llama-server`

3. **Docker Compose for benchmarks, abandon for host** — Started with compose but nvidia
   runtime setup + stale images was too much friction. Native binary is faster and simpler
   for a single-host benchmark run. Compose files preserved in bench/ but unused.

4. **Qwen3 family as primary test** — Open, no auth required, full size range (1.7B, 4B,
   8B, 14B, 30B-A3B MoE) from single publisher for clean comparison. Added Phi-4 Mini and
   Gemma 4 26B-A4B for cross-publisher comparison.

5. **Use `/no_think` suffix for Qwen3 prompts** — Qwen3 has aggressive "thinking" mode
   enabled by default that burns 1000+ tokens on reasoning before producing output.
   For short routing tasks this is wasteful.

### Rigel Benchmark Results

| Model | Type | File Size | VRAM Used | Mealtracker (80k→512) | Routing (~200→30) | Verdict |
|-------|------|-----------|-----------|----------------------|-------------------|---------|
| Qwen3 1.7B Q8 | Dense | 1.8GB | 4.4/8.2 GB | **173 tok/s** (1.6s) | **177 tok/s** (0.2s) | Instant |
| Phi-4 Mini 3.8B Q4 | Dense | 2.4GB | 5.3/8.2 GB | **138 tok/s** (3.9s) | **130 tok/s** (0.3s) | Fast, MS quality |
| Qwen3 4B Q4 | Dense | 2.5GB | 5.2/8.2 GB | **124 tok/s** (2.5s) | **131 tok/s** (0.3s) | Solid |
| Qwen3 8B Q4 | Dense | 4.7GB | 7.3/8.2 GB | **80 tok/s** (5.2s) | **88 tok/s** (0.4s) | **Best that fits** |
| Qwen3 14B Q4 | Dense | 8.4GB | 6.2 GB* | **0.3 tok/s** (timeout) | **0.3 tok/s** (99s) | **CLIFF** — partial offload |
| Gemma 4 26B-A4B Q4 | MoE | 16GB | 2.6 GB* | timeout | timeout | CPU-only, dead |
| Qwen3 30B-A3B Q4 | MoE | 18GB | 2.0 GB* | timeout | **0.5 tok/s** (75s) | CPU-only, dead |

*\*partial GPU offload or CPU-only — numbers reflect residual VRAM usage, not full model*

### Critical Finding: The Cliff Is Real

The original concern was validated definitively: **there is no graceful degradation**.
Models either fit in VRAM and run at 80-177 tok/s, or they don't and run at 0.3-0.5 tok/s.
That's a 200-500x performance cliff. Partial GPU offload doesn't help — having 20/41 layers
on GPU means every token has to cross the PCIe bus twice, which destroys throughput.

The 8B model is the practical ceiling for rigel. Everything below 8B is overkill for speed
(all well above Vitalik's 50 tok/s "annoying" threshold). The 8B sits at ~80 tok/s which
is still very comfortable.

### Failed Approaches / Gotchas

1. **Docker `ghcr.io/ggerganov/llama.cpp:server-cuda`** — Tag doesn't exist anymore, only
   stale `b4719` tag from 11mo ago which doesn't support Qwen3 architecture. Image lookups
   via ghcr API also returned empty for `server-cuda` tags. llama.cpp project appears to
   have abandoned regular Docker publishing.

2. **Docker Compose `deploy.resources.devices`** — Syntax for GPU passthrough requires
   Swarm mode or newer compose spec. Use `runtime: nvidia` with `NVIDIA_VISIBLE_DEVICES=all`
   env var instead for plain docker-compose.

3. **nvidia-container-toolkit missing** — `libnvidia-container-tools` was installed but
   `nvidia-container-runtime` binary was not. Separate package required, needs the
   nvidia-container-toolkit repo added. User installed manually.

4. **HuggingFace repo naming** — bartowski has multiple repo variants for Phi-4:
   - `bartowski/Phi-4-mini-instruct-GGUF` returns "Repository not found"
   - `bartowski/microsoft_Phi-4-mini-instruct-GGUF` exists but use `unsloth/Phi-4-mini-instruct-GGUF`
   - Always verify via `curl https://huggingface.co/api/models/OWNER/REPO` first

5. **HuggingFace filename case sensitivity** — `qwen3-8b-q4_k_m.gguf` returns "Entry not
   found" but `Qwen3-8B-Q4_K_M.gguf` works. The 15-byte download should be the canary —
   always check file size after download.

6. **Silent curl failures** — The Qwen3 30B-A3B download stopped at 2.4GB (expected 18.6GB)
   with no error. Use `curl -L --retry 3` and verify final size against HF API metadata.

7. **Qwen3 thinking mode** — Fills up `reasoning_content` field, leaving `content` empty
   if max_tokens is too small. Append `/no_think` to prompts to disable.

8. **`--n-gpu-layers 99`** — Tells llama.cpp to offload all layers to GPU. If model doesn't
   fit, it OOMs immediately instead of falling back. Need to specify a lower number for
   partial offload (e.g., `--n-gpu-layers 20` for Qwen3 14B on 3070 Ti), but partial offload
   is so slow it's not useful.

### Files Created

```
ai/
├── README.md           — Project overview, goals, hardware inventory, Vitalik reference
├── PLAN.md             — Phase 0 (benchmark) + Phase 1 (decide) plan
├── JOURNAL.md          — This file
└── bench/
    ├── README.md       — How to run sweep and eval scripts
    ├── sweep.py        — Speed benchmark, tests tok/s across all models
    ├── eval.py         — Intelligence benchmark using lm-evaluation-harness
    ├── bench.sh        — Original shell benchmark (broken JSON parsing, unused)
    ├── prompts/
    │   ├── routing.txt     — Short classification prompt (~200 tokens)
    │   └── mealtracker.txt — Context-stuffed realistic prompt (~2000 tokens)
    ├── rigel/
    │   ├── docker-compose.yml  — Unused, kept for reference
    │   └── models/             — GGUF files (gitignored)
    ├── octo/
    │   ├── docker-compose.yml  — Unused, for when we set up octo
    │   └── models/             — Empty
    └── results/
        └── rigel-sweep-20260410-113342.json  — First sweep results
```

### Outside the Repo

- **llama.cpp build:** `/home/linked/p/boundcorp/llama.cpp/` (cloned from
  https://github.com/ggerganov/llama.cpp, built with CUDA)
- **Binary:** `/home/linked/p/boundcorp/llama.cpp/build/bin/llama-server`
- **Requires:** `export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH` to run
- **CUDA:** 13.0 installed at `/usr/local/cuda`

### External References

- **Vitalik's Secure LLM setup (April 2026):**
  https://vitalik.eth.limo/general/2026/04/02/secure_llms.html
  - 5090: 90 tok/s on Qwen3.5 35B
  - AMD Ryzen AI Max Pro 128GB: 51-18 tok/s
  - "Anything slower than 50 tok/sec feels too annoying"
  - Uses `llama-server` + `llama-swap` for model management
  - Switched to NixOS for reproducibility

- **HN discussion on Gemma 4 + LM Studio + Claude Code:**
  https://news.ycombinator.com/item?id=47651540
  - M1 Max 64GB: Gemma 4 26B-A4B at ~40 tok/s via llama.cpp
  - Gemma 4 has **looping behavior in tool-calling/agentic tasks** — model quality issue
  - Framework Desktop 48GB unified RAM deemed "too slow for coding agents"
  - Ollama has `ollama launch claude --model gemma4:26b` mode wrapping local models as
    Claude Code compatible API — interesting for use case C (routing)
  - Tau2 benchmark: Gemma 4 68% vs Qwen alternatives 81%

### Octo Benchmark Results (Complete)

**All 7 models tested on octo (Xeon W-2145 8c/16t @ 3.7GHz, 125GB RAM, CPU-only)**

| Model | Type | Size | Mealtracker (512 tok) | Routing | Notes |
|-------|------|------|----------------------|---------|-------|
| Qwen3 1.7B Q8 | Dense | 1.8GB | **21.1 tok/s** (17.5s) | **21.9 tok/s** (2.2s) | Fastest dense |
| **Phi-4 Mini 3.8B Q4** | Dense | 2.4GB | **18.7 tok/s** (16.6s) | **22.2 tok/s** (2.9s) | Best dense/size ratio |
| Qwen3 4B Q4 | Dense | 2.5GB | 14.1 tok/s (38.9s) | 17.5 tok/s (3.4s) | |
| Qwen3 8B Q4 | Dense | 4.7GB | 9.1 tok/s (67.4s) | 10.2 tok/s (4.2s) | Mealtracker too slow |
| Qwen3 14B Q4 | Dense | 8.4GB | 6.1 tok/s (61.3s) | 6.8 tok/s (6.8s) | Dense scaling cliff |
| **Gemma 4 26B-A4B Q4** | MoE (4B act) | 16GB | **11.4 tok/s** (59.4s) | 12.6 tok/s (19.5s) | Routing looped (216 tok) |
| **Qwen3 30B-A3B Q4** | MoE (3B act) | 18GB | **18.2 tok/s** (20.3s) | **22.7 tok/s** (2.7s) | **🏆 BEST overall** |

### Critical Finding: MoE Breaks the Scaling Rules

Dense models on CPU scale roughly linearly in inverse proportion to parameter count:
- 1.7B → 21 tok/s
- 4B → 14 tok/s
- 8B → 9 tok/s
- 14B → 6 tok/s

But MoE models run at the speed of their **active** parameters, not total:
- Qwen3 30B-A3B (3B active) → 18 tok/s — faster than the dense 4B model
- Gemma 4 26B-A4B (4B active) → 11 tok/s — faster than the dense 8B model

**Qwen3 30B-A3B is the clear winner on octo.** It delivers:
- 30B-parameter knowledge (approximately Qwen3 14B or better in quality)
- At the speed of a 3-4B model (~18 tok/s)
- Under 21 seconds for mealtracker, ~3 seconds for routing
- Fits comfortably in 125GB RAM (~18GB used)

### Rigel vs Octo Comparison

| Use Case | Best on Rigel (GPU) | Best on Octo (CPU) | Winner |
|----------|---------------------|---------------------|--------|
| Routing (speed matters most) | Qwen3 1.7B @ 177 tok/s (0.2s) | Qwen3 30B-A3B @ 23 tok/s (2.7s) | **Rigel faster, octo smarter** |
| Mealtracker (balance) | Qwen3 8B @ 80 tok/s (5.2s) | Qwen3 30B-A3B @ 18 tok/s (20s) | **Rigel much faster, octo has bigger model** |
| Max quality achievable | Qwen3 8B (VRAM ceiling) | Qwen3 30B-A3B | **Octo wins on quality ceiling** |
| Availability | Intermittent (gaming) | 24/7 | **Octo** |

### Gemma 4 Gotcha: Looping

Gemma 4 26B-A4B's routing prompt generated **216 tokens** instead of the expected ~30.
This matches the HN thread finding that Gemma 4 has looping behavior in agentic/tool-calling
tasks. Will need to verify output quality — might be unusable for routing despite speed.

### Intelligence Benchmarking — Attempted lm-eval, Pivoted to Custom (2026-04-11)

**First tried lm-evaluation-harness** (ARC-Easy, HellaSwag, TruthfulQA). Three failed
approaches before pivoting:

1. **lm-eval → C++ llama-server (OpenAI compat endpoint)**: llama.cpp only returns
   logprobs for *generated* tokens, not context tokens. lm-eval's loglikelihood scoring
   needs per-token logprobs for every token in a candidate continuation. Wrote a
   llamacpp_proxy.py to translate the response format, but it couldn't help because the
   data simply isn't in the response. Result: all models got identical garbage scores.
2. **lm-eval → C++ llama-server with echo=true**: Still doesn't return per-context-token
   logprobs even with `echo: true` or `n_predict: 0`. Same identical-scores failure mode.
3. **lm-eval → llama-cpp-python's Python server**: API format was correct (proper
   `token_logprobs` array with text_offset, etc.) but llama-cpp-python is ~5-10x slower
   than the C++ server. At `--limit 3`, two small models took 80+ minutes. Not viable.

**Root cause:** llama.cpp's server (both OpenAI-compat and native endpoints) doesn't
expose per-context-token logprobs through any API. llama-cpp-python does but is
prohibitively slow without native optimization flags. vLLM would work but requires GPU
+ FP16 weights (not GGUF).

**Pivoted to custom eval** with Claude Haiku as judge. 16 hand-crafted tests across
4 categories. Faster AND more relevant to actual use cases than ARC-Easy scores.

### Custom Intelligence Eval Results (2026-04-11)

**Setup:**
- 16 tests total: 4 coding, 4 math/science, 4 logic, 4 tool-use
- Difficulty target: "senior CS undergrad / coding interview"
- Judged by `claude-haiku-4-5-20251001` via Anthropic API
- Hand-crafted tests in `ai/bench/custom_eval/tests.json`
- Harness in `ai/bench/custom_eval/eval.py`
- Rigel runs with `nice -n 19 ionice -c 3`, 4 threads, skip models >6GB for desktop safety
- Kill switch: `touch /tmp/stop-eval` to halt gracefully between tests

**Final Scores (higher is better, max 16):**

| Rank | Model | Score | Code | Math | Logic | Tool | Notes |
|------|-------|-------|------|------|-------|------|-------|
| 🥇 | **Qwen3 30B-A3B Q4** (octo) | **13/16** | 3/4 | 4/4 | **3/4** | 3/4 | **BEST** |
| 🥈 | Qwen3 14B Q4 (octo) | 12/16 | 3/4 | 4/4 | 2/4 | 3/4 | Good but slow |
| 🥈 | Phi-4 Mini 3.8B (rigel) | 12/16 | **4/4** | 4/4 | 1/4 | 3/4 | Best per-byte |
| 4 | Phi-4 Mini 3.8B (octo) | 11/16 | 3/4 | 2/4 | 3/4 | 3/4 | |
| 4 | Qwen3 8B Q4 (octo) | 11/16 | 3/4 | 4/4 | 1/4 | 3/4 | |
| 4 | Qwen3 8B Q4 (rigel) | 11/16 | 2/4 | 4/4 | 2/4 | 3/4 | |
| 7 | Qwen3 4B Q4 | 10-11/16 | 3/4 | 4/4 | 0-1/4 | 3/4 | |
| 7 | Qwen3 1.7B Q8 | 10/16 | 2/4 | 4/4 | 1/4 | 3/4 | |
| ❌ | Gemma 4 26B-A4B | **6/16** | 1/4 | 1/4 | 1/4 | 3/4 | Truncation bug |

### Key Findings

1. **Qwen3 30B-A3B is the clear winner** — highest intelligence score AND fastest CPU
   inference (18 tok/s) thanks to MoE. Quality + speed in one package.

2. **Phi-4 Mini 3.8B punches way above its weight** — 12/16 at only 3.8B params, beating
   Qwen3 4B and 8B, matching 14B. On rigel it hit perfect 4/4 on coding. Best
   intelligence-per-byte by a wide margin.

3. **Rigel vs Octo same model ≈ ±1 test** — quality is model-dependent, not hardware.
   Pick the right model, don't worry about GPU vs CPU for quality (only for speed).

4. **Gemma 4's 6/16 is not real** — failure reasons overwhelmingly say "response cuts off
   mid-calculation" or "incomplete — cuts off mid-comment". Gemma 4 is verbose and our
   `max_tokens=1024` limit truncates it before completion. Needs retest with
   `max_tokens=4096` to get a fair reading. Also matches HN thread reports of Gemma 4
   having looping behavior in agentic tasks.

5. **Math is universally strong** — nearly every model scored 4/4 on math/science
   (compound interest, projectile physics, probability without replacement, calculus
   max optimization). Modern local models solve these reliably. Only exception: Gemma 4
   (truncation).

6. **Logic is universally weak** — most models score 0-2/4 on knights-knaves, river
   crossing, 12-coin weighing, constraint puzzles. Qwen3 30B-A3B is the ONLY model to
   score 3/4 on logic. Scale genuinely helps for deductive reasoning.

7. **Tool use is uniformly 3/4** — every model fails the same test (`tool-03-chaining`),
   specifically hardcoding the result of a first tool call instead of referencing it as
   a variable. This is a cross-model limitation in multi-step tool orchestration. All
   models pass the simple, restraint, and ambiguity tool tests.

### Phase 1 Decision: RECOMMENDED PATH

**Deploy Qwen3 30B-A3B Q4_K_M on octo as the primary inference endpoint.**

Rationale:
- Best quality score (13/16, 81%)
- Best CPU inference speed (18 tok/s via MoE — only 3B active params per token)
- 24/7 availability on always-on Hetzner machine
- Fits in octo's 54GB free RAM with headroom
- **Zero hardware spend**

**Secondary: Phi-4 Mini on rigel** for on-demand routing/short tasks when rigel is idle
(overnight batch, weekends). Best-in-class speed (138 tok/s) with surprisingly strong
quality (12/16).

**Do NOT buy hardware yet.** The original hardware options ($3-12k for Framework AI PC,
5090 build, USB eGPU, or tinygrad box) are not justified by the current results. The
existing infrastructure handles the target use cases well. Revisit if:
- Volume grows to case #2 (tens of req/min) and octo CPU saturates
- A specific use case emerges where 13/16 isn't enough
- Qwen3 30B-A3B quality degrades in real workloads vs bench numbers

### Next Steps

1. **Retry Gemma 4 with max_tokens=4096** to get a fair score (currently invalid)
2. **Deploy Qwen3 30B-A3B as persistent service** — systemd unit or k8s deployment in
   octo's k3s cluster, expose OpenAI-compat endpoint on LAN
3. **Write Smart Routing shim** — small service that receives prompts, classifies
   complexity via Phi-4 Mini (rigel, when idle) or Qwen3 30B-A3B (octo), routes to local
   for simple/medium or Claude API for complex
4. **Mealtracker integration** — first real use case using the local endpoint
5. **Monitor and revisit** after 2-4 weeks of real usage

### User Preferences Observed

- Lee likes the brainstorming + plan-first approach but wants to jump to execution quickly
  once alignment is clear
- Prefers native Docker Compose over k8s for throwaway benchmarks
- Avoid using `git add -A` (CLAUDE.md memory) — repo has uncommitted backups that shouldn't
  be committed
- GPG signing is broken — use `git -c commit.gpgsign=false commit`
- Wants TDD/structured approach but tolerates ad-hoc iteration during exploration phase
- Cares about general intelligence over task-specific eval for initial model selection
- Desktop safety matters: nice/ionice/thread limiting important when running on rigel
  while user is gaming/working

### User Preferences Observed

- Lee likes the brainstorming + plan-first approach but wants to jump to execution quickly
  once alignment is clear
- Prefers native Docker Compose over k8s for throwaway benchmarks
- Avoid using `git add -A` (CLAUDE.md memory) — repo has uncommitted backups that shouldn't
  be committed
- GPG signing is broken — use `git -c commit.gpgsign=false commit`
- Wants TDD/structured approach but tolerates ad-hoc iteration during exploration phase
