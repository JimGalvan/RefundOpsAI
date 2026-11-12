import json

from playwright.async_api import Browser, Playwright, async_playwright

from navgent.browser.constants import (
    BROWSER_LAUNCH_ARGS,
    LAUNCH_OPTIONS_HEADER,
    REMOTE_BROWSER_LAUNCH_ARGS,
    REMOTE_CONNECT_TIMEOUT_MS,
)


class BrowserManager:
    """Owns the Playwright driver and the browser, launched here or reached on a Playwright server."""

    def __init__(
            self,
            headless: bool = True,
            channel: str | None = None,
            ws_endpoint: str | None = None,
            allowed_hosts: tuple[str, ...] = (),
            asset_hosts: tuple[str, ...] = (),
    ):
        self._headless = headless
        self._channel = channel
        self._ws_endpoint = ws_endpoint
        self._allowed_hosts = allowed_hosts
        self._asset_hosts = asset_hosts
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self) -> Browser:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            if self._ws_endpoint:
                self._browser = await self._connect()
            else:
                self._browser = await self._launch()
        return self._browser

    async def _launch(self) -> Browser:
        return await self._playwright.chromium.launch(
            headless=self._headless,
            channel=self._channel,
            args=build_launch_args(self._allowed_hosts, self._asset_hosts),
        )

    async def _connect(self) -> Browser:
        # The server only honors client args when started with --unsafe; the sandbox's network rules are the real limit.
        launch_options = {
            "headless": True,
            "args": build_launch_args(self._allowed_hosts, self._asset_hosts) + REMOTE_BROWSER_LAUNCH_ARGS,
        }
        return await self._playwright.chromium.connect(
            self._ws_endpoint,
            headers={LAUNCH_OPTIONS_HEADER: json.dumps(launch_options)},
            timeout=REMOTE_CONNECT_TIMEOUT_MS,
        )

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


def build_launch_args(allowed_hosts: tuple[str, ...], asset_hosts: tuple[str, ...] = ()) -> list[str]:
    args = list(BROWSER_LAUNCH_ARGS)
    if allowed_hosts:
        args.append(build_host_resolver_rules(allowed_hosts + asset_hosts))
    return args


def build_host_resolver_rules(allowed_hosts: tuple[str, ...]) -> str:
    rules = ["MAP * ~NOTFOUND"]
    for host in allowed_hosts:
        rules.append(f"EXCLUDE {host}")
    return "--host-resolver-rules=" + ", ".join(rules)
