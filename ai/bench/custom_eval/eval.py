#!/usr/bin/env python3
"""
Custom general-intelligence eval for local GGUF models.

Tests coding, math/science, logic, and tool-use problems.
Uses llama.cpp server (fast C++ one) for inference.
Uses Claude Haiku API to judge responses as PASS/FAIL.

Usage: python3 eval.py <machine> <llama-server-bin> <models-dir> [--limit N] [--models X,Y,Z]
Example: python3 eval.py octo /data/ai/llama.cpp/build/bin/llama-server /data/ai/models
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH_DIR = os.path.dirname(HERE)
RESULTS_DIR = os.path.join(HERE, "results")

SERVER_CONFIGS = {
    # rigel-safe: reduced threads + skip large models, for running while user is on desktop
    "rigel":      {"n_gpu_layers": 99, "threads": 4,  "ctx_size": 4096, "max_size_gb": 6.0},
    "rigel-full": {"n_gpu_layers": 99, "threads": 8,  "ctx_size": 4096, "max_size_gb": 7.5},
    "octo":       {"n_gpu_layers": 0,  "threads": 16, "ctx_size": 4096, "max_size_gb": 999},
}

STOP_FILE = "/tmp/stop-eval"

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
JUDGE_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS_RESPONSE = 1024


def wait_for_server(port, timeout=180):
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2)
            return True
        except Exception:
            pass
        time.sleep(1)
    return False


def call_model(port, prompt, max_tokens=MAX_TOKENS_RESPONSE):
    """Call llama.cpp server via chat completions."""
    # Append /no_think to disable Qwen3 thinking mode for direct answers
    payload = json.dumps({
        "model": "local",
        "messages": [{"role": "user", "content": prompt + "\n\n/no_think"}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        f"http://localhost:{port}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    start = time.time()
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=600).read())
    except Exception as e:
        return None, 0, str(e)
    elapsed = time.time() - start
    msg = resp["choices"][0]["message"]
    content = msg.get("content", "") or ""
    reasoning = msg.get("reasoning_content", "") or ""
    # Some models put the actual answer in reasoning_content if thinking mode still active
    if not content and reasoning:
        content = reasoning
    return content, elapsed, None


def call_judge(test, response, api_key):
    """Ask Claude Haiku to judge if the response is correct."""
    judge_prompt = f"""You are grading a test question. Evaluate whether the model's answer is correct.

QUESTION ({test['category']}):
{test['prompt']}

EXPECTED ANSWER / CORRECT APPROACH:
{test['expected']}

GRADING HINTS:
{test['judge_hint']}

MODEL'S RESPONSE:
{response}

Grade the response as PASS or FAIL. A response is PASS if:
- It arrives at the correct answer (even if the reasoning varies)
- For code: the logic is correct and would produce correct output
- For multi-step problems: the final answer is correct
- Partial credit is NOT awarded — it's pass or fail

