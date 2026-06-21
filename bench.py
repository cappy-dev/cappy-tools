#!/usr/bin/env python3
"""llama.cpp performance benchmark runner.

Runs llama-cli (or llama-cpp-cli) across your GGUF models with configurable
parameters, measures tokens/second from llama.cpp's built-in eval counters,
and outputs results as JSON + a terminal comparison table.

Usage:
    python3 bench.py --models ./models/*.gguf
    python3 bench.py --models ./models/*.gguf --threads 4,8 --batch 512 --tokens 128
    python3 bench.py --models ./models/q4_0.gguf --gpu            # enable GPU layers
    python3 bench.py --models ./models/*.gguf --json results.json  # save to file
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional


# ── Config ──────────────────────────────────────────────────────────────────

DEFAULT_PROMPT = "The meaning of life is"
DEFAULT_TOKENS = 128
DEFAULT_BATCH = 512
DEFAULT_THREADS = None  # auto-detect
DEFAULT_REPS = 1
DEFAULT_CTX = 2048
DEFAULT_GPU_LAYERS = 0

# llama.cpp prints these lines at the end of a run:
#   llama_print_timings:        load time =   123.45 ms
#   llama_print_timings:      sample time =     12.34 ms /   256 runs
#   llama_print_timings: prompt eval time =    56.78 ms /     8 tokens
#   llama_print_timings:        eval time =  1234.56 ms /   248 tokens /    20.09 tokens per second

TIMINGS_RE = re.compile(
    r"llama_print_timings:\s+"
    r"(?P<phase>[^=]+?)\s*=\s*(?P<ms>[\d.]+)\s*ms"
    r"(?:\s*/\s*(?P<count>\d+)\s*tokens?)?"
    r"(?:\s*/\s*(?P<tpbs>[\d.]+)\s*tokens per second)?",
    re.MULTILINE,
)


@dataclass
class BenchResult:
    model: str
    model_name: str
    threads: int
    batch: int
    ctx: int
    gpu_layers: int
    prompt_tokens: int
    generated_tokens: int
    prompt_eval_ms: float
    prompt_eval_tps: float
    eval_ms: float
    eval_tps: float
    load_ms: float
    sample_ms: float
    total_ms: float


def find_llama_binary() -> str:
    """Locate the llama.cpp binary. Checks common names and paths."""
    candidates = [
        "llama-cli",
        "llama-cpp-cli",
        "main",  # older llama.cpp builds
        "./llama-cli",
        "./llama-cpp-cli",
        "./main",
    ]
    # Check LLAMA_CPP_BIN env var first
    env_bin = os.environ.get("LLAMA_CPP_BIN")
    if env_bin and os.path.isfile(env_bin):
        return env_bin

    # Check common build dirs
    build_paths = [
        Path.cwd() / "build" / "bin",
        Path.cwd() / ".." / "build" / "bin",
        Path.home() / "llama.cpp" / "build" / "bin",
        Path.home() / "src" / "llama.cpp" / "build" / "bin",
        Path("/usr/local/bin"),
        Path("/usr/bin"),
    ]
    for bp in build_paths:
        for name in ["llama-cli", "llama-cpp-cli", "main"]:
            full = bp / name
            if full.is_file():
                return str(full)

    # Fallback: hope it's on PATH
    for c in candidates:
        try:
            result = subprocess.run(
                [c, "--version"], capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return c
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    print("ERROR: Cannot find llama.cpp binary.", file=sys.stderr)
    print("Set LLAMA_CPP_BIN=/path/to/llama-cli or add it to PATH.", file=sys.stderr)
    sys.exit(1)


def parse_timings(stderr: str) -> dict:
    """Extract timing data from llama.cpp stderr output."""
    timings = {}
    for m in TIMINGS_RE.finditer(stderr):
        phase = m.group("phase").strip()
        ms = float(m.group("ms"))
        count = int(m.group("count")) if m.group("count") else 0
        tpbs = float(m.group("tpbs")) if m.group("tpbs") else 0.0
        timings[phase] = {"ms": ms, "count": count, "tps": tpbs}
    return timings


def run_benchmark(
    binary: str,
    model: str,
    prompt: str,
    n_tokens: int,
    batch: int,
    threads: Optional[int],
    ctx: int,
    gpu_layers: int,
) -> BenchResult:
    """Run llama.cpp on a single model and capture timings."""
    cmd = [
        binary,
        "-m", model,
        "-p", prompt,
        "-n", str(n_tokens),
        "-b", str(batch),
        "-c", str(ctx),
        "-ngl", str(gpu_layers),
        "--log-disable",
    ]
    if threads is not None:
        cmd.extend(["-t", str(threads)])

    # llama-cli prints generated text to stdout and timings to stderr
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300
    )

    timings = parse_timings(result.stderr)

    # Extract the key numbers
    prompt_eval = timings.get("prompt eval time", {})
    eval_time = timings.get("eval time", {})
    load_time = timings.get("load time", {})
    sample_time = timings.get("sample time", {})

    prompt_tokens = prompt_eval.get("count", 0)
    gen_tokens = eval_time.get("count", 0)
    prompt_tps = prompt_eval.get("tps", 0.0)
    eval_tps = eval_time.get("tps", 0.0)

    # Fallback: compute tps manually if not in output
    if eval_tps == 0.0 and gen_tokens > 0:
        eval_ms = eval_time.get("ms", 0.0)
        if eval_ms > 0:
            eval_tps = gen_tokens / (eval_ms / 1000.0)
    if prompt_tps == 0.0 and prompt_tokens > 0:
        pe_ms = prompt_eval.get("ms", 0.0)
        if pe_ms > 0:
            prompt_tps = prompt_tokens / (pe_ms / 1000.0)

    model_name = Path(model).stem

    return BenchResult(
        model=str(model),
        model_name=model_name,
        threads=threads or 0,
        batch=batch,
        ctx=ctx,
        gpu_layers=gpu_layers,
        prompt_tokens=prompt_tokens,
        generated_tokens=gen_tokens,
        prompt_eval_ms=prompt_eval.get("ms", 0.0),
        prompt_eval_tps=prompt_tps,
        eval_ms=eval_time.get("ms", 0.0),
        eval_tps=eval_tps,
        load_ms=load_time.get("ms", 0.0),
        sample_ms=sample_time.get("ms", 0.0),
        total_ms=load_time.get("ms", 0.0) + prompt_eval.get("ms", 0.0) + eval_time.get("ms", 0.0) + sample_time.get("ms", 0.0),
    )


def print_table(results: list[BenchResult]):
    """Print a comparison table to the terminal."""
    if not results:
        print("No results to display.")
        return

    # Header
    fmt = "{:<35} {:>5} {:>5} {:>5} {:>8} {:>8} {:>10} {:>10}"
    print()
    print(fmt.format("MODEL", "THR", "BATCH", "CTX", "PROMPT/s", "GEN/s", "GEN ms", "TOTAL ms"))
    print("-" * 95)

    for r in results:
        # Truncate long model names
        name = r.model_name
        if len(name) > 33:
            name = name[:30] + "..."
        print(fmt.format(
            name,
            r.threads,
            r.batch,
            r.ctx,
            f"{r.prompt_eval_tps:.1f}",
            f"{r.eval_tps:.1f}",
            f"{r.eval_ms:.0f}",
            f"{r.total_ms:.0f}",
        ))

    print()

    # Highlight best gen tps
    best = max(results, key=lambda r: r.eval_tps)
    print(f"Fastest generation: {best.model_name} at {best.eval_tps:.1f} tok/s")

    if len(results) > 1:
        worst = min(results, key=lambda r: r.eval_tps)
        if best.eval_tps > 0 and worst.eval_tps > 0:
            ratio = best.eval_tps / worst.eval_tps
            print(f"Speed ratio (fastest/slowest): {ratio:.1f}x")


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark llama.cpp inference performance across GGUF models"
    )
    parser.add_argument(
        "--models", nargs="+", required=True,
        help="Path(s) to GGUF model files (supports glob: ./models/*.gguf)"
    )
    parser.add_argument(
        "--prompt", default=DEFAULT_PROMPT,
        help=f"Prompt text (default: {DEFAULT_PROMPT!r})"
    )
    parser.add_argument(
        "--tokens", type=int, default=DEFAULT_TOKENS,
        help=f"Number of tokens to generate (default: {DEFAULT_TOKENS})"
    )
    parser.add_argument(
        "--batch", type=int, default=DEFAULT_BATCH,
        help=f"Batch size (default: {DEFAULT_BATCH})"
    )
    parser.add_argument(
        "--threads", type=str, default=str(DEFAULT_THREADS or 0),
        help="Thread counts to test, comma-separated (e.g. 4,8). 0 = auto"
    )
    parser.add_argument(
        "--ctx", type=int, default=DEFAULT_CTX,
        help=f"Context size (default: {DEFAULT_CTX})"
    )
    parser.add_argument(
        "--gpu", action="store_true",
        help="Offload all layers to GPU (-ngl 99)"
    )
    parser.add_argument(
        "--ngl", type=int, default=DEFAULT_GPU_LAYERS,
        help=f"GPU layers to offload (default: {DEFAULT_GPU_LAYERS}, use --gpu for all)"
    )
    parser.add_argument(
        "--reps", type=int, default=DEFAULT_REPS,
        help=f"Repetitions per config (default: {DEFAULT_REPS})"
    )
    parser.add_argument(
        "--json", type=str, default=None,
        help="Save results to JSON file"
    )
    parser.add_argument(
        "--bin", type=str, default=None,
        help="Path to llama-cli binary (or set LLAMA_CPP_BIN env var)"
    )

    args = parser.parse_args()

    # Resolve binary
    binary = args.bin or find_llama_binary()
    print(f"Using binary: {binary}")

    # Parse thread counts
    thread_list = [int(t) for t in args.threads.split(",")]
    # Normalize 0 to None (auto)
    thread_list = [t if t != 0 else None for t in thread_list]

    # GPU layers
    gpu_layers = 99 if args.gpu else args.ngl

    # Expand globs in model paths
    import glob
    model_paths = []
    for m in args.models:
        expanded = glob.glob(m)
        if expanded:
            model_paths.extend(sorted(expanded))
        elif os.path.isfile(m):
            model_paths.append(m)
        else:
            print(f"WARNING: Model not found: {m}", file=sys.stderr)

    if not model_paths:
        print("ERROR: No model files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(model_paths)} model(s)")
    print(f"Config: {args.tokens} tokens, batch {args.batch}, ctx {args.ctx}, gpu_layers {gpu_layers}")
    print()

    # Run benchmarks
    all_results = []

    for model in model_paths:
        for threads in thread_list:
            for rep in range(args.reps):
                t_label = threads if threads else "auto"
                print(f"Benchmarking {Path(model).stem} (threads={t_label}, rep={rep+1}/{args.reps})...", end=" ", flush=True)

                try:
                    result = run_benchmark(
                        binary=binary,
                        model=model,
                        prompt=args.prompt,
                        n_tokens=args.tokens,
                        batch=args.batch,
                        threads=threads,
                        ctx=args.ctx,
                        gpu_layers=gpu_layers,
                    )
                    all_results.append(result)
                    print(f"{result.eval_tps:.1f} tok/s")
                except subprocess.TimeoutExpired:
                    print("TIMEOUT (300s)")
                except Exception as e:
                    print(f"ERROR: {e}")

    # Output results
    print()
    print_table(all_results)

    if args.json:
        data = [asdict(r) for r in all_results]
        with open(args.json, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Results saved to {args.json}")


if __name__ == "__main__":
    main()
