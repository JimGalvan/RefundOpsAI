"""Run the investigator on one case from the command line and print its log."""

import asyncio
import os
import sys

from django.core.management.base import BaseCommand

from console.hub import Hub
from console.investigation import run_investigation


class Command(BaseCommand):
    help = "Investigate a case with the browser agent (the app server must be running for the mock sites)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("case_id", type=int)
        parser.add_argument("--strong", help="Override REFUNDOPS_STRONG_MODEL for this run.")
        parser.add_argument("--fast", help="Override REFUNDOPS_FAST_MODEL for this run.")

    def handle(self, *args, **options) -> None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if options["strong"]:
            os.environ["REFUNDOPS_STRONG_MODEL"] = options["strong"]
        if options["fast"]:
            os.environ["REFUNDOPS_FAST_MODEL"] = options["fast"]
        investigation_id = asyncio.run(
            run_investigation(options["case_id"], Hub(), echo=lambda line: self.stdout.write(line))
        )
        self.stdout.write(f"investigation {investigation_id} finished")
