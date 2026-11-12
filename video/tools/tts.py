"""Generate one ElevenLabs MP3 for each narration line."""

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from common import ROOT, media_path


def credentials() -> tuple[str, str]:
    names = ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")
    values = {name: os.environ.get(name, "").strip() for name in names}
    if not all(values.values()):
        from dotenv import dotenv_values

        fallback = dotenv_values(ROOT / ".env", interpolate=False)
        for name in names:
            values[name] = values[name] or (fallback.get(name) or "").strip()
    for name in names:
        if not values[name]:
            raise ValueError(f"{name} is missing; set it in the environment or repo-root .env")
    return values[names[0]], values[names[1]]


def synthesize(text: str, model: str, key: str, voice: str) -> bytes:
    voice = urllib.parse.quote(voice, safe="")
    request = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
        data=json.dumps({"text": text, "model_id": model}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise ValueError(f"ElevenLabs returned HTTP {error.code}; check credentials/model/quota") from None
    except urllib.error.URLError:
        raise ValueError("Could not reach ElevenLabs") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lines", type=Path, help="JSON array of {id, text} objects")
    parser.add_argument("out_dir", nargs="?", default="video/voice")
    parser.add_argument("--model", default="eleven_multilingual_v2")
    parser.add_argument("--only", default="", help="comma-separated line IDs")
    args = parser.parse_args()
    try:
        out = media_path(args.out_dir, "voice")
        lines = json.loads(args.lines.read_text(encoding="utf-8"))
        if not isinstance(lines, list) or not lines:
            raise ValueError("Narration must be a nonempty JSON array")
        ids = set()
        for line in lines:
            identifier = line["id"]
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
                raise ValueError("Line IDs may contain only letters, digits, underscores and hyphens")
            if identifier in ids or not isinstance(line["text"], str) or not line["text"].strip():
                raise ValueError("Line IDs must be unique and text must be nonempty")
            ids.add(identifier)
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        if wanted - ids:
            raise ValueError("--only contains unknown line IDs")
        key, voice = credentials()
        out.mkdir(parents=True, exist_ok=True)
        for line in lines:
            if not wanted or line["id"] in wanted:
                audio = synthesize(line["text"], args.model, key, voice)
                path = media_path(out / f"{line['id']}.mp3", "voice")
                path.write_bytes(audio)
                print(path)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
