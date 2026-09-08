"""Tests for translation catalog completeness."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "asusrouter"


def _key_paths(
    value: dict[str, Any], prefix: tuple[str, ...] = ()
) -> set[tuple[str, ...]]:
    """Return every nested key path in a translation catalog."""

    paths: set[tuple[str, ...]] = set()
    for key, child in value.items():
        path = (*prefix, key)
        paths.add(path)
        if isinstance(child, dict):
            paths.update(_key_paths(child, path))
    return paths


def test_every_translation_contains_every_strings_key() -> None:
    """Runtime translation files contain the complete strings schema."""

    strings = json.loads((INTEGRATION / "strings.json").read_text())
    required_paths = _key_paths(strings)

    for path in sorted((INTEGRATION / "translations").glob("*.json")):
        translation = json.loads(path.read_text())
        assert _key_paths(translation) == required_paths, path.name


def test_code_translation_keys_exist_in_strings() -> None:
    """Every config-flow and exception translation key is cataloged."""

    emitted: set[str] = set()
    for path in INTEGRATION.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "translation_key":
                    continue
                assert isinstance(keyword.value, ast.Constant), path
                assert isinstance(keyword.value.value, str), path
                emitted.add(keyword.value.value)

    const_tree = ast.parse((INTEGRATION / "const.py").read_text())
    result_constants = {
        target.id: node.value.value
        for node in const_tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id.startswith("RESULT_")
        and target.id != "RESULT_SUCCESS"
    }
    flow_tree = ast.parse((INTEGRATION / "config_flow.py").read_text())
    emitted.update(
        result_constants[node.id]
        for node in ast.walk(flow_tree)
        if isinstance(node, ast.Name) and node.id in result_constants
    )

    strings = json.loads((INTEGRATION / "strings.json").read_text())
    cataloged = set(strings.get("exceptions", {}))
    cataloged.update(strings.get("config", {}).get("error", {}))
    cataloged.update(strings.get("options", {}).get("error", {}))
    assert not emitted - cataloged
