# Master Gardener: design and roadmap

A Home Assistant custom integration that acts as the master gardener for a
multi-area property: it knows the garden "rooms", what grows in each, what to
do when, what to buy next, and it adjusts to sensors and to your feedback.

## Decisions made

| Question | Decision |
| --- | --- |
| Where the judgment comes from | Hybrid. A deterministic engine owns dates, thresholds, budget and phasing. An LLM (Claude API, user's key) is used only for design advice. Calendar and alerts never depend on the API. |
| Interface | Native Home Assistant first: devices, calendar, to-do, sensors, actions, notifications, Assist. A custom sidebar panel is a later phase. |
| First slice | Areas + calendar (this document's phase 1). |

## Shape

```
custom_components/master_gardener/
  engine/        pure Python, no HA imports, unit-tested on its own
    data/catalog.json   plants and area styles with their task rules
    schedule.py         areas + plantings + frost dates -> dated tasks
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
- **Planting**: a plant (catalog key or free text) in an area, with quantity,
  planted date and status `active | dead | removed`. Dead plantings are kept:
  they are evidence for future recommendations.
- **Task**: generated, never stored. Only completions are stored, so changing a
  frost date or a catalog rule reshapes the whole calendar. States:
  upcoming, due, overdue, done.
- **Rules**: `anchor` (weeks from a frost date), `after` (days after another
  rule, using the real planting date when known), `interval` (repeats through a
  season, rolling forward from when it was last done).

## Roadmap

1. **Areas + calendar (built).** Areas, plantings, frost-anchored task
   calendar, per-area to-do lists, due/overdue sensors, feedback by adding
   tasks and reporting deaths.
2. **Sensor-driven care.** Attach soil moisture, temperature, conductivity and
   light sensors to areas. Engine rules turn readings plus forecast into
   "water / hold off / feed" tasks and alerts. Frost and heat warnings for
   tender plantings. Measured light hours replace the hand-entered light level.
3. **Site knowledge + advisor.** Pull public data for the property: hardiness
   zone, frost normals (replacing the hand-entered dates), soil survey, sun
   path. A `recommend` action sends one area's full context (style, character,
   light, soil, neighbours, existing and dead plantings, seeds on hand, budget)
   to Claude and returns a plant palette with placement notes and a shopping
   list. Answers "what do I buy for the cottage garden front steps".
4. **Multi-year build plan and budget.** Wishlist items with cost and a
   structural tier (trees and shrubs, then hardscape, then perennials and
   small plants). The engine orders purchases across years under an annual
   budget and user priorities; deaths and overdue feedback reshuffle it.
   Seed and plant inventory.
5. **Adaptive maintenance.** Repeated "overdue" feedback tightens an interval;
   repeated early completion loosens it. Larger, richer plant catalog, possibly
   from an external plant database.
6. **Custom panel.** Property map, per-area planting plans, roadmap and budget
   views.

## Known limits of phase 1

- Northern hemisphere only (spring frost must precede autumn frost).
- The catalog is small (about 40 entries, many of them generic types such as
  "deciduous tree" or "herbaceous perennial"). Unknown plants are tracked but
  get no automatic tasks.
- US only: the site is located by ZIP code. Frost dates are estimated from the
  hardiness zone, which measures winter cold rather than frost timing, so they
  can be a week or two off until corrected by hand. Phase 3 replaces the
  estimate with station frost normals.
- The ZIP code cannot be changed after setup; only the frost dates can.
- Overdue feedback creates a task; it does not yet change the schedule.
