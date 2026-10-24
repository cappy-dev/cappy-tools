#!/usr/bin/env python3
"""llm-bench-lite: Lightweight LLM inference benchmark.

Measures time-to-first-token, tokens per second, and total latency
for any OpenAI-compatible API endpoint. Zero dependencies beyond
the Python standard library.
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import statistics


DEFAULT_PROMPTS = [
    "Write a short poem about the moon.",
    "Explain recursion in one paragraph.",
    "List five benefits of exercise.",
    "Summarize the concept of gravity briefly.",
    "Describe a sunset in three sentences.",
]

DEFAULT_MODELS = [
    "gpt-3.5-turbo",
    "gpt-4",
    "gpt-4o-mini",
]

KNOWN_PROVIDERS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "env_key": "NIM_API_KEY",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "env_key": "TOGETHER_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "env_key": "FIREWORKS_API_KEY",
    },
    "localhost": {
        "base_url": "http://localhost:11434/v1",
        "env_key": "OLLAMA_API_KEY",
    },
}


def get_api_key(provider, key_arg):
    """Resolve API key from argument, env variable, or config file."""
    if key_arg:
        return key_arg

    import os
    if provider and provider in KNOWN_PROVIDERS:
        env_var = KNOWN_PROVIDERS[provider]["env_key"]
        env_val = os.environ.get(env_var, "")
        if env_val:
            return env_val

    config_path = get_config_path()
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
            stored = cfg.get("api_key", "")
            if stored:
                return stored
        except (json.JSONDecodeError, OSError):
            pass

    return ""


def get_config_path():
    from pathlib import Path
    return Path.home() / ".config" / "llm-bench-lite" / "config.json"


def save_api_key(key):
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    existing["api_key"] = key
    with open(config_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"API key saved to {config_path}")


def mask_key(key):
    if len(key) <= 8:
        return key[:2] + "***" if len(key) >= 2 else "***"
    return key[:4] + "..." + key[-4:]


def chat_completion(base_url, api_key, model, prompt, max_tokens, temperature):
    """Send a chat completion request and return timing data."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    first_token_time = None
    tokens_received = 0
    full_text = ""

    try:
        t_start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=120) as resp:
            buffer = ""
            for chunk in iter(lambda: resp.read(1), b""):
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        if first_token_time is None:
                            first_token_time = time.perf_counter()
                        tokens_received += 1
                        full_text += content
        t_end = time.perf_counter()
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return {
            "error": f"HTTP {e.code}: {e.reason}",
            "detail": err_body,
            "success": False,
        }
    except urllib.error.URLError as e:
        return {"error": f"Connection error: {e.reason}", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}

    total_time = t_end - t_start
    ttft = (first_token_time - t_start) if first_token_time else total_time

    # Estimate token count more accurately if we got text
    # Rough: words * 1.3 for English, or just use chunk count
    estimated_tokens = max(tokens_received, len(full_text.split()) * 1.3)
    tps = estimated_tokens / max(total_time - ttft, 0.001) if tokens_received > 0 else 0

    return {
        "success": True,
        "ttft": ttft,
        "total_time": total_time,
        "tokens": int(estimated_tokens),
        "tps": tps,
        "response_preview": full_text[:150],
    }


