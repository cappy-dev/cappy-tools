# jmail-python

A simple, easy-to-use Python library for accessing the [Jmail Data API](https://jmail.world/docs/), the public data API for the Jeffrey Epstein email archive.

No API keys. No rate limits. No authentication. Just data.

## Installation

```bash
pip install jmail-python
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install jmail-python
```

## Quick Start

```python
from jmail import JmailClient

client = JmailClient()

# Get emails (slim = no body text, much faster)
df = client.emails(slim=True)
print(df.head())

# Full emails with body text
df = client.emails()

# First 100 emails only
df = client.emails(head=100)

# Document metadata
docs = client.documents()

# Documents with full extracted text (large download)
docs = client.documents(include_text=True)
```

## Available Datasets

| Method | Dataset | Records | Size |
|--------|---------|---------|------|
| `client.emails()` | Emails (full) | 1.78M | 334 MB |
| `client.emails(slim=True)` | Emails (slim) | 1.78M | 41 MB |
| `client.documents()` | Documents | 1.41M | 25 MB |
| `client.documents(include_text=True)` | Documents (full text) | 1.41M | Large |
| `client.photos()` | Photos | 18K | ~1 MB |
| `client.people()` | People | 473 | <100 KB |
| `client.photo_faces()` | Photo Faces | 975 | <100 KB |
| `client.star_counts()` | Star Counts | 414K | ~2 MB |
| `client.release_batches()` | Release Batches | 11 | <10 KB |
| `client.imessage_conversations()` | iMessage Conversations | 15 | <10 KB |
| `client.imessage_messages()` | iMessage Messages | 4,509 | ~172 KB |

All methods return a pandas DataFrame. Pass `head=N` to any method to only load the first N rows.

## Search and Analysis

```python
client = JmailClient()

# Search emails by sender (case-insensitive)
df = client.search_by_sender("ghislaine")

# Search by subject
df = client.search_by_subject("flight")

# Top 10 most frequent senders
top = client.top_senders(n=10)
print(top)
```

## Caching

The client uses ETag-based conditional requests to avoid re-downloading unchanged files.

- Cache location: `~/.cache/jmail/`
- First download: saves the parquet file and its ETag
- Subsequent requests: sends `If-None-Match` header, uses cache on 304 Not Modified
- Disable: `JmailClient(cache=False)`
- Clear: `client.clear_cache()`

```python
# Always download fresh (no caching)
client = JmailClient(cache=False)

# Custom cache directory
client = JmailClient(cache_dir="/tmp/my-cache")

# Clear the cache
client.clear_cache()
```

## Direct URLs

Need raw URLs for use with DuckDB, Polars, or other tools?

```python
client = JmailClient()

# Single dataset URL
url = client.url("emails-slim")
# -> "https://data.jmail.world/v1/emails-slim.parquet"

url = client.url("emails-slim", fmt="ndjson.gz")
# -> "https://data.jmail.world/v1/emails-slim.ndjson.gz"

# All dataset URLs
all_urls = client.urls()
```

## Manifest

The manifest provides dataset metadata and checksums:

```python
client = JmailClient()
manifest = client.manifest()
print(manifest["version"])
print(manifest["datasets"]["emails"]["record_count"])
```

## DuckDB Integration

You can query the parquet files directly over HTTP without downloading:

```python
import duckdb

result = duckdb.sql("""
    SELECT sender, COUNT(*) as n
    FROM read_parquet('https://data.jmail.world/v1/emails-slim.parquet')
    GROUP BY sender ORDER BY n DESC LIMIT 20
""").df()
print(result)
```

## Data Sources

The archive includes data from three sources:
- House Oversight Committee releases
- Department of Justice releases
- Yahoo account releases

All data is public domain (US government records), served as static Parquet files from Cloudflare R2.

## License

AGPL-3.0. See [LICENSE](LICENSE) for details.
