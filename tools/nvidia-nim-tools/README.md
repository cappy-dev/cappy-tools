# nvidia-nim-tools

Lists NVIDIA NIM models, sends completion requests, and generates embeddings from the command line. Access depends on your API key and provider limits.

## What it does

- List models. See which NIM models are available to your API key
- Chat completions. Send messages and get responses from LLMs
- Text completions. Use the legacy completions endpoint
- Embeddings. Generate vector embeddings for text
- Health check. Verify your API key and connectivity
- Config management. Store, show, and remove your API key locally
- Model aliases. 10 built-in shortcuts for popular free-tier models

## Requirements
- Python 3.7 or newer
- An NVIDIA NIM API key (free at https://build.nvidia.com/)

No pip packages needed. The tool uses only the Python standard library.

## Installation
```bash
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/nvidia-nim-tools
chmod +x nvt.py
```

Optional: symlink it into your PATH:

```bash
ln -s $(pwd)/nvt.py ~/.local/bin/nvt
```

## Setup

Get a free API key from https://build.nvidia.com/ and store it:

```bash
python3 nvt.py config --set-key nvapi-xxxxx
```

Or export it as an environment variable:

```bash
export NIM_API_KEY=nvapi-xxxxx
```

Or pass it on every command:

```bash
python3 nvt.py --api-key nvapi-xxxxx models
```

## Usage
### List available models

```bash
python3 nvt.py models
```

### Show built-in model aliases

```bash
python3 nvt.py known
```

This prints 10 shortcut names mapped to their full model IDs so you do not need to type long paths.

### Chat completion

```bash
python3 nvt.py chat llama3-70b-instruct --prompt "Explain quantum entanglement in one paragraph"
```

With a custom system prompt and temperature:

```bash
python3 nvt.py chat llama3-70b-instruct \
  --prompt "Write a haiku about Raspberry Pi" \
  --system "You are a poet." \
  --temperature 0.9
```

Read the prompt from a file:

```bash
python3 nvt.py chat llama3-70b-instruct --file prompt.txt
```

### Text completion

```bash
python3 nvt.py complete mixtral-8x22b-instruct \
  --prompt "The capital of France is"
```

### Embeddings

```bash
python3 nvt.py embed --text "Hello world"
```

Save the full response to a file:

```bash
python3 nvt.py embed --text "Hello world" --output embedding.json
```

Use a different embedding model:

```bash
python3 nvt.py embed arctic-embed-l --text "search query"
```

### Health check

```bash
python3 nvt.py health
```

Prints the number of visible models and latency. Good for confirming your key works.

### Config management

```bash
python3 nvt.py config --set-key nvapi-xxxxx   # store key
python3 nvt.py config --show                   # show masked key
python3 nvt.py config --unset                  # remove key
```

## Built-in model aliases

- `llama-3.1-nemotron-70b-instruct` -> meta/llama-3.1-nemotron-70b-instruct
- `llama3-70b-instruct` -> meta/llama3-70b-instruct
- `llama3-8b-instruct` -> meta/llama3-8b-instruct
- `mixtral-8x22b-instruct` -> mistralai/mixtral-8x22b-instruct-v0.1
- `mistral-large` -> mistralai/mistral-large-2-instruct
- `gemma-2-27b` -> google/gemma-2-27b-it
- `phi-3.5-mini` -> microsoft/phi-3.5-mini-instruct
- `starcoder2-15b` -> bigcode/starcoder2-15b-instruct
- `arctic-embed-l` -> nvidia/arctic-embed-l
- `nvidia-llama3-chatqa-1.0-70b` -> nvidia/nvidia-llama3-chatqa-1.0-70b

You can always pass the full model ID directly. The aliases are shortcuts for convenience.

## Free tier limits

NVIDIA NIM free tier has per-model rate limits (typically 40 requests per minute and 10,000 tokens per minute). The CLI will print an error if you hit a 429 response. Wait a minute and retry.

## Privacy note

Your API key is stored locally at `~/.config/nvidia-nim-tools/config.json`. The tool never sends your key anywhere except `integrate.api.nvidia.com`. No telemetry, no analytics, no phone-home.

## License

AGPL-3.0. See the [root license](../../LICENSE).
