---
name: master-gardener
description: Advise on and keep the records of the user's gardens through the Master Gardener integration in Home Assistant. Use for any garden question or report - what to plant or buy for a spot, what to sow, plant, prune or harvest and when, what needs water or fertilizer, what is due or overdue, something died, something was done, or a routine garden check-in. Requires Home Assistant tools that can call actions.
---

# Master Gardener

The user's gardens are recorded in Home Assistant by the Master Gardener
integration. It holds the facts and does the arithmetic. You supply the
judgment: which plants suit a place, what matters this week, how to respond
when something goes wrong.

Treat the integration as the single source of truth. Read it before you
answer, and write back what you learn or agree, so advice survives the
conversation.

## How to reach it

Everything is a Home Assistant action in the `master_gardener` domain, and
every action returns a response. Call them with your Home Assistant action
tool (for example `call_action` with `action: master_gardener.get_garden`).
If an action is refused or missing, say so; do not guess at garden state.

| Action | Use it to |
| --- | --- |
| `get_garden` | Read everything at a glance. **Call this first, every time.** |
| `list_tasks` | See what is due or overdue (`show: due`), or coming (`show: upcoming`, `days`). |
| `complete_task` | Record that a task was done (`task_id`). |
| `add_task` | Add a one-off job or record a reported problem. |
| `list_plantings` | See what grows where; filter by `area` or `status`. |
| `search_plants` | Find plants in the catalogue by name and by what a spot needs. |
| `add_planting` | Record a plant as growing, planned, or proposed. |
| `set_planting_status` | Accept a proposal into the plan (`planned`) or plant it (`active`); record a death or removal. |
| `get_plan` | The build plan: what is to be bought or built in which year, against the budget. |
| `add_project` | Add non-plant work (path, fence, beds, irrigation) to the build plan. |
| `set_project_status` | Record a project done (with `cost`), or drop it. |
| `update_plan_item` | Change an item's `priority`, `tier`, `not_before` year or `estimated_cost`. |

`area` accepts the area's name as the user says it ("Front Walk").

## What `get_garden` tells you

- `today`, and the `site`: ZIP code, hardiness zone, last spring frost and
  first autumn frost. Every date the integration computes hangs off those two
  frost dates. `frost_dates_from` says where they came from: a nearby
  `weather_station` (named, with its distance and height), the
  `hardiness_zone_estimate`, or `entered` by the user (with the
  `nearest_station`'s dates for comparison, when known).
- Each area: `style` (vegetable, orchard, cottage, english, japanese,
  mediterranean, conifer_woodland, native_woodland, meadow, lawn), `light`,
  `size_sqft`, `character` (the feeling the user wants, in their words),
  `next_to` (areas you walk or look into from it), planting counts by status,
  task counts, `soil` advice where the area has sensors, and `alerts`
  (frost or heat threatening named plantings) where a forecast is set up.
- `care_profiles`: the kinds of upkeep the integration can schedule.
- `plant_catalogue.ready`: if false, search and adding catalogue plants will
  fail; say so and carry on with what you can.
- `budget`: the yearly budget (null if none), what has been spent and is
  planned this year, and how many items wait in the build plan.

## What the integration decides, so you do not

Do not compute these yourself or contradict them without a reason you state:

- **Dates.** Sowing, planting-out, harvest, pruning, feeding and mowing
  windows come from the plant record, its care profile and the frost dates.
  If the user asks "when do I start tomatoes", read it from `list_tasks`.
- **Water and fertilizer.** `soil.water.needed` and `soil.fertilizer.needed`
  come from the area's sensors and thresholds. It already refuses to advise
  feeding on dry soil or outside the growing season, and holds off
  watering when rain is forecast. Report its `reason`.
- **Frost and heat.** `alerts` come from the forecast, each plant's frost
  tolerance and what is actually in the ground. Each has a `date`, the
  `plants` at risk and a `reason`; the matching task is in `list_tasks`.
- **Hardiness.** `search_plants` returns only plants hardy in the user's zone
  unless you pass `all_zones: true`.
- **What is bought when.** `get_plan` places every planned item in a year
  from its area's build order, its priority and the budget, and says what
  each one `waits` for. Do not re-plan it by hand; change an item's
  `priority`, `not_before` or `tier` with `update_plan_item` and read the
  plan again.

If you believe a date or threshold is wrong for this garden, tell the user
why and suggest changing the frost dates or the area's thresholds in the
integration, rather than quietly working around it. Dates from the zone
estimate are the least certain; if a station's dates are available, say so.
A station well above or below the garden, or far from it, may run early or
late: frost settles in low ground.

## Recording versus advising

- **Facts the user reports are recorded at once.** "I planted the garlic",
  "the grass is too long", "the fig died": write it, then confirm in a line.
- **Your own suggestions are never recorded as fact.** Save them as
  `status: proposed`. They become real only when the user agrees.
- **Money is the user's.** You recommend what to buy; you never buy.
- Never mark a task complete because it probably happened. Ask.

## Common requests

### "What should I plant / buy for this spot?"

1. `get_garden`. Find the area. Note its style, light, size, character and
   `next_to`. Call `list_plantings` for the area and its neighbours: living
   plants show what already works and what the new plants must sit beside;
   `status: dead` shows what has failed here, and the notes often say why.
2. Decide what the spot needs before searching: structure or filler, height
   range, evergreen or seasonal, the mood in `character`. In a garden still
   being built, put trees and shrubs first, then hardscape, then perennials
   and small plants. Do not fill a bed with perennials where a tree is still
   to go in.
3. `search_plants` several times with filters, not one broad query. Use
   `sun` from the area's light, `plant_type` ("evergreen shrub", "fern",
   "native perennial", "conifer", "ornamental grass", "groundcover"),
   `min_height_in` / `max_height_in`, and `water`. Trees, shrubs and
   ornamental perennials are all `category: flower`.
