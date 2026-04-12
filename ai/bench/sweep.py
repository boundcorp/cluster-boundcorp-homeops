#!/usr/bin/env python3
"""
Benchmark sweep: test multiple models with multiple prompts on llama-server.
Manages starting/stopping the server for each model.

Usage: python3 sweep.py <machine-name> <llama-server-binary> <models-dir> [--port 8080]
Example: python3 sweep.py rigel /home/linked/p/boundcorp/llama.cpp/build/bin/llama-server ./rigel/models
"""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# Server config per machine type
SERVER_CONFIGS = {
    "rigel": {"n_gpu_layers": 99, "threads": 8, "ctx_size": 8192, "batch_size": 512},
    "octo":  {"n_gpu_layers": 0,  "threads": 12, "ctx_size": 8192, "batch_size": 512},
}

# Test matrix
MAX_TOKENS_BY_PROMPT = {
    "routing": 300,
    "mealtracker": 512,
}


def wait_for_server(port, timeout=120):
    """Wait for llama-server to be ready."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2)
            data = json.loads(resp.read())
            if data.get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def run_prompt(port, prompt_name, prompt_text, max_tokens):
    """Run a single prompt and return results."""
    payload = json.dumps({
        "model": "test",
        "messages": [{"role": "user", "content": prompt_text + "\n\n/no_think"}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }).encode()

    req = urllib.request.Request(
        f"http://localhost:{port}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    start = time.time()
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=300).read())
    except Exception as e:
        return {"prompt": prompt_name, "error": str(e)}
    elapsed = time.time() - start

    t = resp.get("timings", {})
    msg = resp["choices"][0]["message"]
    content = msg.get("content", "") or ""
    reasoning = msg.get("reasoning_content", "") or ""

    return {
        "prompt": prompt_name,
        "prompt_tokens": t.get("prompt_n", 0),
        "completion_tokens": t.get("predicted_n", 0),
        "prompt_tok_per_s": round(t.get("prompt_per_second", 0), 1),
        "gen_tok_per_s": round(t.get("predicted_per_second", 0), 1),
        "total_time_s": round(elapsed, 2),
        "reasoning_len": len(reasoning),
        "response_len": len(content),
        "response_preview": content[:300],
    }


def benchmark_model(machine, server_bin, model_path, port, extra_env=None):
    """Start server with a model, run all prompts, stop server."""
    model_name = os.path.basename(model_path)
    config = SERVER_CONFIGS.get(machine, SERVER_CONFIGS["octo"])

    print(f"\n{'='*60}")
    print(f"Model: {model_name}")
    print(f"{'='*60}")

    # Start server
    cmd = [
        server_bin,
        "--host", "0.0.0.0",
        "--port", str(port),
        "--model", model_path,
        "--ctx-size", str(config["ctx_size"]),
        "--n-gpu-layers", str(config["n_gpu_layers"]),
        "--threads", str(config["threads"]),
        "--batch-size", str(config["batch_size"]),
    ]

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    print(f"Starting server...")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )

    if not wait_for_server(port):
        print(f"  FAILED: Server didn't start within 120s")
        proc.terminate()
        proc.wait()
        stderr = proc.stderr.read().decode()[-500:]
        print(f"  stderr: {stderr}")
        return {"model": model_name, "error": "server_start_timeout", "stderr": stderr}

    # Get VRAM usage
    try:
        smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        vram = smi.stdout.strip()
    except Exception:
        vram = "n/a"

    print(f"  Server ready. VRAM: {vram}")

    # Run prompts
    results = []
    for prompt_file in sorted(os.listdir(PROMPTS_DIR)):
        if not prompt_file.endswith(".txt"):
            continue
        prompt_name = prompt_file.replace(".txt", "")
        prompt_text = open(os.path.join(PROMPTS_DIR, prompt_file)).read()
        max_tokens = MAX_TOKENS_BY_PROMPT.get(prompt_name, 512)

        print(f"  Running: {prompt_name} (max {max_tokens} tokens)...", end=" ", flush=True)
        result = run_prompt(port, prompt_name, prompt_text, max_tokens)
        if "error" in result:
            print(f"ERROR: {result['error']}")
        else:
            print(f"{result['gen_tok_per_s']} tok/s, {result['total_time_s']}s, {result['completion_tokens']} tokens")
        results.append(result)

    # Stop server
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    return {
        "model": model_name,
        "model_size_gb": round(os.path.getsize(model_path) / 1e9, 2),
        "vram": vram,
        "results": results,
    }


def main():
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <machine> <llama-server-bin> <models-dir> [--port PORT]")
        sys.exit(1)

    machine = sys.argv[1]
    server_bin = sys.argv[2]
    models_dir = sys.argv[3]
    port = 8090  # Use different port to avoid conflicts

    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    extra_env = {}
    if os.path.exists("/usr/local/cuda/lib64"):
        extra_env["LD_LIBRARY_PATH"] = f"/usr/local/cuda/lib64:{os.environ.get('LD_LIBRARY_PATH', '')}"

    # Find all GGUF models
    models = sorted([
        os.path.join(models_dir, f)
        for f in os.listdir(models_dir)
        if f.endswith(".gguf")
    ], key=os.path.getsize)

    print(f"Machine: {machine}")
    print(f"Server: {server_bin}")
    print(f"Models: {len(models)} found")
    print(f"Prompts: {[f.replace('.txt','') for f in sorted(os.listdir(PROMPTS_DIR)) if f.endswith('.txt')]}")

    # Kill any existing server on our port
    subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
    time.sleep(0.5)

    all_results = []
    for model_path in models:
        result = benchmark_model(machine, server_bin, model_path, port, extra_env)
        all_results.append(result)

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    outfile = os.path.join(RESULTS_DIR, f"{machine}-sweep-{timestamp}.json")
    with open(outfile, "w") as f:
        json.dump({
            "machine": machine,
            "timestamp": timestamp,
            "models": all_results,
        }, f, indent=2)

    # Print summary table
    print(f"\n{'='*80}")
    print(f"RESULTS SUMMARY — {machine}")
    print(f"{'='*80}")
    print(f"{'Model':<35} {'Size':>6} {'VRAM':>12} {'Prompt':>12} {'tok/s':>8} {'Time':>7} {'Tokens':>7}")
    print("-" * 80)
    for m in all_results:
        if "error" in m:
            print(f"{m['model']:<35} {'FAILED':>6}   {m.get('error','')}")
            continue
        for r in m["results"]:
            if "error" in r:
                print(f"{m['model']:<35} {m['model_size_gb']:>5}G {m.get('vram','?'):>12} {r['prompt']:>12} {'ERR':>8}")
            else:
                print(f"{m['model']:<35} {m['model_size_gb']:>5}G {m.get('vram','?'):>12} {r['prompt']:>12} {r['gen_tok_per_s']:>7.1f} {r['total_time_s']:>6.1f}s {r['completion_tokens']:>6}")
    print(f"\nResults saved to {outfile}")


if __name__ == "__main__":
    main()
