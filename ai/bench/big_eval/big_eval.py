#!/usr/bin/env python3
"""Comprehensive eval: HumanEval (code) + GSM8K (math) with auto-judges.

Usage:
    python3 big_eval.py <endpoint_url> <model_name> [--limit-humaneval N] [--limit-gsm8k N] [--tag LABEL]

Example:
    python3 big_eval.py https://abc-8000.proxy.runpod.net Qwen/Qwen2.5-72B-Instruct-AWQ \\
        --limit-gsm8k 100 --tag 72b-awq

Judges:
    - HumanEval: executes model code against canonical tests in a subprocess
      (10s timeout each). Reports pass@1.
    - GSM8K: extracts final number from response, compares to gold answer.
      Tolerant of formatting (commas, dollar signs, decimals).
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
RESULTS_DIR = os.path.join(HERE, "results")

HUMANEVAL_SYSTEM = (
    "You are a Python coding assistant. When asked to complete a function, respond "
    "with only the complete function inside a single ```python ... ``` code block. "
    "Do not include explanations, examples, or test code."
)
GSM8K_SYSTEM = (
    "You are a math assistant. Solve the problem step by step, then on the last line "
    "write 'Answer: N' where N is the final numeric answer."
)


def call_model(endpoint_url, model_name, system, user_prompt, max_tokens=1024, temperature=0.2, no_think=False):
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if no_think:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{endpoint_url}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "curl/8.5.0"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read()[:200].decode('utf-8', errors='replace')}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    elapsed = time.time() - t0
    msg = data["choices"][0]["message"]["content"]
    u = data["usage"]
    return {
        "response": msg,
        "prompt_tokens": u["prompt_tokens"],
        "completion_tokens": u["completion_tokens"],
        "elapsed_s": round(elapsed, 2),
        "finish_reason": data["choices"][0].get("finish_reason"),
    }


# ---- HumanEval ----

CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def extract_python(response):
    m = CODE_BLOCK_RE.search(response)
    if m:
        return m.group(1).strip()
    return response.strip()


HUMANEVAL_RUNNER = """
import sys, json
code = sys.stdin.read()
try:
    exec(code, {'__name__': '__main__'})
    print(json.dumps({'ok': True}))
except AssertionError as e:
    print(json.dumps({'ok': False, 'err': 'AssertionError: ' + str(e)[:200]}))
except Exception as e:
    print(json.dumps({'ok': False, 'err': type(e).__name__ + ': ' + str(e)[:200]}))
