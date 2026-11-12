"""Host-based URL routing for the mock merchant websites (implemented in task C2).

portal.localhost, tracking.localhost, inbox.localhost and store.localhost must behave like four
separate websites served by this one Django process. This stub passes every request through.
"""

from asgiref.sync import iscoroutinefunction
from django.utils.decorators import sync_and_async_middleware


@sync_and_async_middleware
def HostRoutingMiddleware(get_response):  # noqa: N802 - referenced by dotted path in settings
    if iscoroutinefunction(get_response):

        async def middleware(request):
            return await get_response(request)

    else:

        def middleware(request):
            return get_response(request)

    return middleware
