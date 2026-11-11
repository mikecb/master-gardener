"""The Garden panel in the sidebar."""

from homeassistant.components.frontend import DATA_PANELS
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
