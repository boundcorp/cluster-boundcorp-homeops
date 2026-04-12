# Benchmarking

## Quick Start

### Speed benchmark (sweep.py)
Tests tok/s and response time across all models for each prompt.

```bash
# On rigel (GPU)
python3 sweep.py rigel /home/linked/p/boundcorp/llama.cpp/build/bin/llama-server ./rigel/models

# On octo (CPU)
python3 sweep.py octo <path-to-llama-server> ./octo/models
```

### Intelligence benchmark (eval.py)
Tests model accuracy on ARC-Easy, HellaSwag, and TruthfulQA using lm-evaluation-harness.

```bash
# 50 samples per task (~5min per model on GPU)
python3 eval.py rigel /home/linked/p/boundcorp/llama.cpp/build/bin/llama-server ./rigel/models

# Increase sample size for more accurate scores
python3 eval.py rigel /path/to/llama-server ./rigel/models --limit 200
```

### Individual model test
```bash
# Start server manually
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
/home/linked/p/boundcorp/llama.cpp/build/bin/llama-server \
  --host 0.0.0.0 --port 8080 \
  --model ./rigel/models/Qwen3-8B-Q4_K_M.gguf \
  --ctx-size 8192 --n-gpu-layers 99 --threads 8

# Test it
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"test","messages":[{"role":"user","content":"Hello!"}],"max_tokens":100}'
```

## Models Directory
GGUF models go in `<machine>/models/`. These are gitignored (multi-GB files).

Download from HuggingFace:
```bash
curl -L -o models/ModelName.gguf "https://huggingface.co/<org>/<repo>/resolve/main/<filename>.gguf"
```
