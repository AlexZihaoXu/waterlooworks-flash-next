"""Static lifecycle regression: no credentials, platform fixtures, or network."""

import ast
import inspect
import textwrap

from waterlooworks_flash_next.refresh import RefreshService


def test_browser_closes_after_capture_before_direct_collection() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(RefreshService._run)))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    capture = next(
        node.lineno
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "open_all_jobs"
    )
    close = min(
        node.lineno
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "session"
        and node.func.attr == "close"
    )
    collect = next(
        node.lineno
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "collection"
        and node.func.attr == "run"
    )
    assert capture < close < collect
    assert any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "session"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and node.value.value is None
        and close < node.lineno < collect
        for node in ast.walk(tree)
    )
