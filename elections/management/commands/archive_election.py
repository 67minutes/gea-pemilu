import hashlib
import io
import json
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet
from django.core.management.base import BaseCommand, CommandError

from elections.models import AuditEvent
from elections.services import audit, finalize_results


class Command(BaseCommand):
    help = "Encrypt a PostgreSQL dump, finalized results and audit export in an offline archive."

    def add_arguments(self, parser):
        parser.add_argument("database_dump", help="A fresh pg_dump custom-format file from the closed election")
        parser.add_argument("--output", required=True)
        parser.add_argument("--key-file", required=True, help="New key file; keep separately from the archive")

    def handle(self, *args, **options):
        source, target, key_file = (Path(options[name]) for name in ["database_dump", "output", "key_file"])
        if target.exists() or key_file.exists():
            raise CommandError("Refusing to overwrite an archive or key.")
        if source.read_bytes()[:5] != b"PGDMP":
            raise CommandError("Provide a PostgreSQL custom-format dump (pg_dump --format=custom).")
        snapshot = finalize_results()
        if snapshot is None:
            raise CommandError("Archive only after election closing.")
        audit(None, "archive.created", output_name=target.name)
        files = {"database.dump": source.read_bytes(), "results.json": json.dumps(snapshot, indent=2).encode(),
                 "audit.json": json.dumps(list(AuditEvent.objects.order_by("id").values()), default=str, indent=2).encode()}
        files["manifest.json"] = json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in files.items()}, indent=2).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        target.parent.mkdir(parents=True, exist_ok=True)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        # Exclusive creation prevents accidental replacement during an operator retry.
        with key_file.open("xb") as file:
            file.write(key)
        with target.open("xb") as file:
            file.write(Fernet(key).encrypt(buffer.getvalue()))
        self.stdout.write("Encrypted archive created. Verify it and rehearse restore before removing infrastructure.")
        self.stdout.write("Store the key separately; the archive contains private voter records and MFA secrets.")
