from pathlib import Path

import polib

root = Path(__file__).resolve().parent.parent
for source in (root / "locale").glob("*/LC_MESSAGES/django.po"):
    polib.pofile(str(source)).save_as_mofile(str(source.with_suffix(".mo")))
    print(f"Compiled {source.relative_to(root)}")
