"""
jmail - Python client for the Jmail Data API

A simple, batteries-included Python library for accessing the Jeffrey Epstein
email archive via the Jmail Data API (https://jmail.world/docs/).

No API keys, no rate limits, no authentication. Just data.

Basic usage:
    from jmail import JmailClient

    client = JmailClient()
    df = client.emails(slim=True)
    print(df.head())
"""

__version__ = "0.1.0"
__all__ = ["JmailClient"]

from jmail.client import JmailClient
