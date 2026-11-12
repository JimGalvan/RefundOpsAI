"""Hostname rules for credential allowlists.

An entry is an exact hostname unless it carries a ``*.`` prefix, which also
covers subdomains. Hosts with a leading ``www.`` remain distinct.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

WILDCARD_PREFIX = "*."

# Suffixes where anybody can host a site, so a wildcard over one would cover
# sites other people control. Curated rather than the full Public Suffix List.
_SHARED_SUFFIXES = frozenset(
    {
        "amazonaws.com",
        "appspot.com",
        "azurewebsites.net",
        "blogspot.com",
        "cloudfront.net",
        "firebaseapp.com",
        "github.io",
        "gitlab.io",
        "glitch.me",
        "herokuapp.com",
        "myshopify.com",
        "netlify.app",
        "pages.dev",
        "r2.dev",
        "repl.co",
        "s3.amazonaws.com",
        "squarespace.com",
        "surge.sh",
        "tumblr.com",
        "vercel.app",
        "web.app",
        "weebly.com",
        "wixsite.com",
        "wordpress.com",
        "workers.dev",
    }
)

_SHARED_COUNTRY_SUFFIX = re.compile(
    r"^(?:co|com|org|net|edu|gov|ac|or|ne|go|gob|nom|ltd|plc)\.[a-z]{2}$"
)


def normalize_hostname(value: str) -> str:
    try:
        candidate = value.strip().lower()
        parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
        hostname = parsed.hostname
        if not hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError
        return hostname.rstrip(".").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        raise ValueError("Invalid credential domain") from None


def normalize_allowlist_entry(value: str) -> str:
    candidate = value.strip().lower()
    if not candidate.startswith(WILDCARD_PREFIX):
        return normalize_hostname(candidate)

    base = normalize_hostname(candidate[len(WILDCARD_PREFIX):])
    if base.count(".") < 1 and base != "localhost":
        raise ValueError(
            "Wildcard covers a whole top-level domain, which is too broad"
        )
    if base in _SHARED_SUFFIXES or _SHARED_COUNTRY_SUFFIX.match(base):
        raise ValueError(
            "Wildcard covers a shared hosting suffix, so it would include "
            "sites other people control"
        )
    return WILDCARD_PREFIX + base


def matches_allowlist(hostname: str, allowed_domains: Iterable[str]) -> bool:
    """Entries are normalized here too, so rows saved before these rules work."""
    for entry in allowed_domains:
        try:
            normalized_entry = normalize_allowlist_entry(entry)
        except ValueError:
            continue

        if normalized_entry.startswith(WILDCARD_PREFIX):
            base = normalized_entry[len(WILDCARD_PREFIX):]
            if hostname == base or hostname.endswith(f".{base}"):
                return True
        elif hostname == normalized_entry:
            return True
    return False


def suggest_allowlist_entries(hostname: str) -> tuple[str, str]:
    """For login.example.com the useful wildcard is *.example.com."""
    labels = hostname.split(".")
    parent = ".".join(labels[1:]) if len(labels) > 2 else hostname
    return hostname, f"{WILDCARD_PREFIX}{parent}"
