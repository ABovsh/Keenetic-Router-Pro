"""Log redaction tests for MAC-taking actions."""

from __future__ import annotations

from tests.conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import logging
from unittest.mock import AsyncMock

import pytest

from custom_components.keenetic_router_pro.api import KeeneticClient


MAC = "AA:BB:CC:DD:EE:FF"
MAC_LOWER = MAC.lower()


def _logged_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("async_block_client", (MAC,)),
        ("async_set_client_policy", (MAC, "Policy1")),
        ("async_reboot_mesh_node", (MAC_LOWER,)),
        pytest.param(
            "async_start_node_firmware_update",
            ("192.0.2.5", "Kitchen", MAC_LOWER),
            marks=pytest.mark.xfail(
                strict=True,
                reason="bug: mesh node firmware update logs raw controller member cid",
            ),
        ),
    ],
)
async def test_mac_taking_actions_do_not_log_raw_mac(
    caplog: pytest.LogCaptureFixture,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = object()
    client._rci_parse = AsyncMock(return_value={"message": "ok"})
    client._authenticate_to_node = AsyncMock(return_value=None)

    caplog.set_level(logging.DEBUG, logger="custom_components.keenetic_router_pro")
    method = getattr(client, method_name)

    await method(*args)

    logged = _logged_text(caplog)
    assert MAC not in logged
    assert MAC_LOWER not in logged
