"""Read-only demo world and a persistent shared UTC date anchor."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from django.conf import settings

WORLD: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parent.parent / "demo_data" / "cases.json").read_text(
        encoding="utf-8"
    )
)


def seed_path() -> Path:
    return Path(settings.DATA_DIR) / "demo_sites_seed.json"


def seed_time(*, reset: bool = False) -> datetime:
    path = seed_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    payload = json.dumps({"seed_time": value.isoformat()}) + "\n"
    if reset:
        path.write_text(payload, encoding="utf-8")
    else:
        try:
            with path.open("x", encoding="utf-8") as file:
                file.write(payload)
        except FileExistsError:
            pass
    return datetime.fromisoformat(
        json.loads(path.read_text(encoding="utf-8"))["seed_time"]
    )


def timestamp(days_ago: int, time: str = "00:00") -> datetime:
    hour, minute = map(int, time.split(":"))
    return (seed_time() - timedelta(days=days_ago)).replace(hour=hour, minute=minute)


def display_date(value: datetime) -> str:
    return f"{value:%b} {value.day}, {value.year} {value.hour % 12 or 12}:{value:%M %p}"


def messages() -> list[dict[str, Any]]:
    result = []
    for case in WORLD["cases"]:
        for position, message in enumerate(case["inbox"], start=1):
            received = timestamp(message["days_ago"], message["time"])
            result.append(
                {
                    "id": f"{case['key']}-{position}",
                    "sender": message["from"],
                    "recipient": WORLD["store"]["support_email"],
                    "subject": message["subject"],
                    "body": message["body"],
                    "paragraphs": message["body"].split("\n\n"),
                    "received": received,
                    "date": display_date(received),
                }
            )
    return sorted(result, key=lambda message: message["received"], reverse=True)
