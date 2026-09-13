"""Guard rails encoded as tests.

Several project constraints are easy to state and easy to violate by accident
six weeks later. These tests fail loudly when that happens.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = PROJECT_ROOT / "eda"
SEMANTIC_ROOT = PROJECT_ROOT / "semantic"

#: Names that would let model-produced text become executable code.
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_IMPORTS = {"subprocess", "pickle", "marshal", "shelve"}


def _python_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def test_there_are_python_files_to_check() -> None:
    assert _python_files(), "no package sources found -- the guard tests would be vacuous"


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_dynamic_code_execution(path: Path) -> None:
    """Constraint: never execute model-generated Python/shell. No eval/exec/subprocess."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_CALLS, (
                f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} calls {node.func.id}()"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in FORBIDDEN_IMPORTS, (
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} imports {alias.name}"
                )
        if isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            assert root not in FORBIDDEN_IMPORTS, (
                f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} imports from {node.module}"
            )


def test_sqlite_connect_lives_only_in_eda_db() -> None:
    """Constraint: exactly one SQL execution path. All connections come from eda/db.py."""
    offenders = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in _python_files()
        if path.name != "db.py" and "sqlite3.connect" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"sqlite3.connect outside eda/db.py: {offenders}"


def test_stage1_package_does_not_import_llm_or_ui_libraries() -> None:
    """Stage 1 is offline: importing eda must not pull in an SDK that can call out."""
    forbidden = {"openai", "langgraph", "langchain", "langchain_core", "streamlit", "requests"}
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in forbidden:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT).as_posix()}: {name}")
    assert offenders == [], f"stage 1 code must stay offline: {offenders}"


#: Literals below this value are ordinary constants (row limits, percentages,
#: prices in the catalogue) and would produce false positives.
_ANSWER_LITERAL_THRESHOLD = 1000


def _expected_numbers(node: object) -> set[int]:
    """Every sizeable integer that appears anywhere in the expectation file."""
    found: set[int] = set()
    if isinstance(node, bool):
        return found
    if isinstance(node, int) and node >= _ANSWER_LITERAL_THRESHOLD:
        found.add(node)
    elif isinstance(node, dict):
        for value in node.values():
            found |= _expected_numbers(value)
    elif isinstance(node, list):
        for value in node:
            found |= _expected_numbers(value)
    return found


def test_expected_answers_are_not_baked_into_runtime_code(
    expected_fixture_metrics,
) -> None:
    """Constraint: no hard-coded evaluation answers under eda/.

    The forbidden numbers are read out of the expectation file rather than
    retyped here, so this test cannot drift away from the fixture.
    """
    forbidden = _expected_numbers(expected_fixture_metrics)
    assert forbidden, "expectation file should contain some sizeable numbers"

    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, int)
                and not isinstance(node.value, bool)
                and node.value in forbidden
            ):
                offenders.append(
                    f"{path.relative_to(PROJECT_ROOT).as_posix()}:{node.lineno} -> {node.value}"
                )
    assert offenders == [], f"runtime code must not contain fixture answers: {offenders}"


def test_semantic_config_contains_no_fixture_answers(expected_fixture_metrics) -> None:
    """The semantic layer declares 口径, never the answers to evaluation questions."""
    forbidden = _expected_numbers(expected_fixture_metrics)
    offenders = [
        f"{path.name}: {number}"
        for path in sorted(SEMANTIC_ROOT.glob("*.yaml"))
        for number in sorted(forbidden)
        if str(number) in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"semantic config must not contain fixture answers: {offenders}"


def test_semantic_config_declares_no_executable_expression(expected_fixture_metrics) -> None:
    """Business definitions are declarative: no SQL fragments, no Python in YAML."""
    banned = ("!!python", "lambda", "eval(", "exec(", "__import__", "${")
    offenders = [
        f"{path.name}: {token}"
        for path in sorted(SEMANTIC_ROOT.glob("*.yaml"))
        for token in banned
        if token in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"semantic config must stay declarative: {offenders}"


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """ids of the Constant nodes that are docstrings, so prose can be skipped."""
    ids: set[int] = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def test_runtime_code_never_reads_the_held_out_evaluation_set() -> None:
    """The held-out set must never reach a prompt, an example store or the agent.

    The directory does not exist yet (it is built in stage 6); this guard is in
    place now so it fails the moment someone wires it into runtime code.
    """
    needles = ("evaluation/heldout", "evaluation\\heldout", "heldout")
    offenders = [
        f"{path.relative_to(PROJECT_ROOT).as_posix()}: {needle}"
        for path in _python_files()
        for needle in needles
        if needle in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"runtime code must not touch the held-out set: {offenders}"


def test_runtime_code_never_reads_the_test_expectations() -> None:
    """Runtime code must not be able to look the answers up either.

    Only real string literals count. A docstring is allowed to *mention* where
    the expectation file lives -- that is documentation, not a code path.
    """
    needles = ("expected_fixture_metrics", "tests/data", "tests\\data")
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        skip = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and any(needle in node.value for needle in needles)
            ):
                offenders.append(
                    f"{path.relative_to(PROJECT_ROOT).as_posix()}:{node.lineno} -> {node.value!r}"
                )
    assert offenders == [], f"runtime code must not reach into tests/: {offenders}"


def test_env_example_contains_placeholders_only() -> None:
    """Constraint: .env.example never holds a usable credential."""
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=" in text
    for line in text.splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            value = line.split("=", 1)[1].strip()
            assert value == "sk-your-deepseek-api-key-here", "looks like a real key"
    assert "ENABLE_LIVE_LLM_TESTS=false" in text


def test_gitignore_excludes_secrets_and_generated_databases() -> None:
    lines = {
        line.strip()
        for line in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    for entry in (".env", "*.db", "/data/", ".venv/"):
        assert entry in lines, f".gitignore is missing {entry}"
    assert "!.env.example" in lines
