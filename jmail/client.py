"""
Core JmailClient class for accessing the Jmail Data API.

Provides easy access to all datasets from the Jeffrey Epstein email archive
with built-in ETag caching, parquet and NDJSON support, and optional
document full-text shard concatenation.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://data.jmail.world/v1"
CACHE_DIR = Path.home() / ".cache" / "jmail"

DATASET_NAMES = [
    "emails",
    "emails-slim",
    "documents",
    "photos",
    "people",
    "photo_faces",
    "star_counts",
    "release_batches",
    "imessage_conversations",
    "imessage_messages",
]

# Document full-text shards (too large for a single file)
DOC_FULL_TEXT_SHARDS = [
    "documents-full/VOL00008",
    "documents-full/VOL00009",
    "documents-full/VOL00010",
    "documents-full/DataSet11",
    "documents-full/other",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class JmailError(Exception):
    """Base exception for all jmail errors."""


class JmailDownloadError(JmailError):
    """Raised when a dataset download fails."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class JmailClient:
    """Client for the Jmail Data API.

    Args:
        cache:  If True (default), downloads are cached to ~/.cache/jmail/
                using ETag-based conditional requests. Set to False to always
                download fresh.
        cache_dir: Override the default cache directory.

    Example:
        from jmail import JmailClient

        client = JmailClient()
        df = client.emails(slim=True)
        print(df.head())
    """

    def __init__(
        self,
        cache: bool = True,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.cache = cache
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        if self.cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def url(self, dataset: str, fmt: str = "parquet") -> str:
        """Get the direct URL for a dataset.

        Args:
            dataset: Dataset name, e.g. ``"emails"``, ``"emails-slim"``,
                     ``"photos"``, ``"people"``, etc.
            fmt:    File format: ``"parquet"`` (default) or ``"ndjson.gz"``.

        Returns:
            Full HTTPS URL string.

        Example:
            client.url("emails-slim")
            # -> "https://data.jmail.world/v1/emails-slim.parquet"

            client.url("emails-slim", fmt="ndjson.gz")
            # -> "https://data.jmail.world/v1/emails-slim.ndjson.gz"
        """
        if fmt == "parquet":
            return f"{BASE_URL}/{dataset}.parquet"
        return f"{BASE_URL}/{dataset}.{fmt}"

    def urls(self) -> dict[str, dict[str, str]]:
        """Get all dataset URLs (parquet + NDJSON).

        Returns:
            Dict mapping dataset name to ``{"parquet": url, "ndjson.gz": url}``.
        """
        result: dict[str, dict[str, str]] = {}
        for name in DATASET_NAMES:
            result[name] = {
                "parquet": self.url(name, "parquet"),
                "ndjson.gz": self.url(name, "ndjson.gz"),
            }
        for shard in DOC_FULL_TEXT_SHARDS:
            result[shard] = {
                "parquet": self.url(shard, "parquet"),
                "ndjson.gz": self.url(shard, "ndjson.gz"),
            }
        return result

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def manifest(self) -> dict[str, Any]:
        """Fetch the manifest JSON with dataset metadata and checksums.

        Returns:
            Parsed manifest dict with keys like ``datasets``, ``version``,
            ``generated_at``, etc.
        """
        resp = requests.get(f"{BASE_URL}/manifest.json")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Download engine (with ETag caching)
    # ------------------------------------------------------------------

    def _download(self, dataset: str, fmt: str = "parquet") -> bytes:
        """Download a dataset file, using ETag cache when enabled.

        Returns the raw file bytes (or path to cached file when cache=True).
        """
        target_url = self.url(dataset, fmt)

        if fmt == "parquet":
            cache_file = self.cache_dir / f"{dataset.replace('/', '_')}.parquet"
            etag_file = self.cache_dir / f"{dataset.replace('/', '_')}.parquet.etag"
        else:
            cache_file = self.cache_dir / f"{dataset.replace('/', '_')}.{fmt}"
            etag_file = self.cache_dir / f"{dataset.replace('/', '_')}.{fmt}.etag"

        headers: dict[str, str] = {}

        if self.cache and cache_file.exists() and etag_file.exists():
            etag = etag_file.read_text().strip()
            if etag:
                headers["If-None-Match"] = etag

        resp = requests.get(target_url, headers=headers, stream=True)

        if resp.status_code == 304 and self.cache:
            # Cache is still fresh
            return cache_file.read_bytes()

        resp.raise_for_status()

        if self.cache:
            cache_file.write_bytes(resp.content)
            etag = resp.headers.get("ETag", "")
            if etag:
                etag_file.write_text(etag)
            return cache_file.read_bytes()

        return resp.content

    def _download_to_file(self, dataset: str, fmt: str = "parquet") -> Path:
        """Download a dataset and return the local file path.

        When caching is enabled, the file is stored in the cache directory
        and ETag conditional requests are used on subsequent calls.
        When caching is disabled, a temporary file is created.
        """
        if self.cache:
            self._download(dataset, fmt)
            if fmt == "parquet":
                return self.cache_dir / f"{dataset.replace('/', '_')}.parquet"
            return self.cache_dir / f"{dataset.replace('/', '_')}.{fmt}"

        # No cache: download to a temp file
        import tempfile

        raw = self._download(dataset, fmt)
        suffix = f".{fmt}" if fmt != "parquet" else ".parquet"
        tf = tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="jmail_"
        )
        tf.write(raw)
        tf.close()
        return Path(tf.name)

    # ------------------------------------------------------------------
    # Dataset accessors
    # ------------------------------------------------------------------

    def emails(
        self, slim: bool = False, head: int | None = None
    ) -> pd.DataFrame:
        """Load the emails dataset.

        Args:
            slim: If True, load the network-only view (no body text). Much
                  smaller download, ideal for sender/recipient graphs and
                  timeline visualizations.
            head: If given, only load the first N rows.

        Returns:
            A pandas DataFrame.

        Example:
            client = JmailClient()

            # Full emails with body text
            df = client.emails()

            # Network-only (slim)
            df = client.emails(slim=True)

            # First 100 emails
            df = client.emails(head=100)
        """
        dataset = "emails-slim" if slim else "emails"
        path = self._download_to_file(dataset)
        df = pd.read_parquet(path)
        if head is not None:
            df = df.head(head)
        return df

    def documents(
        self, include_text: bool = False, head: int | None = None
    ) -> pd.DataFrame:
        """Load the documents dataset.

        Args:
            include_text: If True, also download and concatenate the full
                          extracted text shards. This is a large download.
            head: If given, only load the first N rows (applied after
                  concatenation).

        Returns:
            A pandas DataFrame.

        Example:
            client = JmailClient()

            # Document metadata only (small)
            docs = client.documents()

            # Document metadata + full text (large download)
            docs = client.documents(include_text=True)
        """
        path = self._download_to_file("documents")
        df = pd.read_parquet(path)

        if include_text:
            shards_df = self._download_doc_text_shards()
            df = df.merge(shards_df, on="id", how="left")

        if head is not None:
            df = df.head(head)
        return df

    def _download_doc_text_shards(self) -> pd.DataFrame:
        """Download and concatenate all document full-text shards."""
        frames: list[pd.DataFrame] = []
        for shard in DOC_FULL_TEXT_SHARDS:
            path = self._download_to_file(shard)
            frames.append(pd.read_parquet(path))
        return pd.concat(frames, ignore_index=True)

    def photos(self, head: int | None = None) -> pd.DataFrame:
        """Load the photos dataset (18K rows, ~1 MB).

        Args:
            head: If given, only load the first N rows.

        Returns:
            A pandas DataFrame with photo metadata and AI descriptions.
        """
        path = self._download_to_file("photos")
        df = pd.read_parquet(path)
        if head is not None:
            df = df.head(head)
        return df

    def people(self, head: int | None = None) -> pd.DataFrame:
        """Load the people dataset (473 rows, <100 KB).

        People identified via AWS Rekognition facial recognition.

        Args:
            head: If given, only load the first N rows.

        Returns:
            A pandas DataFrame.
        """
        path = self._download_to_file("people")
        df = pd.read_parquet(path)
        if head is not None:
            df = df.head(head)
        return df

    def photo_faces(self, head: int | None = None) -> pd.DataFrame:
        """Load the photo_faces dataset (975 rows, <100 KB).

        Bounding boxes linking detected faces in photos to identified people.

        Args:
            head: If given, only load the first N rows.

        Returns:
            A pandas DataFrame.
        """
        path = self._download_to_file("photo_faces")
        df = pd.read_parquet(path)
        if head is not None:
            df = df.head(head)
        return df

    def star_counts(self, head: int | None = None) -> pd.DataFrame:
        """Load the star_counts dataset (414K rows, ~2 MB).

        Crowd-sourced star/interest counts from jmail.world users.

        Args:
            head: If given, only load the first N rows.

        Returns:
            A pandas DataFrame.
        """
        path = self._download_to_file("star_counts")
        df = pd.read_parquet(path)
        if head is not None:
            df = df.head(head)
        return df

    def release_batches(self, head: int | None = None) -> pd.DataFrame:
        """Load the release_batches dataset (11 rows, <10 KB).

        Metadata about each release batch.

        Args:
            head: If given, only load the first N rows.

        Returns:
            A pandas DataFrame.
        """
        path = self._download_to_file("release_batches")
        df = pd.read_parquet(path)
        if head is not None:
            df = df.head(head)
        return df

    def imessage_conversations(self, head: int | None = None) -> pd.DataFrame:
        """Load the imessage_conversations dataset (15 rows).

        Metadata for iMessage conversations recovered from the archive.

        Args:
            head: If given, only load the first N rows.

        Returns:
            A pandas DataFrame.
        """
        path = self._download_to_file("imessage_conversations")
        df = pd.read_parquet(path)
        if head is not None:
            df = df.head(head)
        return df

    def imessage_messages(self, head: int | None = None) -> pd.DataFrame:
        """Load the imessage_messages dataset (4,509 rows).

        Individual iMessage text messages with sender info and timestamps.

        Args:
            head: If given, only load the first N rows.

        Returns:
            A pandas DataFrame.
        """
        path = self._download_to_file("imessage_messages")
        df = pd.read_parquet(path)
        if head is not None:
            df = df.head(head)
        return df

    # ------------------------------------------------------------------
    # Convenience: search emails by sender
    # ------------------------------------------------------------------

    def search_by_sender(self, sender: str, slim: bool = True) -> pd.DataFrame:
        """Search emails by sender name/email (case-insensitive).

        Args:
            sender: Sender name or email to search for (substring match).
            slim: If True (default), use the slim dataset for speed.

        Returns:
            Filtered DataFrame of emails from that sender.

        Example:
            df = client.search_by_sender("ghislaine")
        """
        df = self.emails(slim=slim)
        mask = df["sender"].str.contains(sender, case=False, na=False)
        return df[mask]

    def search_by_subject(self, subject: str, slim: bool = True) -> pd.DataFrame:
        """Search emails by subject line (case-insensitive).

        Args:
            subject: Subject text to search for (substring match).
            slim: If True (default), use the slim dataset for speed.

        Returns:
            Filtered DataFrame of emails matching that subject.

        Example:
            df = client.search_by_subject("flight logs")
        """
        df = self.emails(slim=slim)
        mask = df["subject"].str.contains(subject, case=False, na=False)
        return df[mask]

    def top_senders(self, n: int = 20, slim: bool = True) -> pd.DataFrame:
        """Get the top N most frequent email senders.

        Args:
            n: Number of top senders to return (default 20).
            slim: If True (default), use the slim dataset for speed.

        Returns:
            DataFrame with columns ``sender`` and ``count``, sorted descending.

        Example:
            df = client.top_senders(n=10)
        """
        df = self.emails(slim=slim)
        counts = df["sender"].value_counts().head(n).reset_index()
        counts.columns = ["sender", "count"]
        return counts

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Delete all cached files from disk."""
        if self.cache_dir.exists():
            import shutil

            shutil.rmtree(self.cache_dir)
            if self.cache:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
