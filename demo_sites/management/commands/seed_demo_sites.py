from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from demo_sites.data import seed_path, seed_time


class Command(BaseCommand):
    help = (
        "Persist the shared demo-site UTC date anchor; --reset starts a new demo day."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--reset", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        anchor = seed_time(reset=options["reset"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Demo sites seeded at {anchor.isoformat()} ({seed_path()})"
            )
        )
