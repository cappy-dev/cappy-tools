#!/usr/bin/env python3
"""CLI for the Jmail Data API client.

Usage:
    jmail <command> [options]

Commands:
    manifest          Print manifest JSON
    emails            Download emails (--slim for network-only, --head N)
    documents         Download documents (--include-text for full text, --head N)
    photos            Download photos metadata (--head N)
    people            Download people (--head N)
    photo_faces       Download photo face data (--head N)
    imessage_conversations  Download iMessage conversations (--head N)
    imessage_messages       Download iMessage messages (--head N)
    star_counts       Download star counts (--head N)
    release_batches   Download release batches (--head N)
    urls              Print all dataset URLs
    duckdb-examples   Print example DuckDB SQL queries

Options:
    --head N          Show first N rows
    --slim            (emails) Omit body text columns
    --include-text    (documents) Include full extracted text
    --no-cache        Skip local caching, always download fresh
"""

from __future__ import annotations

import argparse
import json
import sys

from . import JmailClient


def _build_client(args: argparse.Namespace) -> JmailClient:
    return JmailClient(cache=not args.no_cache)


def _print_df(df, head: int | None = None) -> None:
    if head is not None:
        df = df.head(head)
    # Use to_string for clean terminal output
    print(df.to_string())


_DUCKDB_EXAMPLES = """-- Top 20 senders by email count
SELECT sender, COUNT(*) AS n
FROM read_parquet('https://data.jmail.world/v1/emails-slim.parquet')
GROUP BY sender ORDER BY n DESC LIMIT 20;

-- Emails per month
SELECT date_trunc('month', sent_at) AS month, COUNT(*) AS n
FROM read_parquet('https://data.jmail.world/v1/emails-slim.parquet')
GROUP BY month ORDER BY month;

-- Most starred documents
SELECT entity_id, count
FROM read_parquet('https://data.jmail.world/v1/star_counts.parquet')
WHERE entity_type = 'document'
ORDER BY count DESC LIMIT 20;

-- People with most photo appearances
SELECT name, photo_count
FROM read_parquet('https://data.jmail.world/v1/people.parquet')
ORDER BY photo_count DESC;

-- Join emails to star counts (top starred emails)
SELECT e.sender, e.subject, s.count
FROM read_parquet('https://data.jmail.world/v1/emails-slim.parquet') e
JOIN read_parquet('https://data.jmail.world/v1/star_counts.parquet') s
  ON s.entity_type = 'email_message' AND s.entity_id = e.id
ORDER BY s.count DESC LIMIT 20;
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jmail",
        description="Jmail Data API client - access the Epstein email archive",
    )
    parser.add_argument(
        "command",
        choices=[
            "manifest",
            "emails",
            "documents",
            "photos",
            "people",
            "photo_faces",
            "imessage_conversations",
            "imessage_messages",
            "star_counts",
            "release_batches",
            "urls",
            "duckdb-examples",
        ],
    )
    parser.add_argument("--head", type=int, default=None, help="Show first N rows")
    parser.add_argument("--slim", action="store_true", help="(emails) Omit body text")
    parser.add_argument(
        "--include-text", action="store_true", help="(documents) Include full text"
    )
    parser.add_argument("--no-cache", action="store_true", help="Skip local caching")
    args = parser.parse_args()

    client = _build_client(args)

    if args.command == "manifest":
        m = client.manifest()
        print(json.dumps(m, indent=2))

    elif args.command == "emails":
        df = client.emails(slim=args.slim, head=args.head)
        _print_df(df)

    elif args.command == "documents":
        df = client.documents(include_text=args.include_text, head=args.head)
        _print_df(df)

    elif args.command == "photos":
        _print_df(client.photos(head=args.head))

    elif args.command == "people":
        _print_df(client.people(head=args.head))

    elif args.command == "photo_faces":
        _print_df(client.photo_faces(head=args.head))

    elif args.command == "imessage_conversations":
        _print_df(client.imessage_conversations(head=args.head))

    elif args.command == "imessage_messages":
        _print_df(client.imessage_messages(head=args.head))

    elif args.command == "star_counts":
        _print_df(client.star_counts(head=args.head))

    elif args.command == "release_batches":
        _print_df(client.release_batches(head=args.head))

    elif args.command == "urls":
        urls = client.urls()
        for name, formats in urls.items():
            for fmt, url in formats.items():
                print(f"{name}\t{fmt}\t{url}")

    elif args.command == "duckdb-examples":
        print(_DUCKDB_EXAMPLES)


if __name__ == "__main__":
    main()