Respond with ONLY one word on the first line: PASS or FAIL
Then on a second line, one sentence explaining why.
"""

    payload = json.dumps({
        "model": JUDGE_MODEL,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": judge_prompt}],
    }).encode()

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        return None, f"API error: {e.code} {e.read().decode()[:200]}"
    except Exception as e:
        return None, f"Request error: {e}"

    text = resp["content"][0]["text"].strip()
    lines = text.split("\n", 1)
    verdict = lines[0].strip().upper()
    reason = lines[1].strip() if len(lines) > 1 else ""

    if "PASS" in verdict:
        return True, reason
    elif "FAIL" in verdict:
        return False, reason
    else:
        return None, f"Unparseable verdict: {text[:200]}"


def run_eval(machine, server_bin, model_path, tests, api_key, port=8096, extra_env=None):
    """Start server, run all tests, judge each, return results."""
    model_name = os.path.basename(model_path)
    config = SERVER_CONFIGS.get(machine, SERVER_CONFIGS["octo"])

    print(f"\n{'='*70}")
    print(f"Model: {model_name}")
    print(f"{'='*70}")

    model_size_gb = os.path.getsize(model_path) / 1e9
    max_size = config.get("max_size_gb", 7.5)
    if config["n_gpu_layers"] > 0 and model_size_gb > max_size:
        print(f"  SKIPPED: {model_size_gb:.1f}GB > {max_size}GB limit (avoids VRAM pressure)")
        return {"model": model_name, "skipped": True}

    cmd = [
        server_bin,
        "--host", "127.0.0.1",
        "--port", str(port),
        "--model", model_path,
        "--ctx-size", str(config["ctx_size"]),
        "--n-gpu-layers", str(config["n_gpu_layers"]),
        "--threads", str(config["threads"]),
        "--batch-size", "512",
    ]

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    print(f"  Starting llama-server...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    if not wait_for_server(port):
        print(f"  FAILED: server did not start")
        proc.terminate()
        proc.wait()
        return {"model": model_name, "error": "server_start_timeout"}

    print(f"  Server ready. Running {len(tests)} tests...")

    results = []
    for i, test in enumerate(tests, 1):
        # Kill switch: touch /tmp/stop-eval to halt gracefully between tests
        if os.path.exists(STOP_FILE):
            print(f"  STOPPED: {STOP_FILE} detected, halting")
            break
        print(f"  [{i}/{len(tests)}] {test['id']} ({test['category']})...", end=" ", flush=True)
        response, elapsed, err = call_model(port, test["prompt"])
        if err:
            print(f"ERROR: {err}")
            results.append({
                "id": test["id"], "category": test["category"],
                "error": err, "elapsed": elapsed,
            })
            continue

        verdict, reason = call_judge(test, response, api_key)
        status = "PASS" if verdict else ("FAIL" if verdict is False else "UNCLEAR")
        print(f"{status} ({elapsed:.1f}s)")
        if verdict is False:
            print(f"      reason: {reason[:120]}")

        results.append({
            "id": test["id"],
            "category": test["category"],
            "elapsed": round(elapsed, 2),
            "verdict": verdict,
            "judge_reason": reason,
            "response": response[:2000],  # truncate long responses
        })

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    # Aggregate by category
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"pass": 0, "fail": 0, "unclear": 0, "error": 0}
        if "error" in r:
            categories[cat]["error"] += 1
        elif r["verdict"] is True:
            categories[cat]["pass"] += 1
        elif r["verdict"] is False:
            categories[cat]["fail"] += 1
        else:
            categories[cat]["unclear"] += 1

    total_pass = sum(c["pass"] for c in categories.values())
    total_count = sum(sum(c.values()) for c in categories.values())

    print(f"\n  SCORE: {total_pass}/{total_count}")
    for cat, stats in categories.items():
        total = sum(stats.values())
        print(f"    {cat}: {stats['pass']}/{total} pass, {stats['fail']} fail, {stats['unclear']} unclear, {stats['error']} error")

    return {
        "model": model_name,
        "model_size_gb": round(model_size_gb, 2),
        "score": total_pass,
        "total": total_count,
        "categories": categories,
        "results": results,
    }


def main():
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <machine> <llama-server-bin> <models-dir> [--limit N] [--models a,b,c]")
        sys.exit(1)

    machine = sys.argv[1]
    server_bin = sys.argv[2]
    models_dir = sys.argv[3]
    limit = None
    model_filter = None
    port = 8096

    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
        if arg == "--models" and i + 1 < len(sys.argv):
            model_filter = set(sys.argv[i + 1].split(","))
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in environment")
        sys.exit(1)

    # Load tests
    with open(os.path.join(HERE, "tests.json")) as f:
        test_data = json.load(f)
    tests = test_data["tests"]
    if limit:
        tests = tests[:limit]

    # Find models
    extra_env = {}
    if os.path.exists("/usr/local/cuda/lib64"):
        extra_env["LD_LIBRARY_PATH"] = f"/usr/local/cuda/lib64:{os.environ.get('LD_LIBRARY_PATH', '')}"

    models = sorted([
        os.path.join(models_dir, f)
        for f in os.listdir(models_dir)
        if f.endswith(".gguf")
    ], key=os.path.getsize)

    if model_filter:
        models = [m for m in models if any(f in os.path.basename(m) for f in model_filter)]

    print(f"Machine: {machine}")
    print(f"Tests: {len(tests)} ({test_data['description']})")
    print(f"Models: {len(models)}")
    print(f"Judge: {JUDGE_MODEL}")

    subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
    time.sleep(0.5)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    all_results = []
    for model_path in models:
        result = run_eval(machine, server_bin, model_path, tests, api_key, port=port, extra_env=extra_env)
        all_results.append(result)

        # Save incrementally
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        outfile = os.path.join(RESULTS_DIR, f"{machine}-custom-{timestamp}.json")
        with open(outfile, "w") as f:
            json.dump({
                "machine": machine,
                "timestamp": timestamp,
                "tests_version": test_data["version"],
                "judge": JUDGE_MODEL,
                "results": all_results,
            }, f, indent=2)

    # Final summary
    print(f"\n{'='*70}")
    print(f"SUMMARY — {machine}")
    print(f"{'='*70}")
    print(f"{'Model':<40} {'Score':>10} {'Code':>8} {'Math':>8} {'Logic':>8} {'Tool':>8}")
    print("-" * 82)
    for r in all_results:
        if r.get("skipped"):
            print(f"{r['model']:<40} {'SKIPPED':>10}")
            continue
        if "error" in r:
            print(f"{r['model']:<40} {'ERROR':>10}")
            continue
        cats = r["categories"]
        def fmt(c):
            if c not in cats: return "-"
            s = cats[c]
            tot = sum(s.values())
            return f"{s['pass']}/{tot}" if tot else "-"
        print(f"{r['model']:<40} {r['score']}/{r['total']:<8} {fmt('coding'):>8} {fmt('math-science'):>8} {fmt('logic'):>8} {fmt('tool-use'):>8}")

    print(f"\nResults: {outfile}")


if __name__ == "__main__":
    main()
