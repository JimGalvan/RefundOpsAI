"""Run the investigator on one case from the command line and print its log."""

import asyncio
import os
import sys

from django.core.management.base import BaseCommand

from cases.models import Recommendation
from console.hub import Hub
from console.investigation import run_investigation
from console.recommend import generate_recommendation


class Command(BaseCommand):
    help = "Investigate a case with the browser agent (the app server must be running for the mock sites)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("case_id", type=int)
        parser.add_argument("--strong", help="Override REFUNDOPS_STRONG_MODEL for this run.")
        parser.add_argument("--fast", help="Override REFUNDOPS_FAST_MODEL for this run.")
        parser.add_argument("--no-recommendation", action="store_true", help="Stop after the evidence.")

    def handle(self, *args, **options) -> None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if options["strong"]:
            os.environ["REFUNDOPS_STRONG_MODEL"] = options["strong"]
        if options["fast"]:
            os.environ["REFUNDOPS_FAST_MODEL"] = options["fast"]
        investigation_id = asyncio.run(
            run_investigation(
                options["case_id"],
                Hub(),
                after_success=None if options["no_recommendation"] else generate_recommendation,
                echo=lambda line: self.stdout.write(line),
            )
        )
        self.stdout.write(f"investigation {investigation_id} finished")
        recommendation = Recommendation.objects.filter(investigation_id=investigation_id).first()
        if recommendation:
            self.stdout.write(
                f"RECOMMENDATION {recommendation.action} {recommendation.amount or ''} "
                f"({recommendation.confidence}): {recommendation.headline}\n{recommendation.rationale}\n"
                f"citations {recommendation.citations}; guardrails ok={recommendation.guardrails.get('ok')} "
                f"{recommendation.guardrails.get('violations')}"
            )
