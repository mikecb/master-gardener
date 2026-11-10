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
