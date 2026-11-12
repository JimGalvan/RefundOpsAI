import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from playwright.async_api import Page, ScreencastFrame

logger = logging.getLogger(__name__)


def wall_time_ms() -> float:
    return time.time() * 1000


@dataclass(frozen=True)
class Recording:
    """A finished tab recording and the wall-clock time of its first video frame.

    WebM time 0 is the first frame, so `wall_time_ms - start_wall_time_ms` is the playback position.
    """

    id: str
    path: Path
    width: int
    height: int
    start_wall_time_ms: float | None

    def recording_time_ms(self, wall_time_ms: float) -> float | None:
        if self.start_wall_time_ms is None or wall_time_ms < self.start_wall_time_ms:
            return None
        return wall_time_ms - self.start_wall_time_ms


@dataclass(frozen=True)
class PerformedClick:
    x: float
    y: float
    viewport_width: int
    viewport_height: int
    wall_time_ms: float
    recording_id: str | None
    recording_time_ms: float | None
    id: str = field(default_factory=lambda: uuid4().hex)

    def event_payload(self, action: str, brief_step: int | None) -> dict:
        return {
            "click_id": self.id,
            "action": action,
            "brief_step": brief_step,
            "recording_id": self.recording_id,
            "x": self.x,
            "y": self.y,
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
            "wall_time_ms": self.wall_time_ms,
            "recording_time_ms": self.recording_time_ms,
        }


class PageRecorder:
    """Records one tab with Playwright's screencast and remembers when its first frame was drawn."""

    def __init__(self, page: Page, directory: Path, width: int, height: int):
        self.page = page
        self.id = uuid4().hex
        self.path = directory / f"{self.id}.webm"
        self.width = width
        self.height = height
        self.start_wall_time_ms: float | None = None
        self._started = False

    async def start(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            await self.page.screencast.start(
                path=self.path,
                on_frame=self._remember_frame,
                size={"width": self.width, "height": self.height},
            )
            self._started = True
        except Exception:
            logger.warning("Could not start recording a tab", exc_info=True)

    def _remember_frame(self, frame: ScreencastFrame) -> None:
        if self.start_wall_time_ms is None:
            self.start_wall_time_ms = frame["timestamp"]

    def recording_time_ms(self, wall_time_ms: float) -> float | None:
        return self.describe().recording_time_ms(wall_time_ms)

    def describe(self) -> Recording:
        return Recording(self.id, self.path, self.width, self.height, self.start_wall_time_ms)

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        try:
            if self.page.is_closed():
                await self._save_after_page_closed()
            else:
                await self.page.screencast.stop()
        except Exception:
            logger.warning("Could not save the recording of a tab", exc_info=True)

    async def _save_after_page_closed(self) -> None:
        # Screencast.stop() needs a live page, but the video artifact outlives a tab the site closed itself.
        artifact = self.page.screencast._impl_obj._artifact
        if artifact is not None:
            await artifact.save_as(self.path)
