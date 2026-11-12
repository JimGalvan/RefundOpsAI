"""Host routing that preserves native synchronous and asynchronous request handling."""

from collections.abc import Callable
from typing import Any

from asgiref.sync import iscoroutinefunction
from django.http import HttpRequest
from django.utils.decorators import sync_and_async_middleware

from .data import WORLD

HOST_URLCONFS = {
    site["host"]: f"demo_sites.urls_{name}" for name, site in WORLD["sites"].items()
}


def route(request: HttpRequest) -> None:
    host = request.get_host().split(":", 1)[0].lower()
    if host in HOST_URLCONFS:
        request.urlconf = HOST_URLCONFS[host]


@sync_and_async_middleware
def HostRoutingMiddleware(get_response: Callable[..., Any]) -> Callable[..., Any]:  # noqa: N802
    if iscoroutinefunction(get_response):

        async def middleware(request: HttpRequest) -> Any:
            route(request)
            return await get_response(request)
    else:

        def middleware(request: HttpRequest) -> Any:
            route(request)
            return get_response(request)

    return middleware
