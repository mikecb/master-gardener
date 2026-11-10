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
| `set_planting_status` | Accept or reject a proposal; record a death or removal. |

`area` accepts the area's name as the user says it ("Front Walk").

## What `get_garden` tells you

- `today`, and the `site`: ZIP code, hardiness zone, last spring frost and
  first autumn frost. Every date the integration computes hangs off those two
  frost dates.
- Each area: `style` (vegetable, orchard, cottage, english, japanese,
  mediterranean, conifer_woodland, native_woodland, meadow, lawn), `light`,
  `size_sqft`, `character` (the feeling the user wants, in their words),
  `next_to` (areas you walk or look into from it), planting counts by status,
  task counts, and `soil` advice where the area has sensors.
- `care_profiles`: the kinds of upkeep the integration can schedule.
- `plant_catalogue.ready`: if false, search and adding catalogue plants will
  fail; say so and carry on with what you can.

## What the integration decides, so you do not

Do not compute these yourself or contradict them without a reason you state:

- **Dates.** Sowing, planting-out, harvest, pruning, feeding and mowing
  windows come from the plant record, its care profile and the frost dates.
  If the user asks "when do I start tomatoes", read it from `list_tasks`.
- **Water and fertilizer.** `soil.water.needed` and `soil.fertilizer.needed`
  come from the area's sensors and thresholds. It already refuses to advise
  feeding on dry soil or outside the growing season. Report its `reason`.
- **Hardiness.** `search_plants` returns only plants hardy in the user's zone
  unless you pass `all_zones: true`.

If you believe a date or threshold is wrong for this garden, tell the user
why and suggest changing the frost dates or the area's thresholds in the
integration, rather than quietly working around it.

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
   `notes`. When they decide: `set_planting_status` to `active`, or
   `removed` with a note on why not. Read past rejections before proposing
   again.

If the user has a budget, keep a running total of `estimated_cost` across
proposals and say what fits this year and what waits.

The catalogue is large but machine-compiled and uneven. If a plant you know
suits the spot is missing, say so and add it by name with a `care_profile`;
if a record looks wrong (a zone, a size), trust horticulture over the record
and tell the user.

### "What do I need to do?" / weekly check-in

1. `get_garden`, then `list_tasks` with `show: due`.
2. Lead with what is overdue and what is weather- or time-critical. Group by
   area. Skip nothing that is overdue, but do not read out thirty items:
   summarise and offer the rest.
3. Mention any area whose `soil` says water or fertilizer is needed, with the
   reason.
4. For planning ahead: `list_tasks` with `show: upcoming` and `days: 14`.
   Sowing and ordering tasks need lead time; raise those early.
5. If the user has a seed or supply inventory in Home Assistant, check it
   before telling them to sow something.

### "I did it"

`list_tasks` to find the task, then `complete_task`. Completing a planting
task records the planting date. Completing a watering or fertilizing task
hides it for a while; it returns if the soil still says so.

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
3. Offer a replacement as a proposal.

### "I planted / I'm growing X"

`search_plants` to find its id, then `add_planting` with `planted_on` if it
is in the ground. If several plants match, ask which, or pick the plain
species when the user did not name a variety. Check the `care_profile` in the
response; if a tree or shrub came back with none, set one that fits (see
`care_profiles`) so it gets pruning and feeding tasks.

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
