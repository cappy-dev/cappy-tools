# llama-cpp-benchmark

Runs llama-cli against GGUF models and compares its reported timing measurements. Requires Python and a working llama.cpp build.

## What it measures
- Prompt eval speed. how fast the model processes the input prompt (tok/s)
- Generation speed. how fast the model generates new tokens (tok/s)
- Load time. how long the model takes to load from disk
- Total time. end-to-end latency for the whole run

## Quick start
```bash
# Make sure llama-cli is on your PATH (or set LLAMA_CPP_BIN)
python3 bench.py --models ./models/*.gguf
```

That's it. You get a table like:

```
MODEL                               THR  BATCH   CTX  PROMPT/s    GEN/s     GEN ms   TOTAL ms
-----------------------------------------------------------------------------------------------
qwen2.5-0.5b-q4_0                    8    512  2048     312.5     42.1       3040      3250
qwen2.5-0.5b-q8_0                    8    512  2048     245.0     31.8       4025      4280
llama-3.2-1b-q4_k_m                  8    512  2048     198.3     27.4       4738      4970

Fastest generation: qwen2.5-0.5b-q4_0 at 42.1 tok/s
Speed ratio (fastest/slowest): 1.5x
```

## Options

```bash
# Test multiple thread counts
python3 bench.py --models ./models/*.gguf --threads 4,8,16

# Generate more tokens for steadier measurements
python3 bench.py --models ./models/*.gguf --tokens 512

# Enable GPU offload
python3 bench.py --models ./models/*.gguf --gpu

# Control GPU layers manually
python3 bench.py --models ./models/*.gguf --ngl 32

# Change context size
python3 bench.py --models ./models/*.gguf --ctx 4096

# Run multiple repetitions and average
python3 bench.py --models ./models/*.gguf --reps 3

# Save results as JSON
python3 bench.py --models ./models/*.gguf --json results.json

# Specify binary directly
python3 bench.py --models ./models/*.gguf --bin /path/to/llama-cli
```

## All Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--models` | (required) | Path(s) to GGUF files, supports globs |
| `--prompt` | "The meaning of life is" | Prompt text |
| `--tokens` | 128 | Tokens to generate |
| `--batch` | 512 | Batch size |
| `--threads` | 0 (auto) | Thread counts, comma-separated (e.g. 4,8) |
| `--ctx` | 2048 | Context window size |
| `--gpu` | off | Offload all layers to GPU (-ngl 99) |
| `--ngl` | 0 | Number of GPU layers to offload |
| `--reps` | 1 | Repetitions per config |
| `--json` | (none) | Save results to JSON file |
| `--bin` | (auto) | Path to llama-cli binary |

## Finding llama-cli

The script looks for the binary in this order:

1. `--bin` flag
2. `LLAMA_CPP_BIN` environment variable
3. Common build directories (`./build/bin/`, `~/llama.cpp/build/bin/`)
4. System PATH (`llama-cli`, `llama-cpp-cli`, `main`)

## How It Works

1. Runs `llama-cli -m model.gguf -p "prompt" -n 128 ...`
2. Captures stderr, which contains llama.cpp's built-in timing output
3. Parses `llama_print_timings` lines for eval speed, prompt speed, and load time
4. Sums up total latency
5. Prints a comparison table and optionally saves JSON

No API calls, no server mode, no Python ML libraries. Just the raw llama.cpp binary and standard output parsing.

## Typical Use Cases

- Compare quantizations. Q4_0 vs Q4_K_M vs Q8_0 vs F16, which is the best tradeoff?
- Tune thread count. Is 4, 8, or 16 threads fastest on your CPU?
- CPU vs GPU. Benchmark with and without `--gpu` to see the speedup
- Hardware upgrades. Run before and after a RAM/CPU/GPU upgrade
- Regression testing. Run the same benchmark after updating llama.cpp

## Tips

- Use `--tokens 512` or higher for more stable measurements (short runs have high variance)
- Use `--reps 3` and take the average if you want reliable numbers
- Close other programs before benchmarking for clean results
- First run may be slower due to disk caching; run twice and take the second result
- On laptops, plug in power and disable power saving for consistent results

## Requirements
- Python 3.7+
- llama.cpp compiled with `llama-cli` binary
- GGUF model files to test

## License

AGPL-3.0. See the [root license](../../LICENSE).
