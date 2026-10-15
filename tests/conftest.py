"""Broadlink test helpers."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components in every test."""


@pytest.fixture(autouse=True)
def mock_heartbeat():
    """Mock broadlink heartbeat."""
    with patch("custom_components.broadlink_capture.heartbeat.blk.ping"):
        yield
