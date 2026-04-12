# On-Prem AI Inference

Self-hosted LLM inference for routing, summarization, and lightweight AI tasks.
Heavy coding tasks stay on Anthropic (Claude); everything else runs locally.

## Goals

1. **Smart Routing (primary):** Classify incoming prompts by complexity, route to appropriate model tier (local small model, local large model, or Claude)
2. **Summarization & Reviews:** Handle simple summarizations, decision trees, and structured extraction locally
3. **Cost Reduction:** Offload high-volume, low-complexity work from paid API calls
4. **Privacy:** Keep personal data (meal logs, chat history, etc.) on local infrastructure

## Hardware Inventory

| Machine | CPU | RAM | GPU | Location | Notes |
|---------|-----|-----|-----|----------|-------|
| **Octo** | Xeon W-2145 8c/16t @ 3.7GHz | 125GB | None | Hetzner (ssh -p 2133 root@octo) | Always on, runs k3s |
| **Rigel** | i9-10900K 10c/20t | 32GB | RTX 3070 (8GB VRAM) | Local desktop | Gaming PC, intermittent use |
| **Iota** | ~40 Xeon cores | 128GB | None | Local (HP DL380) | Out of service, available |
| **ThinkCenters** (x4) | Intel (unknown gen) | 32GB | Intel iGPU | Local k8s cluster | Not suitable for inference |

## Stack

- **Inference server:** [llama.cpp](https://github.com/ggerganov/llama.cpp) via `llama-server`
- **Model format:** GGUF (quantized)
- **Model swapping:** [llama-swap](https://github.com/mostlygeek/llama-swap) (future)
- **API:** OpenAI-compatible endpoint from llama-server

## Reference

- [Vitalik's Secure LLM Setup (April 2026)](https://vitalik.eth.limo/general/2026/04/02/secure_llms.html) — practical experience with 5090, AMD unified memory, llama-server stack
- His key finding: "anything slower than 50 tok/sec feels too annoying to be worth it"
- Vitalik's benchmarks: 5090 = 90 tok/s on Qwen3.5:35B, AMD 128GB unified = 51 tok/s

## Directory Structure

```
ai/
  README.md              # this file
  PLAN.md                # benchmarking & evaluation plan
  bench/
    prompts/             # standardized test prompts
    rigel/
      docker-compose.yml # llama-server with nvidia GPU passthrough
    octo/
      docker-compose.yml # llama-server CPU-only
    results/             # benchmark outputs
```
