import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "logic"))

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def _recorder(recorder_mock):  # integration depends on recorder; recorder_mock must come before hass
    yield


@pytest.fixture(autouse=True)
def _open_meteo_mock(aioclient_mock):
    """No test may reach the network; tests needing a specific answer clear and re-register."""
    aioclient_mock.get(
        "https://api.open-meteo.com/v1/forecast",
        json={"daily": {"time": [], "shortwave_radiation_sum": []}},
    )
    aioclient_mock.get(
        "https://archive-api.open-meteo.com/v1/archive",
        json={"daily": {"time": [], "shortwave_radiation_sum": []}},
    )
    yield


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    """Weather retry must not sleep for real (frozen clocks would never end it)."""
    monkeypatch.setattr("custom_components.prad.weather.RETRY_DELAY", 0)
    yield
