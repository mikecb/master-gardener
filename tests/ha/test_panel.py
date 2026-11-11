"""The Garden panel in the sidebar."""

import pytest
import voluptuous as vol
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component


async def test_panel_is_registered_and_served(hass, entry, hass_client):
    assert await async_setup_component(hass, "frontend", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    panel = hass.data[DATA_PANELS]["master-gardener"]
    assert panel.sidebar_title == "Garden"
    assert panel.sidebar_icon == "mdi:sprout"
    custom = panel.config["_panel_custom"]
    assert custom["name"] == "master-gardener-panel"
    assert custom["module_url"].startswith(
        "/master_gardener_static/master-gardener-panel.js?v="
    )

    client = await hass_client()
    response = await client.get("/master_gardener_static/master-gardener-panel.js")
    assert response.status == 200
    assert 'customElements.define("master-gardener-panel"' in await response.text()

    # Unloading takes it out of the sidebar; loading again puts it back.
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert "master-gardener" not in hass.data[DATA_PANELS]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "master-gardener" in hass.data[DATA_PANELS]


async def test_works_without_the_frontend(hass, entry):
    """Headless (and most tests): no panel, everything else as usual."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "frontend" not in hass.config.components
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_area_layout(hass, entry):
    """Where each area sits on the property map in the Garden panel."""

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    async def call(action, data=None):
        return await hass.services.async_call(
            "master_gardener", action, data or {}, blocking=True, return_response=True
        )

    garden = await call("get_garden")
    assert [a["layout"] for a in garden["areas"]] == [None, None]

    placed = await call(
        "set_area_layout",
        {"area": "Vegetable garden", "x": 2, "y": 1, "width": 10, "height": 6},
    )
    assert placed == {"x": 2, "y": 1, "width": 10, "height": 6}
    garden = await call("get_garden")
    assert garden["areas"][0]["layout"] == placed
    assert garden["areas"][1]["layout"] is None

    # Kept across a restart.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (await call("get_garden"))["areas"][0]["layout"] == placed

    # Off the right-hand edge, or too small, is refused.
    with pytest.raises(ServiceValidationError):
        await call(
            "set_area_layout",
            {"area": "lawn", "x": 20, "y": 0, "width": 6, "height": 3},
        )
    with pytest.raises(vol.Invalid):
        await call(
            "set_area_layout",
            {"area": "lawn", "x": 0, "y": 0, "width": 1, "height": 3},
        )
