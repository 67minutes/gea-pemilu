"""Render pre-deploy: direct PostgreSQL URL for schema migrations, not the pooler."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
env = os.environ.copy()
if env.get("DATABASE_MIGRATION_URL"):
    env["DATABASE_URL"] = env["DATABASE_MIGRATION_URL"]
subprocess.run([sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"], cwd=root, env=env, check=True)
subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], cwd=root, env=env, check=True)
