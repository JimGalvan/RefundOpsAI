from dataclasses import dataclass
from urllib.parse import urlsplit

BLOCK_ERRORS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_CONNECTION_REFUSED",
    "ERR_ADDRESS_UNREACHABLE",
)
MAX_URL_LENGTH = 300


@dataclass(frozen=True)
class BlockedRequest:
    host: str
    url: str
    error: str
    brief_step: int | None


class RequestBlockTracker:
    """Picks out failed requests to hosts outside the allowlist, once per host."""

    def __init__(self, allowed_hosts: tuple[str, ...]):
        self._allowed_hosts = set()
        for host in allowed_hosts:
            self._allowed_hosts.add(host.lower())
        self._reported_hosts: set[str] = set()

    def check(self, url: str, failure: str | None, brief_step: int | None) -> BlockedRequest | None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host or host in self._allowed_hosts or host in self._reported_hosts:
            return None
        error = find_block_error(failure or "")
        if error is None:
            return None
        self._reported_hosts.add(host)
        short_url = f"{parts.scheme}://{parts.netloc}{parts.path}"[:MAX_URL_LENGTH]
        return BlockedRequest(host, short_url, error, brief_step)


def find_block_error(failure: str) -> str | None:
    for error in BLOCK_ERRORS:
        if error in failure:
            return error
    return None
