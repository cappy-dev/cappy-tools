# Tests for the jmail-python library
# Uses real API calls (small datasets only) to verify everything works end to end.

import pytest
import pandas as pd

from jmail import JmailClient
from jmail.client import BASE_URL, DATASET_NAMES, DOC_FULL_TEXT_SHARDS


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


class TestUrls:
    def test_url_default_parquet(self):
        client = JmailClient()
        assert client.url("emails") == f"{BASE_URL}/emails.parquet"

    def test_url_ndjson(self):
        client = JmailClient()
        assert (
            client.url("emails-slim", fmt="ndjson.gz")
            == f"{BASE_URL}/emails-slim.ndjson.gz"
        )

    def test_url_sharded_path(self):
        client = JmailClient()
        assert (
            client.url("documents-full/VOL00009")
            == f"{BASE_URL}/documents-full/VOL00009.parquet"
        )

    def test_urls_returns_all_datasets(self):
        client = JmailClient()
        all_urls = client.urls()
        for name in DATASET_NAMES:
            assert name in all_urls
            assert "parquet" in all_urls[name]
            assert "ndjson.gz" in all_urls[name]
        for shard in DOC_FULL_TEXT_SHARDS:
            assert shard in all_urls

    def test_urls_format(self):
        client = JmailClient()
        all_urls = client.urls()
        assert all_urls["photos"]["parquet"].endswith(".parquet")
        assert all_urls["photos"]["ndjson.gz"].endswith(".ndjson.gz")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class TestManifest:
    def test_manifest_fetch(self):
        client = JmailClient()
        manifest = client.manifest()
        assert "version" in manifest
        assert "datasets" in manifest
        assert "emails" in manifest["datasets"]
        assert manifest["datasets"]["emails"]["record_count"] > 0

    def test_manifest_has_all_main_datasets(self):
        client = JmailClient()
        manifest = client.manifest()
        ds = manifest["datasets"]
        for name in ["emails", "emails-slim", "documents", "photos", "people"]:
            assert name in ds, f"Missing {name} in manifest"


# ---------------------------------------------------------------------------
# Small datasets (safe to download in tests)
# ---------------------------------------------------------------------------


class TestSmallDatasets:
    """Test datasets that are small enough to download in CI (<100 KB)."""

    def test_people(self):
        client = JmailClient()
        df = client.people()
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0
        assert "name" in df.columns
        assert "id" in df.columns

    def test_people_head(self):
        client = JmailClient()
        df = client.people(head=5)
        assert len(df) <= 5

    def test_photo_faces(self):
        client = JmailClient()
        df = client.photo_faces()
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0
        assert "photo_id" in df.columns
        assert "person_id" in df.columns

    def test_release_batches(self):
        client = JmailClient()
        df = client.release_batches()
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0
        assert "id" in df.columns
        assert "name" in df.columns

    def test_release_batches_head(self):
        client = JmailClient()
        df = client.release_batches(head=3)
        assert len(df) <= 3

    def test_imessage_conversations(self):
        client = JmailClient()
        df = client.imessage_conversations()
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0
        assert "slug" in df.columns
        assert "name" in df.columns

    def test_imessage_messages(self):
        client = JmailClient()
        df = client.imessage_messages()
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0
        assert "text" in df.columns
        assert "sender" in df.columns

    def test_imessage_messages_head(self):
        client = JmailClient()
        df = client.imessage_messages(head=10)
        assert len(df) <= 10

    def test_photos(self):
        client = JmailClient()
        df = client.photos()
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0
        assert "id" in df.columns

    def test_photos_head(self):
        client = JmailClient()
        df = client.photos(head=5)
        assert len(df) <= 5


# ---------------------------------------------------------------------------
# Medium datasets (using head to limit download)
# ---------------------------------------------------------------------------


class TestEmailsWithHead:
    """Test email loading with head= to limit the download."""

    def test_emails_slim_head(self):
        client = JmailClient()
        df = client.emails(slim=True, head=10)
        assert isinstance(df, pd.DataFrame)
        assert len(df) <= 10
        assert "sender" in df.columns


class TestDocuments:
    def test_documents_head(self):
        client = JmailClient()
        df = client.documents(head=10)
        assert isinstance(df, pd.DataFrame)
        assert len(df) <= 10
        assert "id" in df.columns


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_by_sender(self):
        client = JmailClient()
        df = client.search_by_sender("epstein", slim=True)
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0
        # Should find Epstein in the sender column
        assert df["sender"].str.contains("epstein", case=False).any()

    def test_search_by_subject(self):
        client = JmailClient()
        df = client.search_by_subject("re:", slim=True)
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_top_senders(self):
        client = JmailClient()
        top = client.top_senders(n=5)
        assert isinstance(top, pd.DataFrame)
        assert len(top) <= 5
        assert "sender" in top.columns
        assert "count" in top.columns


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class TestCaching:
    def test_cache_disabled(self):
        client = JmailClient(cache=False)
        df = client.release_batches()
        assert isinstance(df, pd.DataFrame)

    def test_custom_cache_dir(self, tmp_path):
        client = JmailClient(cache_dir=tmp_path)
        df = client.release_batches()
        assert isinstance(df, pd.DataFrame)
        # Check that cache files were created
        cache_files = list(tmp_path.glob("*.parquet"))
        assert len(cache_files) > 0

    def test_clear_cache(self, tmp_path):
        client = JmailClient(cache_dir=tmp_path)
        client.release_batches()
        assert tmp_path.exists()
        client.clear_cache()
        # Directory should be empty after clearing
        files = list(tmp_path.iterdir())
        assert len(files) == 0
