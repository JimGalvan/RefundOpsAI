"""Prepare the demo world: mock-site seed date, saved logins in the vault, and (optionally) offline cases."""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from console.demo import seed_offline_cases
from demo_sites.data import WORLD
from investigator.sites import CREDENTIAL_NAMES
from vault.models import Credential
from vault.service import CredentialService


class Command(BaseCommand):
    help = "Seed the mock sites, the vault logins for them, and optionally offline demo cases."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--offline-cases", action="store_true", help="Also create the two demo cases without PayPal.")
        parser.add_argument("--reset", action="store_true", help="Rebuild offline cases and re-seed the site dates.")

    def handle(self, *args, **options) -> None:
        call_command("seed_demo_sites", *(["--reset"] if options["reset"] else []), stdout=self.stdout)
        service = CredentialService()
        for key, name in CREDENTIAL_NAMES.items():
            site = WORLD["sites"][key]
            if Credential.objects.filter(name=name).exists():
                self.stdout.write(f"vault: '{name}' already saved")
                continue
            service.create(
                name,
                [site["host"]],
                login=site["login"]["username"],
                password=site["login"]["password"],
            )
            self.stdout.write(f"vault: saved '{name}' for {site['host']}")
        if options["offline_cases"]:
            for case in seed_offline_cases(reset=options["reset"]):
                self.stdout.write(f"case {case.pk}: {case.order_ref} {case.amount} {case.currency} ({case.state})")
