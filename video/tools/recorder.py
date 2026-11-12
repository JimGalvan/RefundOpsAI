"""Record Chrome CDP screencast frames; build a constant-frame-rate MP4."""

import argparse
import base64
import json
import subprocess
import time
from pathlib import Path

from common import SCALE, concat_file, media_path, run


def record(out: Path, cdp_url: str, page_index: int, quality: int) -> None:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    frames = media_path(out / "frames", "footage")
    frames.mkdir(exist_ok=True)
    log_path = out / "frames.jsonl"
    if log_path.exists():
        raise ValueError("Take already exists; choose a new take directory")
    stop = out / "STOP"
    stop.unlink(missing_ok=True)
    count = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        pages = [page for context in browser.contexts for page in context.pages]
        if not 0 <= page_index < len(pages):
            raise ValueError("Page index does not exist")
        page = pages[page_index]
        session = page.context.new_cdp_session(page)
        started = time.monotonic()
        with log_path.open("x", encoding="utf-8") as log:
            def on_frame(event: dict) -> None:
                nonlocal count
                moment = time.monotonic() - started
                name = f"{count:08d}.jpg"
                media_path(frames / name, "footage").write_bytes(base64.b64decode(event["data"]))
                log.write(json.dumps({"file": name, "time": moment}) + "\n")
                log.flush()
                count += 1
                session.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

            session.on("Page.screencastFrame", on_frame)
            session.send("Page.startScreencast", {
                "format": "jpeg", "quality": quality, "maxWidth": 1920,
                "maxHeight": 1080, "everyNthFrame": 1,
            })
            print(f"Recording. Create {stop} or press Ctrl+C to stop.", flush=True)
            try:
                while not stop.exists():
                    page.wait_for_timeout(200)
            except KeyboardInterrupt:
                pass
            finally:
                session.send("Page.stopScreencast")
                log.write(json.dumps({"file": None, "time": time.monotonic() - started}) + "\n")
                session.detach()
        # Playwright disconnects from the existing browser when its context exits.
    print(f"Saved {count} frames")


def build(out: Path, target: Path) -> None:
    entries = [json.loads(line) for line in (out / "frames.jsonl").read_text().splitlines()]
    if len(entries) < 2 or entries[-1]["file"] is not None:
        raise ValueError("No complete recording; stop the recorder first")
    listing = ""
    last = None
    previous = 0.0
    for index, entry in enumerate(entries[:-1]):
        moment = float(entry["time"])
        end = float(entries[index + 1]["time"])
        if moment < previous or end < moment:
            raise ValueError("Frame timestamps must increase")
        last = media_path(out / "frames" / entry["file"], "footage")
        if not last.is_file():
            raise ValueError("Missing recorded frame")
        # Hold the first image through initial capture latency as well.
        seconds = end - (0 if index == 0 else moment)
        listing += concat_file(last) + f"duration {seconds:.9f}\n"
        previous = moment
    if last is None:
        raise ValueError("Recording contains no frames")
    listing += concat_file(last)
    list_path = media_path(out / "frames.txt", "footage")
    list_path.write_text(listing, encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(list_path),
         "-vf", f"{SCALE},fps=30", "-t", str(entries[-1]["time"]), "-fps_mode", "cfr",
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)])
    print(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("record")
    capture.add_argument("out_dir")
    capture.add_argument("--cdp-url", default="http://127.0.0.1:9333")
    capture.add_argument("--page-index", type=int, default=0)
    capture.add_argument("--quality", type=int, choices=range(1, 101), default=92, metavar="1-100")
    assemble = commands.add_parser("build")
    assemble.add_argument("out_dir")
    assemble.add_argument("out", help="MP4 under video/footage/")
    args = parser.parse_args()
    try:
        out = media_path(args.out_dir, "footage")
        if args.command == "record":
            record(out, args.cdp_url, args.page_index, args.quality)
        else:
            target = media_path(args.out, "footage")
            if target.suffix.lower() != ".mp4":
                raise ValueError("Build output must have an .mp4 extension")
            build(out, target)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
