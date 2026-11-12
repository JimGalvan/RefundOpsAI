"""Export a case's recorded investigation for the offline demo."""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from cases.models import Case, Investigation
from console.replay import export_replay


class Command(BaseCommand):
    help = "Export a completed investigation and screenshots for offline replay."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("case_id", type=int)
        parser.add_argument("key")
        parser.add_argument("--investigation", type=int)

    def handle(self, *args: Any, **options: Any) -> None:
        if options["key"] not in {"A", "B"}:
            raise CommandError("Replay key must be A or B.")
        case = Case.objects.filter(pk=options["case_id"]).first()
        if case is None:
            raise CommandError("The case does not exist.")
        investigations = case.investigations.all()
        if options["investigation"] is not None:
            investigation = investigations.filter(pk=options["investigation"]).first()
            if investigation is None:
                raise CommandError("The investigation does not exist for this case.")
        else:
            investigation = investigations.filter(
                status=Investigation.Status.COMPLETED, recommendations__isnull=False,
            ).first()
            if investigation is None:
                raise CommandError("No completed investigation with a recommendation was found.")
        try:
            path = export_replay(investigation, options["key"])
        except ValueError as error:
            raise CommandError(f"{error}.") from error
        timeline = json.loads(path.read_text(encoding="utf-8"))["timeline"]
        events = sum(item["kind"] == "event" for item in timeline)
        evidence = [item for item in timeline if item["kind"] == "evidence"]
        screenshots = sum(bool(item["screenshot"]) for item in evidence)
        self.stdout.write(f"{path}: {events} events, {len(evidence)} evidence items, {screenshots} screenshots")
