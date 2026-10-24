# llm-bench-lite

Measures time to first token, tokens per second, and request latency for OpenAI-compatible APIs.

## What it does

- Time to first token (TTFT). Measures how fast the model starts responding (streaming mode)
- Tokens per second (TPS). Measures generation throughput
- Total latency. End to end request time
- Multiple rounds. Run each prompt several times for reliable averages
- Multiple models. Compare models side by side in one run
- Provider shortcuts. Built in shortcuts for OpenAI, Groq, Together, Fireworks, NVIDIA NIM, and local Ollama
- Export. Save results as JSON or CSV for analysis

No pip packages required. Uses only the Python standard library.

## Installation
```bash
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/llm-bench-lite
chmod +x llm_bench_lite.py
```

Optional: add it to your PATH:

```bash
ln -s $(pwd)/llm_bench_lite.py ~/.local/bin/llm-bench
```

## Quick start

Benchmark a local Ollama model:

```bash
python3 llm_bench_lite.py --model llama3
```

Benchmark OpenAI:

```bash
export OPENAI_API_KEY=sk-xxxxx
python3 llm_bench_lite.py --provider openai --model gpt-4o-mini
```

Benchmark Groq (fast inference):

```bash
export GROQ_API_KEY=gsk_xxxxx
python3 llm_bench_lite.py --provider groq --model llama-3.1-8b
```

## Usage
```bash
python3 llm_bench_lite.py [OPTIONS]
python3 llm_bench_lite.py providers
python3 llm_bench_lite.py config --show
```

### Options

- `--provider, -p`: Use a known provider shortcut (openai, groq, together, fireworks, nvidia, localhost)
- `--url, -u`: Custom OpenAI-compatible base URL
- `--api-key, -k`: API key (or set the env variable for your provider)
- `--model, -m`: Model name. Can be specified multiple times to compare models
- `--prompt, -P`: Custom prompt text. Can be specified multiple times. Defaults to 5 built in prompts
- `--rounds, -r`: Number of rounds per prompt (default: 3)
- `--max-tokens, -t`: Max tokens per response (default: 128)
- `--temperature`: Sampling temperature (default: 0.7)
- `--no-stream`: Disable streaming. TTFT will not be measured
- `--output, -o`: Export results to a JSON file
- `--csv`: Export results to a CSV file
- `--quiet, -q`: Only show the summary, skip individual run output

### Subcommands

- `providers`: List all known provider shortcuts and their base URLs
- `config`: Manage your stored API key

## Examples
### Compare two models

```bash
python3 llm_bench_lite.py --provider openai -m gpt-4o-mini -m gpt-3.5-turbo
```

### Custom prompt with 5 rounds

```bash
python3 llm_bench_lite.py --provider groq --model llama-3.1-70b \
  --prompt "Explain quantum computing in 50 words" \
  --rounds 5
```

### Benchmark a local Llama.cpp server

```bash
python3 llm_bench_lite.py --url http://localhost:8080/v1 --model llama-3
```

### Export results

```bash
python3 llm_bench_lite.py --provider nvidia --model llama-3.1-nemotron-70b \
  --output results.json --csv results.csv
```

### Non streaming mode

Some servers do not support streaming. Use `--no-stream` for those:

```bash
python3 llm_bench_lite.py --provider openai --model gpt-4 \
  --no-stream
```

### API key management

```bash
# Store key in config file
python3 llm_bench_lite.py config --set-key sk-xxxxx

# Show current key (masked)
python3 llm_bench_lite.py config --show

# Remove stored key
python3 llm_bench_lite.py config --unset
```

## How it works

1. Sends chat completion requests to any OpenAI-compatible `/v1/chat/completions` endpoint
2. In streaming mode, measures the time until the first token arrives (TTFT)
3. Counts output tokens and calculates tokens per second
4. Runs each prompt multiple times (configurable with `--rounds`)
5. Prints a summary with min, median, and max for TTFT, total time, and TPS

The tool uses the streaming SSE protocol by default to capture TTFT. If the server does not support streaming, use `--no-stream` for a simpler measurement of total latency and TPS.

## Output format

Each run prints one line:

```
  llama3 | round 1/3 | TTFT: 0.142s | Total: 1.23s | Tokens: 87 | TPS: 70.7 | OK
```

After all runs, a summary shows statistics:

```
Model: llama3
  Successful runs: 15 / 15
  Time to first token:
    min:    0.120s
    median: 0.145s
    max:    0.189s
  Total latency:
    min:    0.980s
    median: 1.120s
    max:    1.450s
  Tokens per second:
    min:    60.5
    median: 72.3
    max:    88.1
  Token count:
    min:    65
    median: 88
    max:    127
```

## Config file

API keys are stored at `~/.config/llm-bench-lite/config.json`. The tool never sends your key anywhere except the API endpoint you specify. No telemetry, no analytics, no phone-home.

## Known providers

| Shortcut | Base URL | Env variable |
|---|---|---|
| openai | https://api.openai.com/v1 | OPENAI_API_KEY |
| nvidia | https://integrate.api.nvidia.com/v1 | NIM_API_KEY |
| together | https://api.together.xyz/v1 | TOGETHER_API_KEY |
| groq | https://api.groq.com/openai/v1 | GROQ_API_KEY |
| fireworks | https://api.fireworks.ai/inference/v1 | FIREWORKS_API_KEY |
| localhost | http://localhost:11434/v1 | OLLAMA_API_KEY |

## Why another benchmark?

Most LLM benchmarks are heavy frameworks with dozens of dependencies, complex config files, and database backends. Sometimes you just want a script that hits an endpoint a few times and tells you how fast it is. That is all this is.

## License

AGPL-3.0. See the [root license](../../LICENSE).
