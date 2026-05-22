"""RCI parse command validation tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from custom_components.keenetic_router_pro.api import KeeneticApiError, _validate_cli_arg


@pytest.mark.parametrize(
    "raw",
    [
        "has space",
        " leading",
        "trailing ",
        "'quote'",
        '"quote"',
        "back\\slash",
        "line\nbreak",
        "carriage\rreturn",
        "tab\tchar",
        "nul\0char",
        "pipe|",
        "amp&",
        "semi;",
        "paren(",
        "paren)",
        "comma,",
        "tick`",
        "gt>",
        "lt<",
        "star*",
        "question?",
        "unicode\u00a0space",
        "rtl\u202eoverride",
    ],
)
def test_validate_cli_arg_rejects_injection_tokens(raw: str) -> None:
    with pytest.raises(KeeneticApiError):
        _validate_cli_arg(raw, "token")


@pytest.mark.parametrize(
    "raw",
    [
        "aa:bb:cc:dd:ee:ff",
        "AA-BB-CC-DD-EE-FF",
        "Policy0",
        "Policy1",
        "GigabitEthernet0",
        "WifiMaster0/AccessPoint0",
        "Crypto.Map_01+backup@site",
        "Token123",
    ],
)
def test_validate_cli_arg_accepts_router_identifiers(raw: str) -> None:
    assert _validate_cli_arg(raw, "token") == raw


def test_domain_f_string_parse_parameters_are_validated_before_use() -> None:
    failures: list[str] = []
    for path in Path("custom_components/keenetic_router_pro/api/domains").glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]:
            params = {arg.arg for arg in func.args.args if arg.arg != "self"}
            validated: set[str] = set()
            parse_command_vars: set[str] = set()
            for node in ast.walk(func):
                if (
                    isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Call)
                    and getattr(node.value.func, "id", "") == "_validate_cli_arg"
                ):
                    validated.update(
                        target.id for target in node.targets if isinstance(target, ast.Name)
                    )
                    if node.value.args and isinstance(node.value.args[0], ast.Name):
                        validated.add(node.value.args[0].id)
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.JoinedStr):
                    if any(
                        isinstance(target, ast.Name) and target.id == "cmd"
                        for target in node.targets
                    ):
                        parse_command_vars.add("cmd")
                is_parse_call = (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_rci_parse"
                    and node.args
                )
                fstring = node.args[0] if is_parse_call else None
                if is_parse_call and isinstance(fstring, ast.Name) and fstring.id in parse_command_vars:
                    continue
                if is_parse_call and isinstance(fstring, ast.JoinedStr):
                    used = {
                        child.value.id
                        for child in ast.walk(fstring)
                        if isinstance(child, ast.FormattedValue)
                        and isinstance(child.value, ast.Name)
                    }
                    unsafe = (used & params) - validated
                    if unsafe:
                        failures.append(f"{path}:{func.name} uses {sorted(unsafe)}")

    assert failures == []
