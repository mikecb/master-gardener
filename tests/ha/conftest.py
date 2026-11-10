"""Fixtures for tests that load the integration into a real Home Assistant core."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.master_gardener.const import DOMAIN


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def entry(hass):
    """An entry with two areas, not yet set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Master Gardener",
        data={"last_frost": "04-15", "first_frost": "10-25"},
        subentries_data=[
            {
                "subentry_id": "veg",
                "subentry_type": "area",
                "title": "Vegetable garden",
                "unique_id": None,
                "data": {
                    "name": "Vegetable garden",
                    "style": "vegetable",
                    "light": "full_sun",
                },
            },
            {
                "subentry_id": "lawn",
                "subentry_type": "area",
                "title": "Front lawn",
                "unique_id": None,
                "data": {"name": "Front lawn", "style": "lawn", "light": "full_sun"},
            },
        ],
    )
    entry.add_to_hass(hass)
    return entry
