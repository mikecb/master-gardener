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
   your average last spring frost and first autumn frost.
2. On the integration page choose **Add garden area** for each area (vegetable
   garden, orchard, cottage garden...). Each becomes a device with:
   - a **calendar** of every task window,
   - a **to-do list** of what needs doing now (tick to record it; add an item
     to report a problem such as "grass too long"),
   - sensors: **Tasks due**, **Tasks overdue**, **Next task**, **Plantings**.
3. Record what grows where with the **Master Gardener: Add planting** action:

   ```yaml
   action: master_gardener.add_planting
   data:
     area: <pick the garden area device>
     plant: tomato
     name: Cherokee Purple tomato
   ```

   With an April 15 last frost this schedules: start indoors from February 18,
   plant out from April 22, harvest from July 1.

The **Garden** device has a calendar and task counts for the whole property.

## Other actions

- `master_gardener.set_planting_status`: mark a planting `dead` or `removed`.
- `master_gardener.add_task`: a one-off task in an area.
- `master_gardener.list_plantings`: everything recorded, with ids.

## Catalog

Vegetables and herbs: tomato, pepper, eggplant, broccoli, cabbage, kale, onion,
lettuce, spinach, peas, radish, carrot, beet, potato, beans, cucumber, zucchini,
winter squash, sweet corn, garlic, basil. Fruit: strawberry, blueberry, apple
(and pear), peach (and other stone fruit). Ornamentals: deciduous tree, Japanese
maple, conifer, pine, boxwood, azalea, hydrangea, rose, lavender, perennial,
ornamental grass, spring bulbs, lawn, wildflower meadow. Common names such as
"hosta", "oak" or "daffodils" map to the matching type.

## Develop

The scheduling engine has no Home Assistant dependency; `tests/ha` loads the
integration into a real Home Assistant core:

```bash
python -m unittest discover tests   # engine only, no dependencies
pytest                              # everything; needs pytest-homeassistant-custom-component
```
