"""docker-compose.prod.yml repeats some config.py defaults, and must keep them.

Prod forwards only the variables it lists, so a Settings field it omits is a line
in .env.prod that does nothing. Listing one as `${X:-}` would be worse: pydantic
reads the present-but-empty value and an int field refuses to boot. So the file
spells each default out — and a default changed in config.py alone would then be
silently overridden in prod by the stale copy. This is what notices.
"""
import re
from pathlib import Path

from platform_app.config import Settings

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.prod.yml"
_DEFAULTED = re.compile(r"^\s+([A-Z0-9_]+): \$\{\1:-([^}]*)\}", re.MULTILINE)


def test_every_settings_default_in_prod_compose_matches_config():
    fields = Settings.model_fields
    pairs = [(name, default) for name, default in _DEFAULTED.findall(COMPOSE.read_text())
             if name.lower() in fields]
    assert pairs, "no Settings field found in docker-compose.prod.yml"
    for name, default in pairs:
        assert default == str(fields[name.lower()].default), name