"""


def run_humaneval(problem, response, timeout=10):
    completion = extract_python(response)
    # Build the check program. If the model returned the whole function (most common
    # for chat models), we use completion alone; if it returned just the body, we
    # concatenate with the prompt (stripping the signature).
    if f"def {problem['entry_point']}" in completion:
        program = completion
    else:
        program = problem["prompt"] + "\n" + completion
    check_program = program + "\n" + problem["test"] + f"\ncheck({problem['entry_point']})\n"
    try:
        r = subprocess.run(
            ["python3", "-c", HUMANEVAL_RUNNER],
            input=check_program,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
            return out.get("ok", False), out.get("err")
        except Exception:
            return False, f"runner_parse_error: stdout={r.stdout[:200]} stderr={r.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, f"timeout({timeout}s)"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def humaneval_prompt(problem):
    return (
        "Complete the following Python function. Return only the complete function "
        "in a ```python``` code block.\n\n```python\n" + problem["prompt"] + "```"
    )


# ---- GSM8K ----

NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def extract_gsm8k_gold(answer_field):
    # Gold answer ends with "#### N"
    m = re.search(r"####\s*(-?[\d,.]+)", answer_field)
    if not m:
        return None
    return _parse_num(m.group(1))


def _parse_num(s):
    if s is None:
        return None
    s = s.strip().replace(",", "").replace("$", "").rstrip(".")
    try:
        f = float(s)
        return f
    except Exception:
        return None


def extract_gsm8k_pred(response):
    # First try "Answer: N" pattern (what we requested)
    m = re.search(r"[Aa]nswer\s*[:=]?\s*\$?(-?[\d,.]+)", response)
    if m:
        return _parse_num(m.group(1))
    # Fallback: last number in the response
    nums = NUM_RE.findall(response.replace(",", ""))
    if nums:
        return _parse_num(nums[-1])
    return None


def grade_gsm8k(gold, pred):
    if gold is None or pred is None:
        return False
    return abs(gold - pred) < 1e-4


def gsm8k_prompt(problem):
    return problem["question"]


# ---- runner ----

def load_humaneval():
    with open(os.path.join(DATA_DIR, "humaneval.jsonl")) as f:
        return [json.loads(line) for line in f]


def load_gsm8k():
    with open(os.path.join(DATA_DIR, "gsm8k_test.jsonl")) as f:
        return [json.loads(line) for line in f]


def run_suite(args):
    endpoint = args.endpoint.rstrip("/")
    os.makedirs(RESULTS_DIR, exist_ok=True)

    humaneval = load_humaneval()
    gsm8k = load_gsm8k()

    if args.limit_humaneval:
        humaneval = humaneval[: args.limit_humaneval]
    if args.limit_gsm8k:
        random.seed(42)
        gsm8k = random.sample(gsm8k, min(args.limit_gsm8k, len(gsm8k)))

    tag = args.tag or "unlabeled"
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(RESULTS_DIR, f"big-{tag}-{ts}.json")

    summary = {
        "tag": tag,
        "model": args.model,
        "endpoint": endpoint,
        "started_at": ts,
        "humaneval": {"total": len(humaneval), "pass": 0, "fail": 0, "error": 0, "items": []},
        "gsm8k": {"total": len(gsm8k), "pass": 0, "fail": 0, "error": 0, "items": []},
    }

    print(f"== big_eval [{tag}] ==", flush=True)
    print(f"  endpoint: {endpoint}", flush=True)
    print(f"  model:    {args.model}", flush=True)
    print(f"  humaneval: {len(humaneval)} problems", flush=True)
    print(f"  gsm8k:     {len(gsm8k)} problems", flush=True)
    print(f"  results:   {out_path}", flush=True)

    t_start = time.time()

    # HumanEval
    print("\n--- HumanEval ---", flush=True)
    for i, prob in enumerate(humaneval, 1):
        res = call_model(endpoint, args.model, HUMANEVAL_SYSTEM, humaneval_prompt(prob), max_tokens=1024, no_think=args.no_think)
        if "error" in res:
            summary["humaneval"]["error"] += 1
            item = {"task_id": prob["task_id"], "error": res["error"]}
        else:
            ok, err = run_humaneval(prob, res["response"])
            item = {
                "task_id": prob["task_id"],
                "pass": ok,
                "err": err,
                "completion_tokens": res["completion_tokens"],
                "elapsed_s": res["elapsed_s"],
                "response": res["response"][:2000],
            }
            if ok:
                summary["humaneval"]["pass"] += 1
            else:
                summary["humaneval"]["fail"] += 1
        summary["humaneval"]["items"].append(item)
        if i % 10 == 0 or i == len(humaneval):
            passed = summary["humaneval"]["pass"]
            print(f"  [{i}/{len(humaneval)}] pass@1 so far: {passed}/{i} = {passed/i:.1%}", flush=True)
        # Incremental save
        if i % 20 == 0:
            with open(out_path, "w") as f:
                json.dump(summary, f, indent=2)

    # GSM8K
    print("\n--- GSM8K ---", flush=True)
    for i, prob in enumerate(gsm8k, 1):
        res = call_model(endpoint, args.model, GSM8K_SYSTEM, gsm8k_prompt(prob), max_tokens=768, no_think=args.no_think)
        gold = extract_gsm8k_gold(prob["answer"])
        if "error" in res:
            summary["gsm8k"]["error"] += 1
            item = {"gold": gold, "error": res["error"]}
        else:
            pred = extract_gsm8k_pred(res["response"])
            ok = grade_gsm8k(gold, pred)
            item = {
                "gold": gold,
                "pred": pred,
                "pass": ok,
                "completion_tokens": res["completion_tokens"],
                "elapsed_s": res["elapsed_s"],
                "response": res["response"][:1500],
            }
            if ok:
                summary["gsm8k"]["pass"] += 1
            else:
                summary["gsm8k"]["fail"] += 1
        summary["gsm8k"]["items"].append(item)
        if i % 20 == 0 or i == len(gsm8k):
            passed = summary["gsm8k"]["pass"]
            print(f"  [{i}/{len(gsm8k)}] pass@1 so far: {passed}/{i} = {passed/i:.1%}", flush=True)
        if i % 20 == 0:
            with open(out_path, "w") as f:
                json.dump(summary, f, indent=2)

    summary["total_elapsed_s"] = round(time.time() - t_start, 1)

    # Totals
    he, gs = summary["humaneval"], summary["gsm8k"]
    he_rate = he["pass"] / max(he["total"], 1)
    gs_rate = gs["pass"] / max(gs["total"], 1)
    total = he["total"] + gs["total"]
    total_pass = he["pass"] + gs["pass"]

    print("\n== SUMMARY ==", flush=True)
    print(f"  HumanEval: {he['pass']}/{he['total']} = {he_rate:.1%}", flush=True)
    print(f"  GSM8K:     {gs['pass']}/{gs['total']} = {gs_rate:.1%}", flush=True)
    print(f"  Combined:  {total_pass}/{total} = {total_pass/max(total,1):.1%}", flush=True)
    print(f"  Elapsed:   {summary['total_elapsed_s']}s", flush=True)
    print(f"  Saved:     {out_path}", flush=True)

    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)

    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("endpoint", help="vLLM endpoint URL, e.g. https://abc-8000.proxy.runpod.net")
    ap.add_argument("model", help="Model name as registered with vLLM")
    ap.add_argument("--limit-humaneval", type=int, default=None, help="Cap HumanEval problems (default: all 164)")
    ap.add_argument("--limit-gsm8k", type=int, default=100, help="Cap GSM8K problems (default: 100)")
    ap.add_argument("--tag", default=None, help="Label for results filename")
    ap.add_argument("--no-think", action="store_true", help="Disable thinking mode (Qwen3+)")
    args = ap.parse_args()
    run_suite(args)


if __name__ == "__main__":
    main()
