"""Shared classifier for project tooling paths (formatter, linter, deps)."""
from __future__ import annotations

# Tooling files. Match the basename; ``tsconfig*.json`` and ``.eslintrc*`` are prefixes.
_CONFIG_NAMES = frozenset({
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    ".flake8",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".pre-commit-config.yaml",
    "package.json",
    "biome.json",
})


def _path_parts(path: str) -> list[str]:
    return [part for part in path.replace("\\", "/").split("/") if part not in {"", "."}]


def _basename(path: str) -> str:
    parts = _path_parts(path)
    return parts[-1] if parts else ""


def is_config_path(path: str) -> bool:
    """True when ``path`` is project tooling (formatter, linter, type checker, deps)."""
    name = _basename(path).lower()
    if name in _CONFIG_NAMES:
        return True
    if name.startswith("tsconfig") and name.endswith(".json"):
        return True
    return name.startswith(".eslintrc")
