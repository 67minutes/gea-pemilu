"""Optional isolated PostgreSQL 17 for Windows development when Docker is unavailable.

Downloads the Zonky test-binary bundle from Maven Central, verifies its published
SHA-256 checksum, and binds PostgreSQL only to 127.0.0.1:55432. Data stays in output/.
This is a development convenience, never the production database.
"""
import argparse
import hashlib
import io
import os
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "output" / "postgres"
VERSION = "17.11.0"
URL = ("https://repo.maven.apache.org/maven2/io/zonky/test/postgres/"
       f"embedded-postgres-binaries-windows-amd64/{VERSION}/"
       f"embedded-postgres-binaries-windows-amd64-{VERSION}.jar")


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, timeout=60,
                          creationflags=subprocess.CREATE_NO_WINDOW, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop"])
    action = parser.parse_args().action
    if os.name != "nt":
        raise SystemExit("Use Docker Compose or your installed PostgreSQL on other platforms.")
    binary = WORK / "bin" / "pg_ctl.exe"
    data = WORK / "data"
    WORK.mkdir(parents=True, exist_ok=True)
    if not binary.exists():
        if action == "stop":
            return
        print(f"Downloading isolated PostgreSQL {VERSION} test binaries...")
        bundle = urllib.request.urlopen(URL, timeout=60).read()
        expected = urllib.request.urlopen(URL + ".sha256", timeout=30).read().decode().split()[0]
        if hashlib.sha256(bundle).hexdigest() != expected:
            raise SystemExit("Binary checksum mismatch.")
        with zipfile.ZipFile(io.BytesIO(bundle)) as jar:
            archive = next(name for name in jar.namelist() if name.endswith(".txz"))
            with tarfile.open(fileobj=io.BytesIO(jar.read(archive)), mode="r:xz") as tar:
                tar.extractall(WORK, filter="data")
        binary = next(WORK.rglob("pg_ctl.exe"))
    if not binary.exists():
        binary = next(WORK.rglob("pg_ctl.exe"))
    env = os.environ.copy()
    env["PGPASSWORD"] = "local-development-only"
    if action == "stop":
        run([binary, "-D", data, "stop", "-m", "fast"], env=env)
        return
    if not (data / "PG_VERSION").exists():
        password_file = WORK / "initial-password.txt"
        password_file.write_text(env["PGPASSWORD"], encoding="utf-8")
        try:
            run([binary.parent / "initdb.exe", "-D", data, "-U", "gea", "--encoding=UTF8",
                 "--locale=C", "--auth=scram-sha-256", f"--pwfile={password_file}"], env=env)
        finally:
            password_file.unlink(missing_ok=True)
    status = subprocess.run([str(binary), "-D", str(data), "status"], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if status.returncode != 0:
        run([binary, "-D", data, "-l", WORK / "server.log", "-o", "-h 127.0.0.1 -p 55432", "start"], env=env)
    with psycopg.connect(host="127.0.0.1", port=55432, user="gea", password=env["PGPASSWORD"],
                         dbname="postgres", autocommit=True, connect_timeout=5) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='gea'").fetchone():
            conn.execute("CREATE DATABASE gea")
    print("Development PostgreSQL ready on 127.0.0.1:55432 (database/user gea).")


if __name__ == "__main__":
    main()
