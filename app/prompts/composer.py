"""Prompt fragments, stored as markdown next to this file.

Each .md holds one part of a prompt; compose() joins them back into the single
string the agent receives. Splitting them keeps the parts that change often
(output rules, which track app.formatting) separate from the parts that don't.
"""

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache
def load(name: str) -> str:
    """Read one fragment by stem, e.g. load("output_rules") -> output_rules.md.

    Cached: prompts are read once per process, not once per request.
    """
    path = _DIR / f"{name}.md"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in _DIR.glob("*.md"))) or "none"
        raise FileNotFoundError(f"No prompt {name!r} in {_DIR} (have: {available})")
    return path.read_text(encoding="utf-8").strip()


def compose(*names: str) -> str:
    """Join fragments in the order given, one blank line between each.

    The call site reads as an outline of the finished prompt:
        compose("system_role", "research_process", "output_rules")
    """
    if not names:
        raise ValueError("compose() needs at least one prompt name")
    return "\n\n".join(load(name) for name in names)