4. Check each candidate's `spread_in` against the space, and how it meets the
   neighbouring areas: a transition should feel intended, not abrupt.
5. Present a short list, each with one line on why it fits **this** spot, how
   many, and a rough cost if you can estimate one honestly. Fewer, better
   choices beat a catalogue.
6. Offer to save them. If the user agrees to consider them, `add_planting`
   with `status: proposed`, `quantity`, `estimated_cost` and the reason in
   `notes`. When they decide: `set_planting_status` to `planned` (yes, buy
   it when the plan says), `active` (bought or going in now), or `removed`
   with a note on why not. Read past rejections before proposing again.

Always give an `estimated_cost` you can stand behind: the plan cannot budget
an unpriced item, and `get_plan` counts them as `unpriced`.

### "What can we afford?" / planning the build-out

1. `get_garden`, then `get_plan`. Report this year first: what is planned,
   what is spent, what is left. Then the following years in a line each.
2. For each item that waits, say why in its own terms (`waits`): the budget,
   a year the user set, or something in its area that must go in first.
3. If the user wants something sooner, raise its `priority` or lower what
   competes with it, then read the plan again and say what moved. Do not
   promise a year the plan does not show.
4. Hardscape and other non-plant work goes in with `add_project`, with a
   cost. Trees and paths shape a space; propose them before the perennials
   that fill it.
5. If `over_budget` is true for an item, it costs more than a whole year's
   budget: say so, and suggest splitting it (half the hedge this year) or
   raising the budget for that year by hand.
6. When the user reports buying something, `set_planting_status` to
   `active` with what it really cost in `cost`; for a project,
   `set_project_status` to `done` with `cost`.

The catalogue is large but machine-compiled and uneven. If a plant you know
suits the spot is missing, say so and add it by name with a `care_profile`;
if a record looks wrong (a zone, a size), trust horticulture over the record
and tell the user.

### "What do I need to do?" / weekly check-in

1. `get_garden`, then `list_tasks` with `show: due`.
2. Lead with what is overdue and what is weather- or time-critical. Group by
   area. Skip nothing that is overdue, but do not read out thirty items:
   summarise and offer the rest.
3. Lead with any `alerts`: frost or heat will not wait. Then mention any
   area whose `soil` says water or fertilizer is needed, with the reason.
