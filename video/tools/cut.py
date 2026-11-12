"""Assemble a JSON edit list into a 1080p H.264/AAC demo."""

import argparse
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from common import SCALE, VIDEO, concat_file, duration, media_path, number, run


def validate(edit: object) -> list[dict]:
    if not isinstance(edit, list) or not edit:
        raise ValueError("Edit list must be a nonempty JSON array")
    segments = []
    for index, item in enumerate(edit, start=1):
        clip = media_path(item["clip"], "footage")
        start = number(item.get("start", 0), "start")
        end = number(item["end"], "end")
        speed = number(item.get("speed", 1), "speed", 0.01)
        if end <= start or end > duration(clip) + 0.05:
            raise ValueError(f"Segment {index}: end must follow start and fit inside clip")
        # Quantize every segment to a whole number of output frames.
        length = math.ceil((end - start) / speed * 30 - 1e-8) / 30
        narration = media_path(item["narration"], "voice") if item.get("narration") else None
        if narration and duration(narration) > length + 0.02:
            raise ValueError(f"Segment {index}: narration exceeds {length:.2f}s; extend the segment")
        label = item.get("label", "")
        if not isinstance(label, str):
            raise ValueError("label must be text")
        if speed != 1:
            label = f"{speed:g}x" + (f" | {label}" if label else "")
        segments.append({"clip": clip, "start": start, "end": end, "speed": speed,
                         "length": length, "narration": narration, "label": label})
    return segments


def render(segment: dict, target: Path, work: Path) -> None:
    command = ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(segment["start"]),
               "-t", str(segment["end"] - segment["start"]), "-i", str(segment["clip"])]
    if segment["narration"]:
        command += ["-i", str(segment["narration"])]
    else:
        command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    filters = f"setpts=(PTS-STARTPTS)/{segment['speed']},{SCALE},fps=30"
    filters += ",tpad=stop_mode=clone:stop_duration=0.1"
    if segment["label"]:
        # A text file with expansion disabled keeps punctuation out of filter syntax.
        label_path = work / "label.txt"
        label_path.write_text(segment["label"], encoding="utf-8")
        fonts = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf",
                 Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                 Path("/System/Library/Fonts/Supplemental/Arial.ttf")]
        font = next((path for path in fonts if path.is_file()), None)
        if font is None:
            raise ValueError("Labels require Arial or DejaVu Sans in the system font directory")
        shutil.copyfile(font, work / "label-font.ttf")
        filters += (",drawtext=fontfile=label-font.ttf:textfile=label.txt:expansion=none:fontsize=40:"
                    "fontcolor=white:box=1:boxcolor=black@0.8:boxborderw=14:x=w-tw-40:y=40")
    command += ["-map", "0:v:0", "-map", "1:a:0", "-vf", filters,
                "-af", "apad,aresample=48000", "-t", str(segment["length"]),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-r", "30", "-fps_mode", "cfr", "-c:a", "aac", "-b:a", "192k",
                "-ar", "48000", "-ac", "2", str(target)]
    subprocess.run(command, cwd=work, check=True)


def export(edit_path: Path, target: Path) -> None:
    segments = validate(json.loads(edit_path.read_text(encoding="utf-8")))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cut-", dir=target.parent) as temp:
        work = Path(temp)
        clips = []
        for index, segment in enumerate(segments):
            path = work / f"{index:03d}.mp4"
            render(segment, path, work)
            clips.append(path)
            print(f"Segment {index + 1}: {segment['length']:.2f}s", flush=True)
        listing = work / "clips.txt"
        listing.write_text("".join(concat_file(path) for path in clips), encoding="utf-8")
        # Concatenate decoded audio to avoid AAC encoder padding at each join.
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-t", str(sum(segment["length"] for segment in segments)),
             "-movflags", "+faststart", str(target)])
    seconds = duration(target)
    print(f"{target}\nFinal duration: {seconds:.2f}s ({int(seconds // 60)}:{seconds % 60:05.2f})")
    if seconds >= 180:
        print("WARNING: duration is 3:00 or more; shorten before upload.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    output = commands.add_parser("export")
    output.add_argument("edit", type=Path)
    output.add_argument("--out", default=str(VIDEO / "exports" / "demo.mp4"))
    args = parser.parse_args()
    try:
        target = media_path(args.out, "exports")
        if target.suffix.lower() != ".mp4":
            raise ValueError("Export must have an .mp4 extension")
        export(args.edit, target)
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
