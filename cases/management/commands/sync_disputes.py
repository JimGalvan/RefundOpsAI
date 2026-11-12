"""Import or refresh PayPal sandbox disputes."""

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from cases.sync import SyncResult, sync_disputes, sync_one
from paypal_client import PayPalClient, PayPalError


class Command(BaseCommand):
    help = "Import or refresh PayPal disputes without changing case workflow state."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--state", default=None)
        parser.add_argument("--dispute-id", default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        client = PayPalClient.from_env()
        try:
            if options["dispute_id"]:
                case, created = sync_one(client, options["dispute_id"])
                result = SyncResult(
                    created=[case.pk] if created else [],
                    updated=[] if created else [case.pk],
                )
            else:
                result = sync_disputes(client, dispute_state=options["state"])
        except PayPalError as exc:
            prefix = f"{options['dispute_id']}: " if options["dispute_id"] else ""
            result = SyncResult(errors=[f"{prefix}{exc}"])
        finally:
            client.close()
        self.stdout.write(f"created {len(result.created)}, updated {len(result.updated)}")
        for error in result.errors:
            self.stderr.write(error)
        if result.errors:
            raise CommandError("PayPal dispute sync had errors", returncode=1)
