"""Server-rendered merchant tools independent of main-app accounts."""

from functools import wraps
from secrets import compare_digest
from typing import Any

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .data import WORLD, display_date, messages, timestamp


def page(
    request: HttpRequest, site: str, template: str, **context: Any
) -> HttpResponse:
    return render(
        request,
        f"demo_sites/{template}.html",
        {
            "site": site,
            "brand": WORLD["sites"][site]["brand"],
            "signed_in": request.session.get(f"{site}_user", False),
            **context,
        },
    )


def protected(site: str) -> Any:
    def decorate(view: Any) -> Any:
        @wraps(view)
        def gated(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not request.session.get(f"{site}_user"):
                return redirect("/login/")
            return view(request, *args, **kwargs)

        return gated

    return decorate


@require_GET
def tool_home(request: HttpRequest, site: str) -> HttpResponse:
    destination = "/orders/" if site == "portal" else "/inbox/"
    return redirect(destination if request.session.get(f"{site}_user") else "/login/")


@require_http_methods(["GET", "POST"])
def login(request: HttpRequest, site: str) -> HttpResponse:
    error = ""
    if request.method == "POST":
        credentials = WORLD["sites"][site]["login"]
        email_ok = compare_digest(
            request.POST.get("email", "").encode(), credentials["username"].encode()
        )
        password_ok = compare_digest(
            request.POST.get("password", "").encode(), credentials["password"].encode()
        )
        if email_ok & password_ok:
            request.session.cycle_key()
            request.session[f"{site}_user"] = credentials["username"]
            return redirect("/orders/" if site == "portal" else "/inbox/")
        error = "Email or password is incorrect. Please try again."
    return page(
        request, site, "login", error=error, email=request.POST.get("email", "")
    )


@require_POST
def logout(request: HttpRequest) -> HttpResponse:
    request.session.flush()
    return redirect("/login/")


@protected("portal")
@require_GET
def orders(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    matches = [
        case
        for case in WORLD["cases"]
        if query.casefold() in case["order_ref"].casefold()
    ]
    return page(request, "portal", "orders", orders=matches, query=query)


@protected("portal")
@require_GET
def order_detail(request: HttpRequest, order_ref: str) -> HttpResponse:
    case = next(
        (case for case in WORLD["cases"] if case["order_ref"] == order_ref), None
    )
    if case is None:
        raise Http404("Order not found")
    # get_host preserves an explicit port even when a proxy's SERVER_PORT differs.
    host = request.get_host()
    port = host.rsplit(":", 1)[1] if ":" in host else ""
    authority = WORLD["sites"]["tracking"]["host"] + (f":{port}" if port else "")
    tracking_url = f"{request.scheme}://{authority}/track/{case['fulfillment']['tracking_number']}/"
    return page(
        request,
        "portal",
        "order_detail",
        order=case,
        tracking_url=tracking_url,
        shipped=display_date(timestamp(case["fulfillment"]["shipped_days_ago"])),
    )


@require_GET
def tracking_home(request: HttpRequest) -> HttpResponse:
    number = request.GET.get("number", "").strip()
    if number:
        return redirect("tracking-detail", tracking_number=number)
    return page(request, "tracking", "tracking_home")


@require_GET
def tracking_detail(request: HttpRequest, tracking_number: str) -> HttpResponse:
    case = next(
        (
            case
            for case in WORLD["cases"]
            if case["fulfillment"]["tracking_number"] == tracking_number
        ),
        None,
    )
    if case is None:
        response = page(request, "tracking", "tracking_detail", number=tracking_number)
        response.status_code = 404
        return response
    events = sorted(
        case["tracking"]["events"],
        key=lambda event: timestamp(event["days_ago"], event["time"]),
        reverse=True,
    )
    events = [
        {**event, "date": display_date(timestamp(event["days_ago"], event["time"]))}
        for event in events
    ]
    return page(
        request,
        "tracking",
        "tracking_detail",
        order=case,
        number=tracking_number,
        events=events,
    )


@protected("inbox")
@require_GET
def inbox(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    matches = [
        message
        for message in messages()
        if query.casefold()
        in " ".join(message[key] for key in ("subject", "body", "sender")).casefold()
    ]
    return page(request, "inbox", "inbox", messages=matches, query=query)


@protected("inbox")
@require_GET
def message_detail(request: HttpRequest, message_id: str) -> HttpResponse:
    message = next(
        (message for message in messages() if message["id"] == message_id), None
    )
    if message is None:
        raise Http404("Message not found")
    return page(request, "inbox", "message", message=message)


@require_GET
def store(request: HttpRequest, section: str = "home") -> HttpResponse:
    return page(
        request,
        "store",
        f"store_{section}",
        store=WORLD["store"],
        products=[item for case in WORLD["cases"] for item in case["items"]],
    )
