"""Seed on hand from a Simple Inventory inventory."""

from homeassistant.core import SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.master_gardener.const import DOMAIN

JAN_10 = "2027-01-10 12:00:00+00:00"
SENSOR = "sensor.vegetable_garden_seeds_inventory"


class FakeInventory:
    """Stands in for Simple Inventory: a sensor and its get_items action."""

    def __init__(self, hass, items=None):
        self.hass = hass
        self.items = items or []
        self.calls = 0
        hass.services.async_register(
            "simple_inventory",
            "get_items",
            self._get_items,
            supports_response=SupportsResponse.ONLY,
        )
        self.publish()

    async def _get_items(self, call):
        assert call.data == {"inventory_id": "inv1"}
        self.calls += 1
        return {"items": self.items}

    def publish(self):
        self.hass.states.async_set(
            SENSOR,
            str(len(self.items)),
            {
                "inventory_id": "inv1",
                "total_quantity": sum(i.get("quantity") or 0 for i in self.items),
            },
        )


def _entry(hass, seed_inventory=SENSOR):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Master Gardener",
        data={"last_frost": "04-15", "first_frost": "10-25"},
        options={"seed_inventory": seed_inventory} if seed_inventory else {},
        subentries_data=[
            {
                "subentry_id": "veg",
                "subentry_type": "area",
                "title": "Vegetable garden",
                "unique_id": None,
                "data": {"name": "Vegetable garden", "style": "vegetable"},
            }
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _call(hass, action, data=None):
    return await hass.services.async_call(
        DOMAIN, action, data or {}, blocking=True, return_response=True
    )


async def _tasks(hass):
    result = await _call(hass, "list_tasks", {"show": "all", "days": 120})
    return {t["title"]: t for t in result["tasks"]}


async def test_get_seed_until_it_is_in_the_inventory(hass, freezer):
    freezer.move_to(JAN_10)
    inventory = FakeInventory(hass)
    entry = _entry(hass)
    await _setup(hass, entry)
    await _call(hass, "add_planting", {"area": "veg", "plant": "tomato"})
    await _call(hass, "add_planting", {"area": "veg", "plant": "cherokee-purple"})

    tasks = await _tasks(hass)
    order = tasks["Get seed for Tomato"]
    assert (order["kind"], order["start"], order["end"]) == (
        "order_seed",
        "2027-01-07",
        "2027-02-17",
    )
    assert order["state"] == "due"
    assert "No seed for Tomato in the seed inventory" in order["detail"]
    assert "Get seed for Cherokee Purple Tomato" in tasks

    # Sungold seed goes in: it does for the tomato, not for Cherokee Purple.
    inventory.items = [
        {"name": "Sungold tomato", "quantity": 2, "unit": "packets", "aliases": []}
    ]
    inventory.publish()
    await hass.async_block_till_done(wait_background_tasks=True)
    tasks = await _tasks(hass)
    assert "Get seed for Tomato" not in tasks
    assert "Get seed for Cherokee Purple Tomato" in tasks
    sow = tasks["Start Tomato indoors"]
    assert sow["detail"].endswith("Seed on hand: Sungold tomato (2 packets).")

    garden = await _call(hass, "get_garden")
    assert garden["seed_inventory"]["read"] is True
    assert garden["seed_inventory"]["seeds"] == [
        {
            "name": "Sungold tomato",
            "quantity": 2.0,
            "unit": "packets",
            "expiry": None,
            "expired": False,
            "plant": "tomato",  # not a catalogue name: the closest plant
            "plant_guessed": True,
        }
    ]

    # Ticking "Get seed" records it, even before the seed is entered.
    await _call(
        hass,
        "complete_task",
        {"task_id": tasks["Get seed for Cherokee Purple Tomato"]["id"]},
    )
    tasks = await _tasks(hass)
    assert "Get seed for Cherokee Purple Tomato" not in tasks  # done tasks hidden


async def test_expired_seed_and_catalogue_names(hass, freezer):
    freezer.move_to(JAN_10)
    FakeInventory(
        hass,
        [
            {"name": "Carrots", "quantity": 1, "expiry_date": "2026-06-30"},
            {"name": "Cherokee Purple Tomato", "quantity": 1},
        ],
    )
    entry = _entry(hass)
    await _setup(hass, entry)
    await _call(hass, "add_planting", {"area": "veg", "plant": "tomato"})
    await _call(hass, "add_planting", {"area": "veg", "plant": "carrot"})
    result = await _call(hass, "list_tasks", {"show": "all", "days": 200})
    tasks = {t["title"]: t for t in result["tasks"]}
    assert (
        "past its date: Carrots (expired 2026-06-30)"
        in (tasks["Get seed for Carrot"]["detail"])
    )
    # Cherokee Purple seed is tomato seed: the plain tomato is covered.
    assert "Get seed for Tomato" not in tasks
    assert tasks["Start Tomato indoors"]["detail"].endswith(
        "Seed on hand: Cherokee Purple Tomato (1)."
    )
    garden = await _call(hass, "get_garden")
    seeds = {s["name"]: s for s in garden["seed_inventory"]["seeds"]}
    assert seeds["Carrots"]["plant"] == "carrot"
    assert seeds["Carrots"]["expired"] is True
    assert seeds["Cherokee Purple Tomato"]["plant"] == "cherokee-purple"


async def test_no_seed_inventory_no_seed_tasks(hass, freezer):
    freezer.move_to(JAN_10)
    entry = _entry(hass, seed_inventory=None)
    await _setup(hass, entry)
    await _call(hass, "add_planting", {"area": "veg", "plant": "tomato"})
    assert not [t for t in await _tasks(hass) if t.startswith("Get seed")]
    assert "seed_inventory" not in await _call(hass, "get_garden")


async def test_inventory_not_there(hass, freezer):
    """Simple Inventory missing or its sensor gone: no seed tasks, no errors."""
    freezer.move_to(JAN_10)
    entry = _entry(hass)
    await _setup(hass, entry)
    await _call(hass, "add_planting", {"area": "veg", "plant": "tomato"})
    assert not [t for t in await _tasks(hass) if t.startswith("Get seed")]
    assert (await _call(hass, "get_garden"))["seed_inventory"]["read"] is False

    # The sensor appears, but the action is not registered.
    hass.states.async_set(SENSOR, "0", {"inventory_id": "inv1"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.seeds is None


async def test_a_failed_read_keeps_the_last_good_one(hass, freezer):
    freezer.move_to(JAN_10)
    inventory = FakeInventory(hass, [{"name": "Tomato", "quantity": 1}])
    entry = _entry(hass)
    await _setup(hass, entry)
    assert [s.name for s in entry.runtime_data.seeds] == ["Tomato"]
    hass.services.async_remove("simple_inventory", "get_items")
    inventory.publish()
    hass.states.async_set(SENSOR, "1", {"inventory_id": "inv1", "changed": True})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [s.name for s in entry.runtime_data.seeds] == ["Tomato"]


async def test_configure_picks_the_inventory(hass):
    entry = _entry(hass, seed_inventory=None)
    await _setup(hass, entry)
    dates = {"last_frost": "2027-04-15", "first_frost": "2027-10-25"}
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "seed_inventory" in {str(k) for k in result["data_schema"].schema}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**dates, "seed_inventory": SENSOR}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.seed_inventory == SENSOR


async def test_packets_are_proposed_as_their_window_nears(hass, freezer):
    freezer.move_to(JAN_10)
    inventory = FakeInventory(
        hass,
        [
            {"name": "Blush Cherry Tomato", "quantity": 1, "unit": "packet"},
            {"name": "Zinnia", "quantity": 1, "unit": "packet"},
        ],
    )
    entry = _entry(hass)
    await _setup(hass, entry)
    manager = entry.runtime_data

    proposed = await _call(hass, "list_plantings", {"status": "proposed"})
    (tomato,) = proposed["plantings"]
    assert (tomato["name"], tomato["area"], tomato["plant"]) == (
        "Blush Cherry Tomato",
        "Vegetable garden",
        "tomato",
    )
    assert tomato["from_seed"] == "Blush Cherry Tomato"
    assert tomato["notes"] == (
        "Proposed from your seed inventory: Blush Cherry Tomato (1 packet)."
        " Its dates are those of Tomato, the closest plant in the catalogue."
    )
    # Proposals get no tasks, and zinnias have no area of their style here.
    assert not [t for t in await _tasks(hass) if "Blush" in t]

    # Accepting: tasks start, and the seed on hand is named; no purchase.
    await _call(
        hass, "set_planting_status", {"planting_id": tomato["id"], "status": "active"}
    )
    planting = manager.plantings[tomato["id"]]
    assert (planting.bought_on, planting.cost) == (None, None)
    tasks = await _tasks(hass)
    sow = tasks["Start Blush Cherry Tomato indoors"]
    assert sow["detail"].endswith("Seed on hand: Blush Cherry Tomato (1 packet).")
    assert not [t for t in tasks if t.startswith("Buy ")]

    # Nothing is proposed twice, even after the packet is read again.
    inventory.publish()
    hass.states.async_set(SENSOR, "2", {"inventory_id": "inv1", "again": True})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(manager.plantings) == 1


async def test_declined_packets_stay_declined(hass, freezer):
    freezer.move_to(JAN_10)
    FakeInventory(hass, [{"name": "Tomato", "quantity": 1}])
    entry = _entry(hass)
    await _setup(hass, entry)
    manager = entry.runtime_data
    (carrot,) = manager.plantings.values()
    manager.set_planting_status(carrot.id, "removed", "Not this year")
    manager.async_seed_inventory_changed()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [p.status for p in manager.plantings.values()] == ["removed"]


async def test_a_packet_is_proposed_when_its_day_comes(hass, freezer):
    """Checked daily: an autumn sowing is proposed in its season."""
    freezer.move_to(JAN_10)
    FakeInventory(hass, [{"name": "Garlic", "quantity": 4, "unit": "bulbs"}])
    entry = _entry(hass)
    await _setup(hass, entry)
    assert entry.runtime_data.plantings == {}
    freezer.move_to("2027-08-10 06:00:00+00:00")  # garlic: September 13
    entry.runtime_data.async_seed_inventory_changed()
    await hass.async_block_till_done(wait_background_tasks=True)
    (garlic,) = entry.runtime_data.plantings.values()
    assert (garlic.name, garlic.status) == ("Garlic", "proposed")
