"""The Garden panel in the sidebar: areas, plantings, the build plan, tasks.

The panel is one plain JavaScript module (frontend/master-gardener-panel.js),
served from the integration, with no build step. It reads and writes the
garden only through the integration's own actions, so it shows exactly what
an automation or an agent would see.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

PANEL_URL = "master-gardener"
STATIC_URL = "/master_gardener_static"
WEBCOMPONENT = "master-gardener-panel"
FRONTEND_DIR = Path(__file__).parent / "frontend"
DATA_KEY = "master_gardener_static_registered"


async def async_register_panel(hass: HomeAssistant, version: str) -> None:
    """Add the panel, if Home Assistant's frontend is running (it always is
    outside tests and headless setups; the integration works without it)."""
    if "frontend" not in hass.config.components:
        return
    # Static paths cannot be removed again, so they are registered once per run.
    if not hass.data.get(DATA_KEY):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
        )
        hass.data[DATA_KEY] = True
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name=WEBCOMPONENT,
        sidebar_title="Garden",
        sidebar_icon="mdi:sprout",
        # The version busts the browser's cache after an update.
        module_url=f"{STATIC_URL}/{WEBCOMPONENT}.js?v={version}",
    )


def async_remove_panel(hass: HomeAssistant) -> None:
    if "frontend" in hass.config.components:
        frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
