import ast
import pathlib

CORE_UI = pathlib.Path(__file__).resolve().parents[3] / "src" / "yohoho" / "core" / "ui"


def test_core_ui_does_not_import_macos_window():
    offenders = []
    for py in CORE_UI.rglob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "macos_window" in node.module:
                offenders.append(str(py))
    assert offenders == [], f"core/ui still imports macos_window: {offenders}"
