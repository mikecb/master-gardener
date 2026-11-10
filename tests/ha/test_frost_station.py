"""Frost dates from the nearest weather station, at setup and in Configure."""

from catalogue_helpers import mock_catalogue
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.master_gardener.const import DOMAIN
from custom_components.master_gardener.engine import frost_normals as fn

LAT, LON = 38.072402, -78.50149
ZIP = {"zone": "7b", "coordinates": {"lat": str(LAT), "lon": str(LON)}}
SEARCH = {
    "results": [
        {
            "boundingPoints": [{"coordinates": [lon, lat]}],
            "stations": [{"id": sid, "name": name}],
        }
        for sid, name, lat, lon in (
            ("USC00441593", "CHARLOTTESVILLE 2W, VA US", 38.0328, -78.5225),
            ("USC00443213", "FREE UNION, VA US", 38.0908, -78.5894),
        )
    ]
}
ROWS = [
    {
        "STATION": "USC00441593",
        "NAME": "CHARLOTTESVILLE 2W, VA US",
        "ELEVATION": " 163.1",
        fn.LAST_FROST: "   04/02",
        fn.FIRST_FROST: "   11/10",
    },
    {
        "STATION": "USC00443213",
        "NAME": "FREE UNION, VA US",
        "ELEVATION": " 179.8",
        fn.LAST_FROST: "   04/19",
        fn.FIRST_FROST: "   10/24",
    },
]
IDS = ["USC00441593", "USC00443213"]


def mock_noaa(aioclient_mock, *, rows=ROWS, search=SEARCH, status=200):
    for radius in fn.SEARCH_RADII:
        aioclient_mock.get(fn.search_url(LAT, LON, radius), json=search, status=status)
    aioclient_mock.get(fn.data_url(IDS), json=rows)


async def _setup_flow(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"zip_code": "22901"}
    )


async def test_setup_offers_the_nearest_station(hass, aioclient_mock):
    mock_catalogue(aioclient_mock)
    aioclient_mock.get("https://phzmapi.org/22901.json", json=ZIP)
    mock_noaa(aioclient_mock)
    result = await _setup_flow(hass)
    assert result["step_id"] == "frost"
    source = result["description_placeholders"]["source"]
    assert "CHARLOTTESVILLE 2W, VA (5 km away, 163 m up)" in source
    assert "last spring frost of April 2 and first autumn frost of November 10" in (
        source
    )
    # The form starts from the station's dates.
    fields = {str(k): k for k in result["data_schema"].schema}
    assert fields["last_frost"].default().endswith("-04-02")
    assert fields["first_frost"].default().endswith("-11-10")

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert (data["last_frost"], data["first_frost"]) == ("04-02", "11-10")
    assert (data["latitude"], data["longitude"]) == (LAT, LON)
    assert data["frost_station"]["station"] == "USC00441593"
    await hass.async_block_till_done(wait_background_tasks=True)

    garden = await hass.services.async_call(
        DOMAIN, "get_garden", {}, blocking=True, return_response=True
    )
    source = garden["site"]["frost_dates_from"]
    assert source["source"] == "weather_station"
    assert source["station"]["name"] == "CHARLOTTESVILLE 2W, VA"


async def test_setup_falls_back_to_the_zone(hass, aioclient_mock):
    mock_catalogue(aioclient_mock)
    aioclient_mock.get("https://phzmapi.org/22901.json", json=ZIP)
    mock_noaa(aioclient_mock, status=503)
    result = await _setup_flow(hass)
    assert "No nearby weather station" in result["description_placeholders"]["source"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert (result["data"]["last_frost"], result["data"]["first_frost"]) == (
        "04-05",
        "10-30",
    )
    assert "frost_station" not in result["data"]
    await hass.async_block_till_done(wait_background_tasks=True)
    garden = await hass.services.async_call(
        DOMAIN, "get_garden", {}, blocking=True, return_response=True
    )
    assert garden["site"]["frost_dates_from"] == {
        "source": "hardiness_zone_estimate",
        "nearest_station": None,
    }


async def test_frost_free_stations_fall_back_too(hass, aioclient_mock):
    mock_catalogue(aioclient_mock)
    aioclient_mock.get("https://phzmapi.org/22901.json", json=ZIP)
    no_freeze = [{**row, fn.LAST_FROST: " -9999.0"} for row in ROWS]
    mock_noaa(aioclient_mock, rows=no_freeze)
    result = await _setup_flow(hass)
    assert "No nearby weather station" in result["description_placeholders"]["source"]


async def test_no_stations_nearby(hass, aioclient_mock):
    mock_catalogue(aioclient_mock)
    aioclient_mock.get("https://phzmapi.org/22901.json", json=ZIP)
    mock_noaa(aioclient_mock, search={"results": []})
    result = await _setup_flow(hass)
    assert "No nearby weather station" in result["description_placeholders"]["source"]
    # Searched twice, the second time wider, and never asked for data.
    paths = [str(call[1]).split("?")[0] for call in aioclient_mock.mock_calls]
    assert paths.count(fn.SEARCH_URL) == len(fn.SEARCH_RADII)
    assert fn.DATA_URL not in paths


async def test_configure_offers_the_station(hass, aioclient_mock):
    """An entry set up before locations were kept finds its ZIP code again."""
    mock_catalogue(aioclient_mock)
    aioclient_mock.get("https://phzmapi.org/22901.json", json=ZIP)
    mock_noaa(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Master Gardener",
        data={
            "zip_code": "22901",
            "zone": "7b",
            "last_frost": "04-05",
            "first_frost": "10-30",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    dates = {"last_frost": "2027-04-05", "first_frost": "2027-10-30"}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "CHARLOTTESVILLE 2W, VA" in result["description_placeholders"]["station"]
    assert "use_station" in {str(k) for k in result["data_schema"].schema}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**dates, "use_station": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.site.last_frost == (4, 2)
    assert entry.runtime_data.site.first_frost == (11, 10)

    # Correcting the dates by hand keeps the station for reference.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"last_frost": "2027-04-12", "first_frost": "2027-11-01"}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    garden = await hass.services.async_call(
        DOMAIN, "get_garden", {}, blocking=True, return_response=True
    )
    source = garden["site"]["frost_dates_from"]
    assert source["source"] == "entered"
    assert source["nearest_station"]["station"] == "USC00441593"


async def test_configure_without_a_station(hass, entry):
    """No ZIP code (or no answer): the form is as before, with no switch."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["description_placeholders"] == {"station": ""}
    assert "use_station" not in {str(k) for k in result["data_schema"].schema}
