from argparse import ArgumentParser
from getpass import getpass
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from vault.exceptions import CredentialError
from vault.service import CredentialService


class Command(BaseCommand):
    help = "Add an encrypted credential; the password is prompted without echo."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--name", required=True)
        parser.add_argument("--domains", required=True)
        parser.add_argument("--login", required=True)
        parser.add_argument("--email")

    def handle(self, *args: Any, **options: Any) -> None:
        password = getpass("Password: ")
        try:
            credential = CredentialService().create(
                options["name"],
                options["domains"].split(","),
                login=options["login"],
                email=options["email"],
                password=password,
            )
        except CredentialError as exception:
            raise CommandError(str(exception)) from None
        self.stdout.write(str(credential.id))
