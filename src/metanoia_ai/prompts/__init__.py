"""Versioned LLM prompts. Change a prompt → new file (v2), then re-run its samples."""

from importlib.resources import files


def load_prompt(name: str) -> str:
    """Text of `<name>.md` in this package, e.g. load_prompt("schedule_reason_v1")."""
    return files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8")


def load_samples(name: str) -> str:
    """Raw JSON of `<name>_samples.json`: inputs to re-run after a prompt change."""
    path = files(__package__).joinpath(f"{name}_samples.json")
    return path.read_text(encoding="utf-8")
