# /// script
# requires-python = ">=3.9"
# dependencies = ["pandas", "pyarrow", "requests"]
# ///
"""
JmailClient: Easy Python client for the Jmail Data API.

A simple, batteries-included library for accessing the Jeffrey Epstein
email archive from https://data.jmail.world/v1/.

Features:
    - One-method access to every dataset (emails, documents, photos, etc.)
    - ETag-based local caching to avoid re-downloading unchanged files
    - Returns pandas DataFrames for easy analysis
    - CLI included for quick exploration
    - Zero auth, zero rate limits, zero friction

Example:
    from jmail import JmailClient

    client = JmailClient()
    df = client.emails(slim=True)
    print(df.head())

    people = client.people()
    print(people[["name", "photo_count"]].head())
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import requests

__version__ = "1.0.0"

__all__ = ["JmailClient"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://data.jmail.world/v1"
MANIFEST_URL = f"{BASE_URL}/manifest.json"
CACHE_DIR = Path.home() / ".cache" / "jmail"

# Map of dataset name -> parquet filename (without extension)
_DATASET_FILES: dict[str, str] = {
    "emails": "emails",
    "emails-slim": "emails-slim",
    "documents": "documents",
    "photos": "photos",
    "people": "people",
    "photo_faces": "photo_faces",
    "imessage_conversations": "imessage_conversations",
    "imessage_messages": "imessage_messages",
    "star_counts": "star_counts",
    "release_batches": "release_batches",
}

# Document full-text shards (concatenated by documents(include_text=True))
_DOC_FULL_SHARDS = [
    "documents-full/VOL00008",
    "documents-full/VOL00009",
    "documents-full/VOL00010",
    "documents-full/DataSet11",
    "documents-full/other",
]


class JmailClient:
    """Client for the Jmail Data API.

    Args:
        cache:   If True (default), downloaded files are cached in
                 ~/.cache/jmail/ with ETag-based conditional requests.
                 If False, every call downloads fresh data.
        cache_dir: Override the cache directory (defaults to ~/.cache/jmail/).
        base_url: Override the base URL (defaults to https://data.jmail.world/v1).
    """

    def __init__(
        self,
        cache: bool = True,
        cache_dir: str | Path | None = None,
        base_url: str | None = None,
    ) -> None:
        self.cache = cache
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        if self.cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url or BASE_URL

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def url(self, dataset: str, fmt: str = "parquet") -> str:
        """Build the full URL for a dataset.

        Args:
            dataset: Dataset name (e.g. "emails", "emails-slim", "photos").
            fmt:     Format: "parquet" (default) or "ndjson.gz".

        Returns:
            Full URL string.

        Example:
            >>> client.url("emails-slim")
            'https://data.jmail.world/v1/emails-slim.parquet'
            >>> client.url("emails-slim", fmt="ndjson.gz")
            'https://data.jmail.world/v1/emails-slim.ndjson.gz'
        """
        return f"{self.base_url}/{dataset}.{fmt}"

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def manifest(self) -> dict[str, Any]:
        """Fetch the manifest JSON with dataset metadata and checksums.

        Returns:
            Parsed manifest dict with keys: version, run_id, generated_at,
            description, license, base_url, credits, datasets.
        """
        resp = requests.get(MANIFEST_URL, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Core download logic
    # ------------------------------------------------------------------

    def _download(self, dataset: str) -> Path:
        """Download a dataset Parquet file (with caching) and return its path."""
        filename = f"{dataset}.parquet"
        etag_filename = f"{dataset}.etag"
        file_path = self.cache_dir / filename
        etag_path = self.cache_dir / etag_filename
        url = self.url(dataset)

        # Check cache
        if self.cache and file_path.exists() and etag_path.exists():
            cached_etag = etag_path.read_text().strip()
            headers = {"If-None-Match": cached_etag}
        else:
            headers = {}

        resp = requests.get(url, headers=headers, timeout=300, stream=True)

        if resp.status_code == 304 and file_path.exists():
            # Cache is still fresh
            return file_path

        resp.raise_for_status()

        # Save downloaded file
        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1_048_576):
                if chunk:
                    f.write(chunk)

        # Save ETag for next time
        etag = resp.headers.get("ETag")
        if etag and self.cache:
            etag_path.write_text(etag)

        return file_path

    def _load_parquet(self, path: Path, head: int | None = None) -> pd.DataFrame:
        """Read a Parquet file into a DataFrame, optionally just the head."""
        if head is not None:
            df = pd.read_parquet(path)
            return df.head(head)
        return pd.read_parquet(path)

    def _dataset(self, dataset: str, head: int | None = None) -> pd.DataFrame:
        """Download + load a standard dataset into a DataFrame."""
        path = self._download(dataset)
        return self._load_parquet(path, head=head)

    # ------------------------------------------------------------------
    # Public dataset methods
    # ------------------------------------------------------------------

    def emails(
        self, slim: bool = False, head: int | None = None
    ) -> pd.DataFrame:
        """Get the emails dataset.

        Args:
            slim: If True, download the network-only view (no body text,
                  ~41 MB instead of ~334 MB). Good for graph analysis.
            head: If given, return only the first N rows.

        Returns:
            DataFrame with columns like: id, doc_id, sender, subject,
            to_recipients, cc_recipients, bcc_recipients, sent_at,
            account_email, email_drop_id, epstein_is_sender,
            and (if full) content_markdown, content_html, attachments.
        """
        dataset = "emails-slim" if slim else "emails"
        return self._dataset(dataset, head=head)

    def documents(
        self, include_text: bool = False, head: int | None = None
    ) -> pd.DataFrame:
        """Get the documents dataset.

        Args:
            include_text: If True, also download and concatenate the
                         full-text shard files (large download).
            head:         If given, return only the first N rows.

        Returns:
            DataFrame with columns: id, source, release_batch,
            original_filename, page_count, size, document_description,
            has_thumbnail. If include_text, also includes full_text.
        """
        df = self._dataset("documents", head=head)

        if include_text:
            shards = []
            for shard in _DOC_FULL_SHARDS:
                path = self._download(shard)
                shards.append(self._load_parquet(path))
            if shards:
                full_df = pd.concat(shards, ignore_index=True)
                df = pd.merge(df, full_df, on="id", how="left")

        return df

    def photos(self, head: int | None = None) -> pd.DataFrame:
        """Get photo metadata (18K photos, ~1 MB)."""
        return self._dataset("photos", head=head)

    def people(self, head: int | None = None) -> pd.DataFrame:
        """Get people identified via facial recognition (473 people, <100 KB)."""
        return self._dataset("people", head=head)

    def photo_faces(self, head: int | None = None) -> pd.DataFrame:
        """Get face bounding boxes linking photos to people (975 faces)."""
        return self._dataset("photo_faces", head=head)

    def imessage_conversations(self, head: int | None = None) -> pd.DataFrame:
        """Get iMessage conversation metadata (15 conversations)."""
        return self._dataset("imessage_conversations", head=head)

    def imessage_messages(self, head: int | None = None) -> pd.DataFrame:
        """Get individual iMessage text messages (~4.5K messages)."""
        return self._dataset("imessage_messages", head=head)

    def star_counts(self, head: int | None = None) -> pd.DataFrame:
        """Get crowd-sourced star/interest counts (~414K entries)."""
        return self._dataset("star_counts", head=head)

    def release_batches(self, head: int | None = None) -> pd.DataFrame:
        """Get release batch metadata (11 batches, <10 KB)."""
        return self._dataset("release_batches", head=head)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def urls(self) -> dict[str, dict[str, str]]:
        """Return all dataset URLs in both parquet and ndjson.gz formats.

        Returns:
            Dict mapping dataset name to {"parquet": url, "ndjson.gz": url}.
        """
        result = {}
        for name in _DATASET_FILES:
            result[name] = {
                "parquet": self.url(name),
                "ndjson.gz": self.url(name, fmt="ndjson.gz"),
            }
        for shard in _DOC_FULL_SHARDS:
            result[shard] = {
                "parquet": self.url(shard),
                "ndjson.gz": self.url(shard, fmt="ndjson.gz"),
            }
        return result

    def clear_cache(self) -> None:
        """Delete all cached files."""
        if self.cache_dir.exists():
            for f in self.cache_dir.iterdir():
                f.unlink()
