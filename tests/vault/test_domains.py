import pytest

from vault.domains import (
    _SHARED_SUFFIXES,
    matches_allowlist,
    normalize_allowlist_entry,
    normalize_hostname,
    suggest_allowlist_entries,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" Example.COM. ", "example.com"),
        ("https://EXAMPLE.com/", "example.com"),
        ("example.com:443", "example.com"),
        ("BÜCHER.example", "xn--bcher-kva.example"),
        ("portal.localhost", "portal.localhost"),
    ],
)
def test_hostname_normalization(value: str, expected: str) -> None:
    assert normalize_hostname(value) == expected


@pytest.mark.parametrize("value", ["", "example.com/path", "example.com?a=b", "example.com#x", "https://", "[invalid"])
def test_invalid_hosts(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_hostname(value)


@pytest.mark.parametrize("base", [*_SHARED_SUFFIXES, "com", "dev", "co.uk", "com.au", "org.nz", "gov.uk"])
def test_overbroad_wildcards_rejected(base: str) -> None:
    with pytest.raises(ValueError):
        normalize_allowlist_entry(f"*.{base}")


def test_shared_suffix_exact_hosts_and_owned_subdomains_allowed() -> None:
    assert normalize_allowlist_entry("github.io") == "github.io"
    assert normalize_allowlist_entry("*.myshop.github.io") == "*.myshop.github.io"


def test_wildcard_idna_normalization() -> None:
    assert normalize_allowlist_entry("*.BÜCHER.example.") == "*.xn--bcher-kva.example"


@pytest.mark.parametrize(
    ("host", "entries", "expected"),
    [
        ("example.com", ["example.com"], True),
        ("www.example.com", ["example.com"], False),
        ("example.com", ["www.example.com"], False),
        ("example.com", ["*.example.com"], True),
        ("a.b.example.com", ["*.example.com"], True),
        ("evil-example.com", ["*.example.com"], False),
        ("example.com.evil.net", ["*.example.com"], False),
        ("example.com", ["*.com", "https://EXAMPLE.com/"], True),
        ("example.com", ["*.com"], False),
        ("portal.localhost", ["portal.localhost"], True),
        ("tracking.localhost", ["portal.localhost"], False),
        ("portal.localhost", ["*.localhost"], True),
        ("tracking.localhost", ["*.localhost"], True),
        ("a.portal.localhost", ["*.localhost"], True),
        ("portal.localhost.evil.com", ["*.localhost"], False),
        ("notlocalhost", ["*.localhost"], False),
    ],
)
def test_allowlist_matching(host: str, entries: list[str], expected: bool) -> None:
    assert matches_allowlist(host, entries) is expected


@pytest.mark.parametrize(
    ("host", "wildcard"),
    [("login.example.com", "*.example.com"), ("example.com", "*.example.com"), ("portal.localhost", "*.portal.localhost")],
)
def test_suggestions(host: str, wildcard: str) -> None:
    assert suggest_allowlist_entries(host) == (host, wildcard)
