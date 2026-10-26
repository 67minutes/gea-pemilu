"""Authenticate/decrypt an archive, verify member checksums, optionally extract for restore."""
import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("archive")
parser.add_argument("--key-file", required=True)
parser.add_argument("--extract-to", help="Optional NEW private directory for a restore rehearsal")
args = parser.parse_args()
plaintext = Fernet(Path(args.key_file).read_bytes().strip()).decrypt(Path(args.archive).read_bytes())
with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
    allowed = {"database.dump", "results.json", "audit.json", "manifest.json"}
    if set(archive.namelist()) != allowed:
        raise SystemExit("Unexpected archive members.")
    manifest = json.loads(archive.read("manifest.json"))
    if set(manifest) != allowed - {"manifest.json"}:
        raise SystemExit("Incomplete manifest.")
    for name, expected in manifest.items():
        if hashlib.sha256(archive.read(name)).hexdigest() != expected:
            raise SystemExit(f"Checksum failed: {name}")
    if args.extract_to:
        target = Path(args.extract_to)
        target.mkdir(parents=True, exist_ok=False)
        for name in allowed:
            (target / name).write_bytes(archive.read(name))
print("Archive authenticated; database, results, and audit checksums verified.")
