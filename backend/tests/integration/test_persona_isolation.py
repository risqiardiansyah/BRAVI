"""Persona tool-isolation regression test — docs/11-coding-standard.md §8.1,
docs/12-testing-strategy.md §3 ("assert user_chat_graph's module has no import path
reaching tools/operator_tools.py").

Static (AST-based) transitive-import walk rather than a dynamic import check: statically
parsing source files never executes `tools/operator_tools.py` even after Phase 10 adds it,
and catches an import reachable through any depth of `graphs/nodes/`/`services/` modules,
not just `user_chat_graph.py`'s own direct imports.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app as app_package

_REPO_ROOT = Path(app_package.__file__).resolve().parent.parent


def _module_to_path(module_name: str) -> Path | None:
    relative = Path(*module_name.split("."))
    file_path = _REPO_ROOT / relative.with_suffix(".py")
    if file_path.exists():
        return file_path
    init_path = _REPO_ROOT / relative / "__init__.py"
    if init_path.exists():
        return init_path
    return None


def _extract_app_imports(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app" or alias.name.startswith("app."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "app" or node.module.startswith("app.")):
                found.add(node.module)
                # Disambiguate `from app.services import chat_service`-style imports:
                # `chat_service` may be a *submodule* of `app.services` (resolved via
                # Python's import machinery, not just a plain attribute) — if the
                # filesystem confirms it's a real submodule, it's a real transitive
                # import too, not just a name pulled from `node.module`'s own body.
                for alias in node.names:
                    candidate = f"{node.module}.{alias.name}"
                    if _module_to_path(candidate) is not None:
                        found.add(candidate)
    return found


def _transitive_app_imports(start_module: str) -> set[str]:
    visited: set[str] = set()
    queue = [start_module]
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        path = _module_to_path(current)
        if path is None:
            continue
        for imported in _extract_app_imports(path):
            if imported not in visited:
                queue.append(imported)
    return visited


def test_user_chat_graph_never_imports_operator_tools() -> None:
    imports = _transitive_app_imports("app.graphs.user_chat_graph")

    assert "app.tools.operator_tools" not in imports
    # Sanity check that the walk actually traversed real modules rather than silently
    # finding nothing (a vacuously-passing test would be worse than no test at all).
    assert "app.graphs.nodes.embed_question" in imports
    assert "app.services.chat_service" in imports
