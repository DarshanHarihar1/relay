"""Guard against a runtime import that only resolves because of a dev dependency.

`Dockerfile` installs with `uv sync --no-dev`, so anything `app/` imports must be
declared in `[project.dependencies]`, not in the dev group.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


# Import name -> the distribution that provides it.
PROVIDED_BY = {
    "cryptography": "cryptography",
    "fastapi": "fastapi",
    "firebase_admin": "firebase-admin",
    "google": "firebase-admin",
    "httpx": "httpx",
    "pydantic": "pydantic",
    "starlette": "fastapi",
    "typing_extensions": "pydantic",
    "uvicorn": "uvicorn",
}


def _declared_runtime_distributions() -> set[str]:
    block = re.search(
        r"^dependencies\s*=\s*\[(.*?)^\]", Path("pyproject.toml").read_text(), re.S | re.M
    )
    assert block, "pyproject.toml must declare [project.dependencies]"
    return {
        re.split(r"[<>=!~\[]", name.strip().strip('",'))[0].strip()
        for name in block.group(1).splitlines()
        if name.strip().strip('",')
    }


def _imported_top_level_modules() -> set[str]:
    modules: set[str] = set()
    for path in Path("app").rglob("*.py"):
        for line in path.read_text().splitlines():
            match = re.match(r"\s*(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_]*)", line)
            if match:
                modules.add(match.group(1))
    return modules


def test_every_runtime_import_is_a_declared_runtime_dependency() -> None:
    declared = _declared_runtime_distributions()
    third_party = {
        module
        for module in _imported_top_level_modules()
        if module not in sys.stdlib_module_names and module != "app"
    }

    unknown = third_party - PROVIDED_BY.keys()
    assert not unknown, f"unrecognised runtime imports, map them in PROVIDED_BY: {sorted(unknown)}"

    missing = {
        module: PROVIDED_BY[module]
        for module in third_party
        if PROVIDED_BY[module] not in declared
    }
    assert not missing, f"imported at runtime but not a runtime dependency: {missing}"


def test_the_dev_group_holds_no_runtime_dependency() -> None:
    block = re.search(r"^dev\s*=\s*\[(.*?)^\]", Path("pyproject.toml").read_text(), re.S | re.M)
    assert block
    dev = {
        re.split(r"[<>=!~\[]", name.strip().strip('",'))[0].strip()
        for name in block.group(1).splitlines()
        if name.strip().strip('",')
    }
    required = {
        PROVIDED_BY[module]
        for module in _imported_top_level_modules()
        if module in PROVIDED_BY
    }

    assert not (dev & required), f"runtime distributions stuck in the dev group: {dev & required}"
