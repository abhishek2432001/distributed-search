import time

from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = "Wait for the database to become available."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=int, default=60)
        parser.add_argument("--interval", type=float, default=2.0)

    def handle(self, *args, **options):
        timeout = options["timeout"]
        interval = options["interval"]
        elapsed = 0

        self.stdout.write("Waiting for database...")

        while elapsed < timeout:
            try:
                connections["default"].ensure_connection()
                self.stdout.write(self.style.SUCCESS("Database ready."))
                return
            except OperationalError:
                self.stdout.write(f"  Not ready, retrying in {interval}s ({elapsed:.0f}/{timeout}s)...")
                time.sleep(interval)
                elapsed += interval

        self.stderr.write(self.style.ERROR(f"Database not available after {timeout}s."))
        raise SystemExit(1)
