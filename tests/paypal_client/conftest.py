from collections.abc import Callable, Iterator

import httpx
import pytest

from paypal_client import PayPalClient


@pytest.fixture
def client_factory() -> Iterator[Callable[..., PayPalClient]]:
    transports: list[httpx.Client] = []

    def build(handler: Callable[[httpx.Request], httpx.Response]) -> PayPalClient:
        transport = httpx.Client(transport=httpx.MockTransport(handler))
        transports.append(transport)
        return PayPalClient("test-client", "test-secret", client=transport)

    yield build
    for transport in transports:
        transport.close()


@pytest.fixture
def recorded_client(
    client_factory: Callable[..., PayPalClient],
) -> tuple[PayPalClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "test-token", "expires_in": 3600}
            )
        return httpx.Response(200, json={"id": "result-id"})

    return client_factory(handle), requests
