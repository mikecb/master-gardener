# Master Gardener: design and roadmap

A Home Assistant custom integration that acts as the master gardener for a
multi-area property: it knows the garden "rooms", what grows in each, what to
do when, what to buy next, and it adjusts to sensors and to your feedback.

## Decisions made

| Question | Decision |
| --- | --- |
| Where the judgment comes from | Split. A deterministic engine in the integration owns facts, dates, thresholds and (later) budget phasing. Design judgment comes from an external AI agent that reads and writes the garden through the integration's actions, guided by `skills/master-gardener/SKILL.md`. The integration never calls an AI service itself, so it needs no API key and the calendar never depends on one. |
| Interface | Native Home Assistant first: devices, calendar, to-do, sensors, actions, notifications, Assist. A custom sidebar panel is a later phase. |
| First slice | Areas + calendar (this document's phase 1). |

## Shape

```
custom_components/master_gardener/
  engine/        pure Python, no HA imports, unit-tested on its own
    data/catalog.json   our care profiles and area styles with their task rules
    plantdb.py          OpenPlantDB CSV -> local SQLite, lookup and search
    planting_rules.py   one plant record -> sow/plant/harvest rules + care profile
    schedule.py         areas + plantings + frost dates -> dated tasks
    care.py             soil readings -> needs water / needs fertilizer
    weather.py          forecast -> rain hold-off, frost and heat alerts
    plan.py             wishlist + budget -> purchases by year, buy windows
  manager.py     state, persistence (.storage), cache, change notifications
  config_flow.py property frost dates; one config subentry per garden area
  calendar.py / todo.py / sensor.py / services.py   thin HA surface
```

The engine/HA split is the main structural rule: every piece of gardening
logic must be testable without Home Assistant, and later phases (sensor rules,
budget phasing, the advisor's prompt building) go in `engine/` too.

### Data model

- **Site**: ZIP code, the USDA hardiness zone looked up from it, and the
  average last spring frost and first autumn frost (estimated from the zone in
  `engine/climate.py`, correctable by the user). Everything is
  anchored to these two dates, never to calendar months, so the catalog is not
  tied to one climate.
- **Area** (config subentry, shown as a device): name, style, light, size,
  adjacent areas, free-text character. Adjacency and character are collected
  now and used by the advisor in phase 3.
- **Plant catalogue**: OpenPlantDB, downloaded at runtime by
  `plant_store.py` and kept as SQLite in the config folder, so 38,000
  records never sit in memory. Refetched when the integration version
  changes, checked weekly with a conditional request, and replaced only by
  a download that parses and has the expected columns.
- **Planting**: a plant (OpenPlantDB id or free text) in an area, with its
  own copy of the plant's record and a care profile, so the calendar works
  with no catalogue on disk. Also quantity,
  planted date (or `established`: in the ground for years, date unknown,
  which ends a perennial's planting task) and status
  `active | proposed | planned | dead | removed`.
  Dead plantings are kept: they are evidence for future recommendations.
  `planned` is agreed and waiting in the build plan; it carries a priority,
  an optional tier override and earliest year, and once bought the date and
  what it cost.
- **Project**: build-plan work that is not a plant (path, fence, beds), with
  tier (hardscape by default), estimated cost, priority, status
  `planned | done | removed`, and when it was done and for what.
- **Task**: generated, never stored. Only completions are stored, so changing a
  frost date or a catalog rule reshapes the whole calendar. States:
  upcoming, due, overdue, done.
- **Rules**: `anchor` (weeks from a frost date), `after` (days after another
  rule, using the real planting date when known), `interval` (repeats through a
  season, rolling forward from when it was last done).

### Soil care rules

- With several sensors in an area the lowest reading decides.
- Thresholds default by garden style (a vegetable bed wants water at 30%
  moisture, a Mediterranean bed at 12%) and can be set per area. Moisture
  percentages differ between sensor makes and soils, so expect to tune them.
- Advice turns off only once the reading clears the threshold by a margin, so
  a value hovering at the threshold does not flap.
- Fertilizer is only advised in the feeding season (two weeks before the last
  frost until six weeks before the first), and never while the soil is dry:
  conductivity reads low in dry soil whatever the nutrient level.
- A wildflower meadow is never fed by default.
- Ticking a care task hides it for 12 hours (water) or 14 days (fertilizer);
  the binary sensor keeps reporting what the soil says.

### Weather rules

The forecast comes from a weather entity chosen in the options and is read
hourly through `weather.get_forecasts`. `engine/weather.py` folds daily,
twice-daily or hourly entries into one record per local day, in °C and mm.

- Watering waits when at least 5 mm of rain is forecast for today or tomorrow
  (counting only rain at least 50% likely; a forecast with no amounts must be
  70% sure). Soil below 60% of its threshold is watered regardless. While
  waiting, "needs water" is off, so an irrigation automation waits too.
- Frost and heat are looked for today and two days ahead, per area, and only
  for plantings in the ground: active, and for an annual, planted within the
  last year. Perennials and plants the catalogue does not know are assumed
  planted.
- Frost: lows at or below 2 °C threaten `tender` plants, -2 °C `half-hardy`
  ones (the catalogue's `frost_tolerance`). Alerts run from eight weeks before
  the average last frost to four weeks after the average first; in deep winter
  a nightly alert would be noise.
- Heat: highs of 30 °C or more threaten cool-season crops, 35 °C or more
  anything planted in the last six weeks.
- Ticking a frost task hides it for a day, a heat task for three.
- A forecast that cannot be refreshed for six hours is dropped; the frost and
  heat sensors then read unknown rather than a stale "no risk".

### Build plan rules

The plan, like the calendar, is never stored: `engine/plan.py` works it out
from the planned plantings and projects, the yearly budget and what has been
spent this year.

- Four tiers: trees and shrubs, hardscape, perennials, small plants. A
  plant's tier comes from its care profile and type (tree, shrub, conifer,
  hedge), else perennial or not. Within an area nothing is placed in an
  earlier year than an item of an earlier tier; same-year is allowed.
  Areas do not hold each other up.
- Items are taken by priority, then tier, then date added. Each item's
  priority is raised to the highest of the later-tier items in its area, so
  what an urgent item waits on is placed first.
- Each item goes in the first year at or after its floor (this year, its
  `not_before`, the year of what it waits on) with room left. An item dearer
  than the whole budget takes the first untouched year and is flagged.
  Unpriced items take no budget and are counted. No budget (or 0): every
  item is this year.
- This year's items become tasks: a plant's purchase runs the six weeks
  before its first planting window (next season's if that falls before
  January), anything else from four weeks before the last frost to the first
  frost. Purchases made this year stay as completed tasks, so unticking one
  undoes it.

### Adding plantings in the UI

**Add plantings** on the integration page is a config subentry flow that
never creates a subentry: it records plantings with the garden and ends with
a summary. A typed name is taken as is only when it is a catalogue plant's
name (allowing plurals). A name that matched only a catalogue id ("lavender"
is Spanish lavender) is confirmed alongside its genus; anything else is
offered as up to eight candidates from a wider search, ranked by a name that
ends with the typed words, then the genus and species most results share,
then the shortest name. Annuals are always counted as planted this season.

## Roadmap

1. **Areas + calendar (built).** Areas, plantings, frost-anchored task
   calendar, per-area to-do lists, due/overdue sensors, feedback by adding
   tasks and reporting deaths.
2. **Sensor-driven care (mostly built).** Built: each area can name soil
   moisture and conductivity sensors; `engine/care.py` turns the readings into
   "needs water" and "needs fertilizer" advice, shown as binary sensors and as
   to-do items. `engine/weather.py` holds off watering when rain is forecast
   and raises frost and heat alerts for the plantings at risk. Still to do:
   measured light hours replacing the hand-entered light level.
3. **Advisor (built) and site knowledge.** Built: agent-facing actions
   (`get_garden`, `list_tasks`, `complete_task`, filtered `search_plants`,
   area names in place of device ids), `proposed` plantings with a reason and
   an estimated cost, and the gardener skill. An agent answers "what do I buy
   for the cottage garden front steps" by reading the area and its neighbours,
   searching the catalogue and saving proposals. Still to do: pull public data
   for the property (frost normals replacing the zone estimate, soil survey,
   sun path) and expose seeds on hand.
4. **Multi-year build plan and budget (mostly built).** Built: planned
   plantings and projects with cost, priority and a structural tier (trees
   and shrubs, then hardscape, then perennials and small plants); the engine
   orders them across years under an annual budget, and this year's
   purchases appear as to-do items that record what was spent. Any change
   to the wishlist, spending or budget reshuffles the plan. Still to do:
   seed and plant inventory, and replacing a dead planting through the plan
   (today the agent proposes the replacement).
5. **Adaptive maintenance.** Repeated "overdue" feedback tightens an interval;
   repeated early completion loosens it. Care profiles for shrubs and vines.
6. **Custom panel.** Property map, per-area planting plans, roadmap and budget
   views.

## Known limits

- Northern hemisphere only (spring frost must precede autumn frost).
- Plant timing is only as good as OpenPlantDB, which is large but compiled
  largely by automated means and not independently verified.
- About 55% of catalogue plants match no care profile (most shrubs, vines,
  tropicals and annuals) and get planting tasks only.
- For seedlings started indoors the database gives the sowing time only;
  the plant-out date is derived from frost tolerance.
- Its `spring` and `fall` planting anchors are read as the frost dates.
- US only: the site is located by ZIP code. Frost dates are estimated from the
  hardiness zone, which measures winter cold rather than frost timing, so they
  can be a week or two off until corrected by hand. Phase 3 replaces the
  estimate with station frost normals.
- The ZIP code cannot be changed after setup; only the frost dates can.
- Overdue feedback creates a task; it does not yet change the schedule.
- The budget is one amount for every year, in Home Assistant's currency; it
  does not inflate or carry unspent money forward.
- Frost and heat alerts trust the forecast's daily low and high as given, and
  the catalogue's frost tolerance; a plant with no catalogue record gets no
  frost alert.
