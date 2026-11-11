# Master Gardener: design and roadmap

A Home Assistant custom integration that acts as the master gardener for a
multi-area property: it knows the garden "rooms", what grows in each, what to
do when, what to buy next, and it adjusts to sensors and to your feedback.

## Decisions made

| Question | Decision |
| --- | --- |
| Where the judgment comes from | Split. A deterministic engine in the integration owns facts, dates, thresholds and (later) budget phasing. Design judgment comes from an external AI agent that reads and writes the garden through the integration's actions, guided by `skills/master-gardener/SKILL.md`. The integration never calls an AI service itself, so it needs no API key and the calendar never depends on one. |
| Interface | Native Home Assistant first: devices, calendar, to-do, sensors, actions, notifications, Assist. The Garden sidebar panel (`panel.py`, `frontend/master-gardener-panel.js`) is a view over the same actions: one plain JavaScript module, no build step, no API of its own. |
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

- **Site**: ZIP code, the USDA hardiness zone and location looked up from
  it, and the average last spring frost and first autumn frost: from the
  nearest NOAA station's 1991-2020 normals (`engine/frost_normals.py`), else
  estimated from the zone (`engine/climate.py`), always correctable by the
  user. The station consulted is kept with the entry, and `get_garden`
  reports whether the dates in use are the station's, the zone estimate's,
  or entered by hand. Everything is
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

### Seed rules

Seed lives in a Simple Inventory inventory chosen in the options; Master
Gardener only reads it (`simple_inventory.get_items`), again whenever the
inventory's sensor changes and once the plant catalogue is ready.
`engine/seeds.py` then works on the generated tasks:

- Only open sowings count (`sow_indoors`, `direct_sow`, not done, not past),
  and only for active plantings.
- A seed item matches a planting by catalogue plant (same plant; or the same
  species when the planting is not a named variety) or by name: the
  planting's name or its plant's common name, as a whole word, optionally
  plural, in the item's name or an alias.
- Usable seed has a quantity above zero (or none recorded) and no expiry
  date in the past. It is named in the sowing task's detail.
- With none usable, a "Get seed" task runs from six weeks before the sowing
  window opens to the day before, saying whether there is no seed or only
  expired seed. It is completed like any task, and disappears once seed is
  entered. Without a seed inventory chosen there are no seed tasks at all.
- A failed read keeps the last good one; no read yet means no seed tasks.
- A packet's catalogue plant is the one its name resolves to, or else the
  likeliest plant for its name without packet words (mix, organic,
  heirloom...) and then for ever shorter tails of it ("Blush Cherry Tomato":
  cherry tomato, tomato), which must be the kind the name ends with (a
  mustard mix must be a mustard). Such a guess is flagged.
- Proposals: a usable packet with a catalogue plant, not yet proposed (the
  planting keeps `from_seed`), not covered by a growing or planned planting,
  and with its sowing or planting window opening within six weeks, is
  proposed in the first area whose style suits its category. Checked when
  the inventory changes, when the catalogue arrives, and daily. Accepting a
  seed proposal records no purchase.

### Adaptive maintenance rules

`engine/adapt.py` keeps, per repeating series, the signals since its last
change: each completion (early: before the task's window; late: after it;
on time) and each reported problem that names the job (a custom task in the
area whose words match the job's kind). Two "sooner" signals (reports or
early completions) within 60 days multiply the interval by 0.85; three late
completions in a row with no report multiply it by 1.15; either uses the
signals up. The factor stays within 0.5-2.0 of the profile's interval and
is stored with its reason, which the task's detail repeats. Doing the job
late is read as "not needed yet" rather than "too busy": if that is wrong,
the complaints that follow tighten it again.

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

**Manage plantings** is a second such flow: area (skipped when only one has
plantings), then planting, then a menu of what can happen to it given its
status (active: died, removed, edit; planned: bought, dropped, edit;
proposed: accept into the plan, plant now, decline, edit). Dead and removed
plantings are not offered: they are history.

**Review proposals** is a third: one form per area listing up to forty
proposals, each with its first window ("start indoors from Jan 21") and
notes, and a choice of later, grow, build plan or decline; a seed proposal
has no build-plan choice, its seed being on hand. Proposals are surfaced as
one generated to-do item per area, due when the earliest window opens (or in
two weeks when none is known), snoozed for a week when ticked.

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
   searching the catalogue and saving proposals. Built too: frost dates from
   the nearest weather station's climate normals, and seed on hand from a
   Simple Inventory inventory. Still to do: more public data for the
   property (soil survey, sun path).
4. **Multi-year build plan and budget (mostly built).** Built: planned
   plantings and projects with cost, priority and a structural tier (trees
   and shrubs, then hardscape, then perennials and small plants); the engine
   orders them across years under an annual budget, and this year's
   purchases appear as to-do items that record what was spent. Any change
   to the wishlist, spending or budget reshuffles the plan. Seed inventory:
   see phase 3. Still to do: replacing a dead planting through the plan
   (today the agent proposes the replacement).
5. **Adaptive maintenance (built).** Repeating jobs adapt their interval
   (`engine/adapt.py`): sooner after reported problems that name them or
   after doing them early, later after repeatedly doing them late. Care
   profiles cover shrubs, vines and soft fruit (grape, bramble, currant,
   fig), and plantings without one are matched again at start-up.
6. **Custom panel (built).** The Garden panel: a property map (areas as
   tiles on a 24-column grid, dragged and resized in edit mode, each
   area's rectangle stored with the garden and set through
   `set_area_layout`), areas, per-area planting plans with proposals
   decided in place, the build plan against the budget, and tasks.

## Known limits

- Northern hemisphere only (spring frost must precede autumn frost).
- Plant timing is only as good as OpenPlantDB, which is large but compiled
  largely by automated means and not independently verified.
- About 43% of catalogue plants match no care profile (mostly tropicals,
  houseplants, succulents, orchids and annuals) and get planting tasks only.
  Shrubs and vines are matched by type words; where pruning depends on
  something the catalogue does not record (old or new wood, a clematis
  group) the task's detail says how to tell.
- For seedlings started indoors the database gives the sowing time only;
  the plant-out date is derived from frost tolerance.
- Its `spring` and `fall` planting anchors are read as the frost dates.
- US only: the site is located by ZIP code, and station normals are NOAA's.
- Station frost dates are the nearest station's, up to about 110 km away,
  chosen by distance alone. Frost pools in hollows and lifts on slopes, so
  stations a few miles apart can differ by two weeks; the form names the
  station, its distance and height, so the user can judge. Where no station
  qualifies the zone estimate is used, which can be a week or two off.
- The ZIP code cannot be changed after setup; only the frost dates can.
- Only repeating jobs (mowing, deadheading) adapt; once-a-year jobs such as
  pruning keep their frost-anchored dates. Reports are matched to a job by
  a few words ("grass", "lawn", "mow"; "deadhead", "spent", "faded").
- The budget is one amount for every year, in Home Assistant's currency; it
  does not inflate or carry unspent money forward.
- Frost and heat alerts trust the forecast's daily low and high as given, and
  the catalogue's frost tolerance; a plant with no catalogue record gets no
  frost alert.
