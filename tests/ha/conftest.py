"""Fixtures for tests that load the integration into a real Home Assistant core."""

import re

import pytest
from catalogue_helpers import mock_catalogue
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.master_gardener import plant_store
from custom_components.master_gardener.const import DOMAIN
from custom_components.master_gardener.engine import plantdb


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


@pytest.fixture(autouse=True)
def catalogue(aioclient_mock, monkeypatch, tmp_path):
    """Keep the catalogue in a temp folder and never touch the network."""
    monkeypatch.setattr(plant_store, "catalogue_dir", lambda hass: tmp_path)
    # The fixture is far smaller than a real export.
    monkeypatch.setattr(plantdb, "MIN_PLANTS", 1)
    mock_catalogue(aioclient_mock)
    # Configure looks for a weather station near home when it has no ZIP
    # code; around the test harness's home (San Diego) there is none.
    aioclient_mock.get(
        re.compile(r"ncei\.noaa\.gov/access/services/search/.*bbox=33\."),
        json={"results": []},
    )
    return tmp_path
