#!/usr/bin/env python3
"""
Intelligence benchmark: run lm-evaluation-harness against GGUF models.

Uses llama-cpp-python's OpenAI-compatible server (not llama.cpp's C++ llama-server)
because llama-cpp-python properly returns per-token logprobs when echo=true, which
lm-eval requires for loglikelihood-based tasks (ARC, HellaSwag, TruthfulQA MC).

Tests ARC-Easy, HellaSwag, and TruthfulQA with a limited sample size for speed.

Usage: python3 eval.py <machine-name> <venv-python> <models-dir> [--limit 50] [--port 8094]
Example: python3 eval.py octo /data/ai/venv/bin/python /data/ai/models --limit 25
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# Per-machine server config (thread count, n_gpu_layers for GPU machines)
SERVER_CONFIGS = {
    "rigel": {"n_gpu_layers": 99, "n_threads": 8,  "n_ctx": 4096},
    "octo":  {"n_gpu_layers": 0,  "n_threads": 16, "n_ctx": 4096},
}

TASKS = "arc_easy,hellaswag,truthfulqa_mc2"

# GGUF filename -> HuggingFace tokenizer repo
TOKENIZERS = {
    "Qwen3-1.7B-Q8_0.gguf": "Qwen/Qwen3-1.7B",
    "Qwen3-4B-Q4_K_M.gguf": "Qwen/Qwen3-4B",
    "Qwen3-8B-Q4_K_M.gguf": "Qwen/Qwen3-8B",
    "Qwen3-14B-Q4_K_M.gguf": "Qwen/Qwen3-14B",
    "Qwen3-30B-A3B-Q4_K_M.gguf": "Qwen/Qwen3-30B-A3B",
    "Phi-4-mini-instruct-Q4_K_M.gguf": "microsoft/Phi-4-mini-instruct",
    "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf": "google/gemma-4-26B-A4B-it",
}


def wait_for_server(port, timeout=180):
    """llama-cpp-python's server uses /v1/models as the readiness endpoint."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = urllib.request.urlopen(f"http://localhost:{port}/v1/models", timeout=2)
            json.loads(resp.read())
            return True
        except Exception:
            pass
        time.sleep(1)
    return False


def eval_model(machine, venv_python, model_path, port, limit):
    """Start llama-cpp-python server, run lm_eval, stop server."""
    model_name = os.path.basename(model_path)
    config = SERVER_CONFIGS.get(machine, SERVER_CONFIGS["octo"])

    print(f"\n{'='*60}")
    print(f"Eval: {model_name} (limit={limit})")
    print(f"{'='*60}")

    # Skip models too large for VRAM on GPU machines
    model_size_gb = os.path.getsize(model_path) / 1e9
    if config["n_gpu_layers"] > 0 and model_size_gb > 7.5:
        print(f"  SKIPPED: {model_size_gb:.1f}GB model won't fit in 8GB VRAM")
        return {"model": model_name, "skipped": True, "reason": "too large for VRAM"}

    tokenizer = TOKENIZERS.get(model_name)
    if not tokenizer:
        print(f"  SKIPPED: no tokenizer mapping for {model_name}")
        return {"model": model_name, "skipped": True, "reason": "no tokenizer mapping"}

    # Start llama-cpp-python server
    cmd = [
        venv_python, "-m", "llama_cpp.server",
        "--model", model_path,
        "--host", "127.0.0.1",
        "--port", str(port),
        "--n_ctx", str(config["n_ctx"]),
        "--n_threads", str(config["n_threads"]),
        "--n_gpu_layers", str(config["n_gpu_layers"]),
    ]

    print(f"  Starting llama-cpp-python server...")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if not wait_for_server(port):
        print(f"  FAILED: Server didn't start")
        proc.terminate()
        proc.wait()
        stderr = proc.stderr.read().decode()[-500:]
        return {"model": model_name, "error": "server_start_timeout", "stderr": stderr}

    print(f"  Server ready. Running lm_eval...")

    eval_cmd = [
        venv_python, "-m", "lm_eval",
        "--model", "local-completions",
        "--model_args", f"model={model_name},base_url=http://localhost:{port}/v1/completions,num_concurrent=1,tokenized_requests=False,tokenizer={tokenizer}",
        "--tasks", TASKS,
        "--limit", str(limit),
        "--output_path", os.path.join(RESULTS_DIR, f"eval-{machine}-{model_name}"),
    ]

    print(f"  CMD: {' '.join(eval_cmd)}")

    try:
        eval_result = subprocess.run(
            eval_cmd, capture_output=True, text=True, timeout=3600
        )
        stdout = eval_result.stdout
        stderr = eval_result.stderr
        returncode = eval_result.returncode
    except subprocess.TimeoutExpired:
        stdout = ""
        stderr = "lm_eval timed out after 60 minutes"
        returncode = -1

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    if returncode != 0:
        print(f"  lm_eval failed (exit {returncode})")
        print(f"  stderr: {stderr[-500:]}")
        return {"model": model_name, "error": f"lm_eval exit {returncode}", "stderr": stderr[-500:]}

    print(f"  Done!")
    print(stdout[-1500:])

    return {
        "model": model_name,
        "model_size_gb": round(model_size_gb, 2),
        "limit": limit,
        "tasks": TASKS,
        "stdout": stdout[-3000:],
    }


def main():
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <machine> <venv-python> <models-dir> [--limit N] [--port PORT]")
        sys.exit(1)

    machine = sys.argv[1]
    venv_python = sys.argv[2]
    models_dir = sys.argv[3]
    limit = 50
    port = 8094

    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    models = sorted([
        os.path.join(models_dir, f)
        for f in os.listdir(models_dir)
        if f.endswith(".gguf")
    ], key=os.path.getsize)

    print(f"Machine: {machine}")
    print(f"Python: {venv_python}")
    print(f"Tasks: {TASKS}")
    print(f"Limit: {limit} samples per task")
    print(f"Models: {len(models)} found")

    # Kill anything on our port
    subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
    time.sleep(0.5)

    all_results = []
    for model_path in models:
        result = eval_model(machine, venv_python, model_path, port, limit)
        all_results.append(result)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    outfile = os.path.join(RESULTS_DIR, f"{machine}-eval-{timestamp}.json")
    with open(outfile, "w") as f:
        json.dump({"machine": machine, "timestamp": timestamp, "results": all_results}, f, indent=2)

    print(f"\nResults saved to {outfile}")


if __name__ == "__main__":
    main()
