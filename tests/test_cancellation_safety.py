"""Static guards that bare ``except Exception`` blocks re-raise CancelledError.

Background: HA tears integrations down by cancelling the coordinator's
update task. A bare ``except Exception`` in the request path swallows
``asyncio.CancelledError`` (which is a ``BaseException`` subclass on
3.8+ but a ``Exception`` subclass on 3.7-) and prevents clean shutdown.
The defensive pattern across this integration is::

    except asyncio.CancelledError:
        raise
    except Exception:
        ...

This test parses the source of the integration's hot-path modules and
fails if it finds an ``except Exception`` clause that is not preceded
by a sibling ``except asyncio.CancelledError: raise`` clause in the
same try block.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "keenetic_router_pro"

# Hot-path modules where a swallowed CancelledError would matter most:
# the API client, the data coordinator, the firmware-update flow, and
# the integration setup module itself.
HOT_PATH_MODULES = [
    ROOT / "api.py",
    ROOT / "coordinator.py",
    ROOT / "update.py",
    ROOT / "__init__.py",
    ROOT / "config_flow.py",
]


def _try_blocks_with_bad_except(tree: ast.AST) -> list[ast.Try]:
    """Return every Try node that catches Exception without first re-raising CancelledError."""
    bad: list[ast.Try] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches_exception = False
        catches_cancelled = False
        for handler in node.handlers:
            exc = handler.type
            if exc is None:
                continue
            names = _exception_names(exc)
            if "CancelledError" in names:
                # Must re-raise.
                if any(isinstance(s, ast.Raise) and s.exc is None for s in handler.body):
                    catches_cancelled = True
            if "Exception" in names and "CancelledError" not in names:
                catches_exception = True
        if catches_exception and not catches_cancelled:
            bad.append(node)
    return bad


def _exception_names(node: ast.AST) -> set[str]:
    """Collect the leaf names mentioned in an ``except E`` clause."""
    names: set[str] = set()
    if isinstance(node, ast.Tuple):
        for elt in node.elts:
            names |= _exception_names(elt)
    elif isinstance(node, ast.Attribute):
        names.add(node.attr)
    elif isinstance(node, ast.Name):
        names.add(node.id)
    return names


@pytest.mark.parametrize("module_path", HOT_PATH_MODULES, ids=lambda p: p.name)
def test_no_cancellation_swallowing(module_path: pathlib.Path) -> None:
    src = module_path.read_text()
    tree = ast.parse(src)
    offenders = _try_blocks_with_bad_except(tree)
    if offenders:
        lines = ", ".join(str(node.lineno) for node in offenders)
        pytest.fail(
            f"{module_path.name}: try-blocks at lines {lines} catch Exception "
            f"without re-raising asyncio.CancelledError. This swallows HA shutdown "
            f"signals and produces hangs on integration reload."
        )