def chat_completion_non_stream(base_url, api_key, model, prompt, max_tokens, temperature):
    """Non-streaming fallback for servers that do not support streaming."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        t_start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp_body = json.loads(resp.read().decode("utf-8"))
        t_end = time.perf_counter()
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return {
            "error": f"HTTP {e.code}: {e.reason}",
            "detail": err_body,
            "success": False,
        }
    except urllib.error.URLError as e:
        return {"error": f"Connection error: {e.reason}", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}

    total_time = t_end - t_start
    choices = resp_body.get("choices", [])
    content = choices[0].get("message", {}).get("content", "") if choices else ""
    usage = resp_body.get("usage", {})
    token_count = usage.get("completion_tokens", len(content.split()))

    return {
        "success": True,
        "ttft": None,
        "total_time": total_time,
        "tokens": token_count,
        "tps": token_count / max(total_time, 0.001),
        "response_preview": content[:150],
    }


def run_benchmark(base_url, api_key, models, prompts, rounds, max_tokens,
                  temperature, no_stream):
    """Run the full benchmark and return structured results."""
    results = {}

    for model in models:
        model_results = []
        for prompt in prompts:
            prompt_results = []
            for i in range(rounds):
                if no_stream:
                    r = chat_completion_non_stream(
                        base_url, api_key, model, prompt, max_tokens, temperature
                    )
                else:
                    r = chat_completion(
                        base_url, api_key, model, prompt, max_tokens, temperature
                    )
                r["round"] = i + 1
                r["prompt_preview"] = prompt[:60]
                prompt_results.append(r)

                status = "OK" if r.get("success") else f"FAIL: {r.get('error', 'unknown')}"
                ttft_str = f"{r['ttft']:.3f}s" if r.get("ttft") is not None else "N/A"
                print(
                    f"  {model} | round {i+1}/{rounds} | "
                    f"TTFT: {ttft_str} | "
                    f"Total: {r.get('total_time', 0):.2f}s | "
                    f"Tokens: {r.get('tokens', 0)} | "
                    f"TPS: {r.get('tps', 0):.1f} | "
                    f"{status}"
                )

            model_results.extend(prompt_results)

        results[model] = model_results

    return results


def summarize(results, no_stream):
    """Print a summary table of benchmark results."""
    print("\n" + "=" * 70)
    print("BENCHMARK SUMMARY")
    print("=" * 70)

    for model, runs in results.items():
        successful = [r for r in runs if r.get("success")]
        failed = [r for r in runs if not r.get("success")]

        print(f"\nModel: {model}")
        print(f"  Successful runs: {len(successful)} / {len(runs)}")

        if failed:
            print(f"  Failed runs: {len(failed)}")
            first_err = failed[0].get("error", "unknown")
            print(f"  First error: {first_err}")

        if not successful:
            print("  No data to summarize.")
            continue

        ttft_vals = [r["ttft"] for r in successful if r.get("ttft") is not None]
        total_vals = [r["total_time"] for r in successful]
        tps_vals = [r["tps"] for r in successful]
        token_vals = [r["tokens"] for r in successful]

        if ttft_vals and not no_stream:
            print(f"  Time to first token:")
            print(f"    min:    {min(ttft_vals):.3f}s")
            print(f"    median: {statistics.median(ttft_vals):.3f}s")
            print(f"    max:    {max(ttft_vals):.3f}s")

        print(f"  Total latency:")
        print(f"    min:    {min(total_vals):.3f}s")
        print(f"    median: {statistics.median(total_vals):.3f}s")
        print(f"    max:    {max(total_vals):.3f}s")

        print(f"  Tokens per second:")
        print(f"    min:    {min(tps_vals):.1f}")
        print(f"    median: {statistics.median(tps_vals):.1f}")
        print(f"    max:    {max(tps_vals):.1f}")

        print(f"  Token count:")
        print(f"    min:    {min(token_vals)}")
        print(f"    median: {int(statistics.median(token_vals))}")
        print(f"    max:    {max(token_vals)}")

    print()


def export_json(results, output_path):
    """Export results to a JSON file."""
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Results exported to {output_path}")


def export_csv(results, output_path):
    """Export results to a CSV file."""
    import csv
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "model", "round", "prompt_preview", "success",
            "ttft", "total_time", "tokens", "tps", "error"
        ])
        for model, runs in results.items():
            for r in runs:
                writer.writerow([
                    model,
                    r.get("round", ""),
                    r.get("prompt_preview", ""),
                    r.get("success", False),
                    f"{r.get('ttft', 0):.4f}" if r.get("ttft") is not None else "",
                    f"{r.get('total_time', 0):.4f}",
                    r.get("tokens", 0),
                    f"{r.get('tps', 0):.2f}",
                    r.get("error", ""),
                ])
    print(f"Results exported to {output_path}")


def cmd_providers():
    """List known provider shortcuts."""
    print("Known providers (use with --provider):\n")
    for name, info in KNOWN_PROVIDERS.items():
        print(f"  {name}")
        print(f"    Base URL: {info['base_url']}")
        print(f"    Env key:  {info['env_key']}")
        print()


def cmd_config(args):
    """Manage stored config."""
    if args.set_key:
        save_api_key(args.set_key)
    elif args.show:
        key = get_api_key(args.provider, None)
        if key:
            print(f"API key: {mask_key(key)}")
        else:
            print("No API key configured.")
    elif args.unset:
        config_path = get_config_path()
        if config_path.exists():
            config_path.unlink()
            print("Config removed.")
        else:
            print("No config file found.")
    else:
        print("Use --set-key, --show, or --unset")


def main():
    parser = argparse.ArgumentParser(
        description="llm-bench-lite: Lightweight LLM inference benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  %(prog)s --provider openai --model gpt-4o-mini
  %(prog)s --url http://localhost:11434/v1 --model llama3
  %(prog)s --provider groq --model llama-3.1-8b --rounds 5
  %(prog)s providers
  %(prog)s config --show""",
    )
    sub = parser.add_subparsers(dest="command")

    providers_cmd = sub.add_parser("providers", help="List known provider shortcuts")
    config_cmd = sub.add_parser("config", help="Manage API key config")
    config_cmd.add_argument("--set-key", help="Store an API key")
    config_cmd.add_argument("--show", action="store_true", help="Show current key (masked)")
    config_cmd.add_argument("--unset", action="store_true", help="Remove stored key")
    config_cmd.add_argument("--provider", choices=list(KNOWN_PROVIDERS.keys()),
                             help="Provider for env key lookup")

    # Main benchmark args (also works without subcommand for brevity)
    parser.add_argument("--provider", "-p", choices=list(KNOWN_PROVIDERS.keys()),
                        help="Use a known provider shortcut")
    parser.add_argument("--url", "-u", help="Custom base URL (OpenAI-compatible)")
    parser.add_argument("--api-key", "-k", help="API key (or set env var)")
    parser.add_argument("--model", "-m", action="append", dest="models",
                        help="Model name (can specify multiple times)")
    parser.add_argument("--prompt", "-P", action="append", dest="prompts",
                        help="Custom prompt (can specify multiple times)")
    parser.add_argument("--rounds", "-r", type=int, default=3,
                        help="Number of rounds per prompt (default: 3)")
    parser.add_argument("--max-tokens", "-t", type=int, default=128,
                        help="Max tokens per response (default: 128)")
    parser.add_argument("--temperature", type=float, default=0.7,
                        help="Sampling temperature (default: 0.7)")
    parser.add_argument("--no-stream", action="store_true",
                        help="Disable streaming (no TTFT measurement)")
    parser.add_argument("--output", "-o", help="Export results to JSON file")
    parser.add_argument("--csv", help="Export results to CSV file")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Only show summary, not individual runs")

    args = parser.parse_args()

    if args.command == "providers":
        cmd_providers()
        return

    if args.command == "config":
        cmd_config(args)
        return

    # Resolve base URL
    if args.provider:
        base_url = KNOWN_PROVIDERS[args.provider]["base_url"]
    elif args.url:
        base_url = args.url
    else:
        base_url = "http://localhost:11434/v1"

    # Resolve API key
    api_key = get_api_key(args.provider, args.api_key)

    # Resolve models
    models = args.models or ["llama3"]

    # Resolve prompts
    prompts = args.prompts or DEFAULT_PROMPTS

    if not args.quiet:
        print(f"llm-bench-lite")
        print(f"Base URL:  {base_url}")
        print(f"API key:   {mask_key(api_key) if api_key else '(none)'}")
        print(f"Models:    {', '.join(models)}")
        print(f"Prompts:   {len(prompts)}")
        print(f"Rounds:    {args.rounds}")
        print(f"Max tokens: {args.max_tokens}")
        print(f"Streaming: {'off' if args.no_stream else 'on'}")
        print()

    results = run_benchmark(
        base_url, api_key, models, prompts, args.rounds,
        args.max_tokens, args.temperature, args.no_stream,
    )

    summarize(results, args.no_stream)

    if args.output:
        export_json(results, args.output)

    if args.csv:
        export_csv(results, args.csv)


if __name__ == "__main__":
    main()
