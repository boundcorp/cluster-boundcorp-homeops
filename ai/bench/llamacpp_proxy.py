#!/usr/bin/env python3
"""
Translation proxy: llama.cpp /v1/completions -> OpenAI-compatible format.

llama.cpp returns logprobs as: {"content": [{"token": ..., "logprob": ..., "top_logprobs": [...]}, ...]}
OpenAI returns logprobs as:    {"tokens": [...], "token_logprobs": [...], "text_offset": [...], "top_logprobs": [{"<tok>": <lp>, ...}, ...]}

lm-evaluation-harness expects the OpenAI format. This proxy translates llama.cpp -> OpenAI.

Usage: python3 llamacpp_proxy.py --upstream http://localhost:8090 --port 8092
"""

import argparse
import json
import sys
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def translate_logprobs(llamacpp_logprobs, prompt_text):
    """
    Convert llama.cpp logprobs format to OpenAI format.

    llama.cpp: {"content": [{"token": "Paris", "logprob": -0.5, "top_logprobs": [{"token": "Paris", "logprob": -0.5}, ...]}, ...]}
    OpenAI:    {"tokens": ["Paris"], "token_logprobs": [-0.5], "text_offset": [21], "top_logprobs": [{"Paris": -0.5, ...}]}
    """
    if not llamacpp_logprobs or "content" not in llamacpp_logprobs:
        return None

    content = llamacpp_logprobs["content"]
    tokens = []
    token_logprobs = []
    text_offset = []
    top_logprobs_list = []

    offset = len(prompt_text)
    for entry in content:
        tok = entry.get("token", "")
        lp = entry.get("logprob", 0.0)
        tokens.append(tok)
        token_logprobs.append(lp)
        text_offset.append(offset)
        offset += len(tok)

        # top_logprobs: list of {token, logprob} -> dict {token: logprob}
        top = {}
        for t in entry.get("top_logprobs", []):
            top[t.get("token", "")] = t.get("logprob", 0.0)
        top_logprobs_list.append(top)

    return {
        "tokens": tokens,
        "token_logprobs": token_logprobs,
        "text_offset": text_offset,
        "top_logprobs": top_logprobs_list,
    }


class ProxyHandler(BaseHTTPRequestHandler):
    upstream = "http://localhost:8090"

    def log_message(self, fmt, *args):
        pass  # suppress access logs

    def do_GET(self):
        # Pass through health checks etc
        try:
            req = urllib.request.Request(self.upstream + self.path)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in ("content-length", "transfer-encoding"):
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except Exception as e:
            self.send_error(502, str(e))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Parse the request
        try:
            req_json = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        # Forward to upstream
        try:
            req = urllib.request.Request(
                self.upstream + self.path,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                resp_body = resp.read()
                resp_json = json.loads(resp_body)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            return
        except Exception as e:
            self.send_error(502, str(e))
            return

        # Translate logprobs in completions response
        if self.path.endswith("/v1/completions") and "choices" in resp_json:
            prompt_text = req_json.get("prompt", "")
            for choice in resp_json["choices"]:
                if "logprobs" in choice and choice["logprobs"]:
                    translated = translate_logprobs(choice["logprobs"], prompt_text)
                    if translated:
                        choice["logprobs"] = translated

        out = json.dumps(resp_json).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", default="http://localhost:8090")
    parser.add_argument("--port", type=int, default=8092)
    args = parser.parse_args()

    ProxyHandler.upstream = args.upstream
    server = ThreadingHTTPServer(("0.0.0.0", args.port), ProxyHandler)
    print(f"Proxy listening on :{args.port} -> {args.upstream}")
    server.serve_forever()


if __name__ == "__main__":
    main()
