# On-Prem Inference Plan

## Status: Phase 0 COMPLETE (2026-04-11)

**Decision:** Deploy **Qwen3 30B-A3B Q4_K_M** on **octo** as primary inference endpoint.
Keep Phi-4 Mini 3.8B on rigel as secondary/routing when GPU is idle. **No hardware spend.**

See `JOURNAL.md` for full results. 30B-A3B won both on intelligence (13/16) and CPU speed
(18 tok/s) thanks to MoE architecture. Phi-4 Mini was the surprise runner-up (12/16 at
only 3.8B params — best intelligence-per-byte by a wide margin).

## Phase 0: Benchmark Existing Hardware

**Goal:** Get concrete tok/s numbers on octo (CPU) and rigel (GPU) across a range of model sizes to understand what's viable before spending any money.

### Step 1: Set up llama-server via docker compose

- **Octo:** CPU-only llama-server container, mount model cache volume
- **Rigel:** GPU-accelerated llama-server with nvidia-container-toolkit, mount model cache volume
- Both expose OpenAI-compatible API on a local port

### Step 2: Download test models

Target a spread of sizes to find the performance cliff:

| Model | Params | Quantization | Disk Size | Use Case |
|-------|--------|-------------|-----------|----------|
| Qwen3 4B | 4B | Q4_K_M | ~2.5GB | Routing / classification |
| Phi-4 Mini | 3.8B | Q4_K_M | ~2.3GB | Routing / classification |
| Qwen3 8B | 8B | Q4_K_M | ~5GB | Light summarization |
| Qwen3 14B | 14B | Q4_K_M | ~8.5GB | Summarization / reviews |
| Qwen3 30B | 30B | Q4_K_M | ~18GB | Full workload (octo only, won't fit rigel VRAM) |

### Step 3: Create standardized test prompts

Two prompt types reflecting real usage:

1. **Routing prompt** (~200 tokens input, ~20 tokens output): Short question, model classifies complexity
2. **Mealtracker prompt** (~2000-4000 tokens input, ~200 tokens output): Context-stuffed with meal history, preferences, chat log — realistic summarization/extraction task

### Step 4: Run benchmarks

For each model on each machine, measure:
- **Time to first token (TTFT)** — how long before output starts
- **Tokens per second (tok/s)** — generation speed
- **Total response time** — end-to-end for the full response
- **RAM/VRAM usage** — how much headroom remains
- **Output quality** — does the response actually make sense? (subjective but critical — small models on CPU can produce garbage)

Record results in `bench/results/` as structured data for comparison.

### Step 5: Evaluate results

Key questions to answer:
- What's the largest model that hits <10s total response time on each machine?
- Is octo CPU fast enough for routing? For summarization?
- Does rigel's 3070 make a meaningful difference for 7-8B models vs octo CPU?
- At what model size does output quality become unacceptable?

## Phase 1: Decide Next Steps (after benchmarks)

Based on Phase 0 results, pick one of:

- **A) Existing hardware is sufficient** — deploy to octo k3s or homelab k8s, done
- **B) Bring iota online** — 128GB RAM, 40 cores, best CPU-only option, no spend required
- **C) Buy GPU hardware** — informed by benchmarks, knowing exactly what performance gap to fill:
  - Framework AI PC (~$3k, 128GB unified memory) — maps to Vitalik's AMD experience (~51 tok/s on 35B)
  - USB 5090 eGPU (~$3-5k) — plug into existing machine
  - 5090 build (~$3-5k for GPU alone) — maps to Vitalik's preferred setup (~90 tok/s on 35B)
  - Tinygrad Red v2 ($12k, 4x 9070) — most powerful but biggest investment
- **D) Hybrid** — small model on existing hardware for routing, paid API for heavy work (status quo but cheaper)
