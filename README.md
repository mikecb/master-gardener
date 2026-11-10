# Master Gardener for Home Assistant

Plans and tracks the gardens of a property: what to sow, plant, prune, mow and
harvest, area by area, counted from your own frost dates. See
[docs/DESIGN.md](docs/DESIGN.md) for the full roadmap; this is phase 1.

## Install

With HACS: open HACS, choose **Custom repositories** from the menu, add
`https://github.com/mikecb/master-gardener` with type **Integration**, then
download Master Gardener and restart.

Manually: copy `custom_components/master_gardener` into the `custom_components` folder of
your Home Assistant config directory and restart. Requires Home Assistant
2026.8 or newer.

## Set up

1. **Settings > Devices & services > Add integration > Master Gardener.** Enter
   your US ZIP code. It is sent to phzmapi.org to look up your USDA hardiness
   zone, and the next screen shows the frost dates typical for that zone.
   Correct them if you know your local averages (also possible later under
   **Configure**).
2. On the integration page choose **Add garden area** for each area (vegetable
   garden, orchard, cottage garden...). Each becomes a device with:
   - a **calendar** of every task window,
   - a **to-do list** of what needs doing now (tick to record it; add an item
     to report a problem such as "grass too long"),
   - sensors: **Tasks due**, **Tasks overdue**, **Next task**, **Plantings**.
3. Find a plant's id with the **Master Gardener: Search plants** action, then
   record what grows where with **Master Gardener: Add planting**:

   ```yaml
   action: master_gardener.add_planting
   data:
     area: <pick the garden area device>
     plant: tomato
     name: Cherokee Purple tomato
   ```

   With an April 15 last frost this schedules: start indoors from February 18,
   plant out from April 22, harvest from June 21.

The **Garden** device has a calendar and task counts for the whole property.

## Soil sensors

Edit a garden area and pick its **soil moisture** and **soil fertility
(conductivity)** sensors. Any sensor entity with the moisture (%) or
conductivity device class works. The area then gets:

- **Needs water** and **Needs fertilizer** binary sensors, with the reading,
  the threshold and the reason as attributes. Use them in automations, for
  example to notify you or open an irrigation valve.
- a **Water …** or **Fertilize …** item on the area's to-do list while the soil
  needs it. Ticking it hides it for 12 hours (water) or 14 days (fertilizer).

Thresholds default to the garden style and can be set per area. Fertilizer is
only advised in the growing season and never while the soil is dry.

## Actions

Every action returns a response, and anywhere an action takes a garden area
you can give the area's name.

| Action | What it does |
| --- | --- |
| `master_gardener.get_garden` | Climate, frost dates and every area with its style, light, size, character, neighbours, counts and soil state |
| `master_gardener.list_tasks` | Tasks due now, or coming up, with their ids |
| `master_gardener.complete_task` | Record a task as done |
| `master_gardener.add_task` | A one-off task or a reported problem in an area |
| `master_gardener.search_plants` | Search the catalogue by name, sun, water, type and height |
| `master_gardener.add_planting` | Record a plant as growing, planned or proposed |
| `master_gardener.set_planting_status` | Accept or reject a proposal; record a death or removal |
| `master_gardener.list_plantings` | What grows where, optionally by area or status |
| `master_gardener.refresh_catalogue` | Download the latest plant catalogue now |

## Use with an AI gardener

Master Gardener holds the facts and does the date and threshold arithmetic;
it leaves plant selection and design judgment to an AI agent that can call
Home Assistant actions (for example Claude with the Home Assistant MCP
server). Give that agent
[skills/master-gardener/SKILL.md](skills/master-gardener/SKILL.md): it explains
how to read the garden, answer "what should I buy for this spot", run a weekly
check-in, and record what you tell it.

The agent's suggestions are saved as **proposed** plantings, with a reason and
an estimated cost, and get no tasks until you accept them. Rejected and dead
plantings are kept, so later advice can learn from them.

Home Assistant cannot wake the agent: for unprompted reminders, run the agent
on a schedule.

## Plant catalogue

Plants come from [OpenPlantDB](https://github.com/cwfrazier1/openplantdb), an
open (CC0) database of about 38,000 garden plants and cultivars. Master
Gardener downloads it from GitHub (about 15 MB over the wire) the first time
it starts and keeps a local copy in `master_gardener/` in your config folder.
It is downloaded again whenever the integration is updated, checked for
changes once a week, and can be refreshed by hand with **Refresh plant
catalogue**. The **Plant catalogue** sensor on the Garden device shows how
many plants it holds and when it was fetched. Setup does not wait for the
download, and plantings you already have keep working without it.

```yaml
action: master_gardener.search_plants
data:
  query: japanese maple
```

Search returns only plants hardy in your zone unless you ask for all zones.
Use the `id` it returns as the `plant` when adding a planting; an exact name
works too when only one plant has it.

OpenPlantDB supplies each plant's sowing or planting time and, for edibles,
days to harvest. It has nothing on upkeep, so Master Gardener matches each
plant to one of its own **care profiles** for pruning, feeding, mowing and
fruit harvest: apple or pear, stone fruit, blueberry, strawberry, deciduous
tree, Japanese maple, conifer, pine, evergreen hedge, azalea, hydrangea, rose,
lavender, perennial, ornamental grass, spring bulb, lawn, wildflower meadow.
The match is by botanical name and type; override it with `care_profile` when
adding a planting. Shrubs, vines and houseplants outside those groups get
planting tasks only.

A perennial's **Plant …** task comes round each year until you tick it off or
give the planting a planted date.

OpenPlantDB's values are typical ranges, compiled largely by automated means;
treat dates as planning guidance and correct them against local experience.

## Develop

The scheduling engine has no Home Assistant dependency; `tests/ha` loads the
integration into a real Home Assistant core:

```bash
pip install -r requirements-test.txt
python -m unittest discover -s tests -p "test_*.py"   # engine only, needs nothing installed
pytest --cov                              # everything
ruff check . && ruff format --check .
```

### Merge gates

Changes reach `main` through pull requests, and every pull request must pass
four checks (see `.github/workflows/ci.yml`):

| Check | What it guards |
| --- | --- |
| Lint | `ruff` rules and formatting |
| Tests | all tests on the pinned Home Assistant version, with at least 90% coverage |
| Hassfest | Home Assistant's own validation of the manifest, services and translations |
| HACS | the repository stays installable through HACS |

The same checks run every Monday, so a new Home Assistant or HACS rule that
breaks the integration is noticed without a merge. Dependabot proposes updates
to the test harness (and with it the Home Assistant version tested).

### Releasing

Bump `version` in `manifest.json` through a pull request, then tag the merged
commit, e.g. `git tag v0.2.0 && git push origin v0.2.0`. The release workflow
refuses tags that don't match the manifest or aren't on `main`, reruns the
checks, and publishes the GitHub release that HACS offers to users.