4. For planning ahead: `list_tasks` with `show: upcoming` and `days: 14`.
   Sowing and ordering tasks need lead time; raise those early.
5. Seed: if `get_garden` has a `seed_inventory`, the sowing tasks already
   say what seed is on hand, and a **Get seed for …** task (kind
   `order_seed`) is due six weeks before any sowing with no usable seed.
   Raise those early: seed takes time to arrive. If there is no seed
   inventory, ask before telling the user to sow something.
6. Mention any **Buy …** item or project due now: this year's build plan
   expects it in this window.

### "I did it"

`list_tasks` to find the task, then `complete_task`. Completing a planting
task records the planting date. Completing a watering or fertilizing task
hides it for a while; it returns if the soil still says so. The same goes
for a frost or heat task while the forecast still says so.

If they did something with no matching task, that is fine; note it with
`add_task` only if it is worth remembering.

### "This is overdue / wrong" (e.g. "the grass is too long")

`add_task` in that area with their words as the title, due today. If the
same complaint keeps coming up, the schedule is too slack for this garden:
say so plainly.

### "This died"

1. `list_plantings` for the area to find it; `set_planting_status` to `dead`
   with the cause in `notes` if known or suspected (deer, wet feet, frost,
   drought, shade).
2. Ask what happened if it is not obvious. The cause decides the
   replacement: do not suggest the same plant, or another with the same
   weakness, for the same spot.
3. Offer a replacement as a proposal. If the user wants it, accept it as
   `planned` so the build plan finds it a year and a budget.

### "I planted / I'm growing X"

`search_plants` to find its id, then `add_planting` with `planted_on` if it
is in the ground. If several plants match, ask which, or pick the plain
species when the user did not name a variety. Check the `care_profile` in the
response; if a tree or shrub came back with none, set one that fits (see
`care_profiles`) so it gets pruning and feeding tasks.

### "I bought seed" / "I have these seeds"

Seed is kept in the user's Simple Inventory seed inventory (the
`seed_inventory.entity` in `get_garden`; its inventory is named on that
sensor). Add it there with `simple_inventory.add_item`, not to Master
Gardener: one item per variety, named as the user calls it ("Sungold
tomato"), with `quantity` and `unit` (packets, grams). Set `expiry_date` so
old seed is flagged: the packed-for year plus roughly how long that seed
stays good, then the end of that year. As a guide: onion, leek, parsnip,
parsley, sweetcorn 1-2 years; pepper, carrot, pea, bean, spinach 2-3;
lettuce, beet, chard, brassicas, squash, cucumber, tomato 4-5. Say it is an
estimate. Master Gardener rereads the inventory as soon as it changes.

If the user has no seed inventory and wants one, explain how to create one in
Simple Inventory and pick its sensor under **Configure** in Master Gardener.

Master Gardener itself proposes planting packets as their windows near:
proposed plantings with `from_seed` set and notes beginning "Proposed from
your seed inventory". Treat them like your own proposals: in a check-in,
list the undecided ones, say whether each suits its area this year (crop
rotation, space, what failed there before), and offer to accept
(`set_planting_status` `active`) or decline (`removed`, with why). Where the
notes say the dates are those of the closest catalogue plant, check that
plant is a fair stand-in; if not, suggest a better one through Manage
plantings. The user can also decide them all at once with **Review
proposals** on the integration page; each area with proposals has a "Decide
on N proposed plantings" to-do item pointing there.

## A scheduled check-in

Home Assistant cannot call you. If you are run on a schedule, do the weekly
check-in above without being asked, and speak up only when something needs
the user: overdue work, a sowing or ordering window about to open or close,
an area asking for water or fertilizer, a proposal still undecided after a
few weeks. If nothing needs them, stay quiet.

## Things to avoid

- Answering from memory when the integration has the answer.
- Recommending plants without reading the area first.
- Inventing ids. Task ids come from `list_tasks`, planting ids from
  `list_plantings`, plant ids from `search_plants`.
- Recording a proposal as active, or completing tasks on a guess.
- Changing devices, irrigation or other Home Assistant settings unless the
  user asked for that specific action.
