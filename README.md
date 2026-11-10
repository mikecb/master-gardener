# Master Gardener for Home Assistant

Plans and tracks the gardens of a property: what to sow, plant, prune, mow and
harvest, area by area, counted from your own frost dates. See
[docs/DESIGN.md](docs/DESIGN.md) for the design and the roadmap.

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

## Weather forecast

Open **Configure** on the integration and pick a **weather forecast** entity
(any Home Assistant weather integration with a daily, twice-daily or hourly
forecast). The forecast is read every hour, and then:

- **Watering waits for rain.** When at least 5 mm (0.2 in) of rain is forecast
  for today or tomorrow, **Needs water** stays off and says why. Soil well
  below its threshold is watered anyway.
- Each area gets a **Frost risk** binary sensor and a **Protect … from frost**
  to-do item when a low forecast within three days threatens what is planted
  there: 2 °C (36 °F) for tender plants, -2 °C (28 °F) for half-hardy ones.
  Frost alerts stop four weeks after your average first autumn frost and
  start again eight weeks before the last spring frost.
- Each area gets a **Heat risk** binary sensor and a **Shield … from heat**
  item when highs reach 30 °C (86 °F) over cool-season crops, or 35 °C (95 °F)
  over anything planted in the last six weeks.

Alerts name the plantings at risk. Only plantings that are in the ground
count: an annual needs a planting date (ticking its **Plant …** task sets it).

## Build plan and budget

A garden is rarely bought in one go. Record what you mean to add as
**planned** plantings (`add_planting` with `status: planned` and an
`estimated_cost`) and **projects** for work that is not a plant, such as a
path, a fence or raised beds (`add_project`). Open **Configure** on the
integration to set a **yearly garden budget**.

Master Gardener places each item in a year:

- **Structure first, area by area:** trees and shrubs, then hardscape, then
  perennials, then small plants. Nothing goes in before something of an
  earlier tier in the same area, so the hostas wait for the path they edge.
  The tier is judged from the plant; set `tier` to override it.
- **Priority** (`high`, `normal`, `low`) orders the whole property. A
  high-priority item pulls whatever it waits on forward with it.
- **Budget:** each year takes items in that order while they fit, and spare
  money goes to smaller items further down. Something dearer than a whole
  year's budget gets a year to itself. `not_before` holds an item back to a
  given year.

**Get build plan** (`master_gardener.get_plan`) returns every year with its
items, what each waits for, and what is spent and left. On the Garden device,
**Budget left this year** and **Planned this year** show the same for the
current year.

This year's items appear on their area's to-do list: **Buy …** for plants, in
the six weeks before their planting window opens, and the project's own name
for projects, through the growing season. Ticking a purchase records it as
bought today at its estimated cost: the planting becomes active and gets its
planting tasks. Unticking puts it back in the plan, and deleting it takes it
off the plan. To record what it really cost, use `set_planting_status` or
`set_project_status` with `cost`.

Spending counts plantings bought from the plan or accepted from a proposal,
and projects marked done. A planting that dies stays counted: the money was
spent. With no budget set, the whole plan falls in this year.

## Actions

Every action returns a response, and anywhere an action takes a garden area
you can give the area's name.

| Action | What it does |
| --- | --- |
| `master_gardener.get_garden` | Climate, frost dates, this year's budget, and every area with its style, light, size, character, neighbours, counts, soil state and weather alerts |
| `master_gardener.list_tasks` | Tasks due now, or coming up, with their ids |
| `master_gardener.complete_task` | Record a task as done |
| `master_gardener.add_task` | A one-off task or a reported problem in an area |
| `master_gardener.search_plants` | Search the catalogue by name, sun, water, type and height |
| `master_gardener.add_planting` | Record a plant as growing, planned or proposed |
| `master_gardener.set_planting_status` | Accept a proposal into the plan or plant it; record a purchase, death or removal |
| `master_gardener.add_project` | Add a path, fence, bed or other non-plant work to the build plan |
| `master_gardener.set_project_status` | Record a project as done (with its cost), or drop it |
| `master_gardener.update_plan_item` | Change an item's priority, tier, earliest year or estimated cost |
| `master_gardener.get_plan` | The build plan, year by year, against the budget |
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
an estimated cost, and get no tasks until you accept them, into the build
plan or straight into the ground. Rejected and dead
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
