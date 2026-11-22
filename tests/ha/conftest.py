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
    yield
