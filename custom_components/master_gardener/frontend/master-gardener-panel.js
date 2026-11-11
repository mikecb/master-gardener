// Master Gardener: the Garden panel.
//
// One plain module, no build step. Everything shown comes from the
// integration's actions (get_garden, list_plantings, get_plan, list_tasks),
// and everything done goes through them (complete_task, set_planting_status),
// so the panel sees and changes exactly what an automation or agent would.

const DOMAIN = "master_gardener";
const REFRESH_MS = 120000;

const VIEWS = [
  ["map", "Map"],
  ["areas", "Areas"],
  ["plantings", "Plantings"],
  ["plan", "Build plan"],
  ["tasks", "Tasks"],
];
const STYLES = {
  vegetable: "Vegetable garden",
  orchard: "Orchard",
  cottage: "Cottage garden",
  english: "English garden",
  japanese: "Japanese garden",
  mediterranean: "Mediterranean",
  conifer_woodland: "Evergreen woodland",
  native_woodland: "Native woodland",
  meadow: "Wildflower meadow",
  lawn: "Lawn",
  other: "Other",
};
// The property map: a grid this many cells wide (see const.py).
const MAP_COLUMNS = 24;
const MAP_MIN_ROWS = 12;
const MAP_MIN_SIZE = 2;
const STYLE_COLORS = {
  vegetable: "#4caf50",
  orchard: "#a1887f",
  cottage: "#f06292",
  english: "#9575cd",
  japanese: "#e57373",
  mediterranean: "#ffb74d",
  conifer_woodland: "#2e7d32",
  native_woodland: "#827717",
  meadow: "#aed581",
  lawn: "#66bb6a",
  other: "#90a4ae",
};
const LIGHTS = {
  full_sun: "Full sun",
  part_shade: "Part shade",
  shade: "Shade",
  mixed: "Mixed light",
};
const STATUS_GROUPS = [
  ["proposed", "Proposed: waiting for a decision"],
  ["active", "Growing"],
  ["planned", "Planned: in the build plan"],
  ["dead", "Died"],
  ["removed", "Removed or turned down"],
];
const TIERS = {
  trees_shrubs: "Trees & shrubs",
  hardscape: "Hardscape",
  perennials: "Perennials",
  small_plants: "Small plants",
};

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
const short = (name) => String(name ?? "").split(" (")[0];
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function day(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function money(value, currency) {
  if (value === null || value === undefined) return "";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "USD",
      maximumFractionDigits: 0,
    }).format(value);
  } catch (_err) {
    return `${Math.round(value)}`;
  }
}

class MasterGardenerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._view = "map";
    try {
      // The last tab used, across visits (only a convenience).
      const saved = localStorage.getItem("master-gardener-view");
      if (VIEWS.some(([key]) => key === saved)) this._view = saved;
    } catch (_err) {
      /* no storage: start on the map */
    }
    this._editing = false;
    this._drag = null;
    this._area = "";
    this._data = null;
    this._error = "";
    this._busy = false;
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this.shadowRoot.addEventListener("change", (ev) => this._onChange(ev));
    this.shadowRoot.addEventListener("pointerdown", (ev) => this._dragStart(ev));
    this.shadowRoot.addEventListener("pointermove", (ev) => this._dragMove(ev));
    this.shadowRoot.addEventListener("pointerup", (ev) => this._dragEnd(ev));
    this.shadowRoot.addEventListener("pointercancel", () => this._dragCancel());
    this.shadowRoot.addEventListener("keydown", (ev) => {
      // Tiles are buttons: Enter and Space open them.
      if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches('[role="button"]')) {
        ev.preventDefault();
        ev.target.click();
      }
    });
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    this._render();
  }

  connectedCallback() {
    this._timer = setInterval(() => this._load(), REFRESH_MS);
    this._render();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  async _call(service, data = {}) {
    const result = await this._hass.connection.sendMessagePromise({
      type: "call_service",
      domain: DOMAIN,
      service,
      service_data: data,
      return_response: true,
    });
    return result.response;
  }

  async _load() {
    if (!this._hass) return;
    try {
      const [garden, plantings, plan, tasks] = await Promise.all([
        this._call("get_garden"),
        this._call("list_plantings"),
        this._call("get_plan"),
        this._call("list_tasks", { show: "all", days: 30 }),
      ]);
      this._data = { garden, plantings: plantings.plantings, plan, tasks: tasks.tasks };
      this._error = "";
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._render();
  }

  async _act(service, data) {
    if (this._busy) return;
    this._busy = true;
    this._render();
    try {
      await this._call(service, data);
      await this._load();
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _onClick(ev) {
    const el = ev.target.closest("[data-action]");
    if (!el) return;
    const { action } = el.dataset;
    if (action === "view") {
      this._view = el.dataset.view;
      try {
        localStorage.setItem("master-gardener-view", this._view);
      } catch (_err) {
        /* not kept: fine */
      }
      this._render();
    } else if (action === "menu") {
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    } else if (action === "status") {
      this._act("set_planting_status", {
        planting_id: el.dataset.id,
        status: el.dataset.status,
      });
    } else if (action === "complete") {
      this._act("complete_task", { task_id: el.dataset.id });
    } else if (action === "edit-map") {
      this._editing = !this._editing;
      this._render();
    } else if (action === "area") {
      if (this._editing) return;
      this._area = el.dataset.area;
      this._view = "plantings";
      this._render();
    }
  }

  _onChange(ev) {
    if (ev.target.id === "area-filter") {
      this._area = ev.target.value;
      this._render();
    }
  }

  // --- property map ----------------------------------------------------------

  /** Every area's cell: saved ones as saved; the rest in the first free
   *  space from the top left, so placing one area never moves another. */
  _cells() {
    const areas = this._data.garden.areas;
    const cells = {};
    for (const area of areas) {
      if (area.layout) {
        const { x, y, width, height } = area.layout;
        cells[area.id] = { x, y, w: width, h: height, placed: true };
      }
    }
    const taken = () => Object.values(cells);
    const free = (x, y, w, h) =>
      taken().every((c) => x >= c.x + c.w || c.x >= x + w || y >= c.y + c.h || c.y >= y + h);
    for (const area of areas) {
      if (cells[area.id]) continue;
      const side = Math.sqrt(area.size_sqft || 200) / 4;
      const w = Math.max(3, Math.min(12, Math.round(side * 1.4)));
      const h = Math.max(MAP_MIN_SIZE, Math.min(8, Math.round(side)));
      let spot = null;
      for (let y = 0; !spot; y += 1) {
        for (let x = 0; x + w <= MAP_COLUMNS; x += 1) {
          if (free(x, y, w, h)) {
            spot = { x, y };
            break;
          }
        }
      }
      cells[area.id] = { ...spot, w, h, placed: false };
    }
    return cells;
  }

  _mapView() {
    const { garden } = this._data;
    if (!garden.areas.length) return this._areasView();
    const cells = this._drag ? this._drag.cells : this._cells();
    const bottom = Math.max(...Object.values(cells).map((c) => c.y + c.h));
    const rows = Math.max(MAP_MIN_ROWS, bottom + (this._editing ? 4 : 1));
    const centre = (c) => [c.x + c.w / 2, c.y + c.h / 2];
    const byName = Object.fromEntries(garden.areas.map((a) => [a.name, a]));
    const seen = new Set();
    const lines = [];
    for (const area of garden.areas) {
      for (const other of area.next_to) {
        const b = byName[other];
        const key = [area.id, b?.id].sort().join("|");
        if (!b || seen.has(key)) continue;
        seen.add(key);
        const [x1, y1] = centre(cells[area.id]);
        const [x2, y2] = centre(cells[b.id]);
        lines.push(`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`);
      }
    }
    const tiles = garden.areas
      .map((area) => {
        const c = cells[area.id];
        const badges = [
          area.tasks_overdue ? `<span class="badge bad" title="Overdue">${area.tasks_overdue}!</span>` : "",
          area.tasks_due - area.tasks_overdue > 0
            ? `<span class="badge" title="Due">${area.tasks_due - area.tasks_overdue} due</span>`
            : "",
          area.plantings.proposed ? `<span class="badge note" title="Proposed">${area.plantings.proposed} new</span>` : "",
          (area.alerts || []).length ? `<span class="badge bad" title="${esc(area.alerts.map((a) => a.reason).join("; "))}">⚠</span>` : "",
        ].join("");
        const color = STYLE_COLORS[area.style] || STYLE_COLORS.other;
        const pos = `left:${(c.x / MAP_COLUMNS) * 100}%;top:${(c.y / rows) * 100}%;width:${(c.w / MAP_COLUMNS) * 100}%;height:${(c.h / rows) * 100}%;--tile:${color}`;
        return `<div class="tile ${c.placed ? "" : "unplaced"}" style="${pos}" data-id="${esc(area.id)}"
            ${this._editing ? "" : `role="button" tabindex="0" data-action="area" data-area="${esc(area.name)}"`}
            aria-label="${esc(area.name)}: ${esc(STYLES[area.style] || area.style)}">
          <span class="tile-name">${esc(area.name)}</span>
          <span class="tile-meta">${esc(STYLES[area.style] || area.style)} · ${area.plantings.active} plants</span>
          <span class="badges">${badges}</span>
          ${this._editing ? `<span class="resize" data-resize="1" aria-hidden="true"></span>` : ""}
        </div>`;
      })
      .join("");
    const unplaced = Object.values(cells).some((c) => !c.placed);
    const hint = this._editing
      ? "Drag an area to move it; drag its corner to resize. Each change is saved as you let go."
      : unplaced
        ? "Areas with dashed edges are not placed yet: they sit wherever there is room. Use Edit layout to arrange them like your property."
        : "Tap an area for its plantings.";
    return `<div class="toolbar map-toolbar">
        <span class="lead">${hint}</span>
        <button class="${this._editing ? "" : "quiet"}" data-action="edit-map">${this._editing ? "Done" : "Edit layout"}</button>
      </div>
      <div class="map ${this._editing ? "editing" : ""}" style="aspect-ratio:${MAP_COLUMNS} / ${rows};--rows:${rows}" data-rows="${rows}">
        <svg class="links" viewBox="0 0 ${MAP_COLUMNS} ${rows}" preserveAspectRatio="none" aria-hidden="true">${lines.join("")}</svg>
        ${tiles}
      </div>`;
  }

  _dragStart(ev) {
    if (!this._editing || ev.button > 0) return;
    const tile = ev.target.closest(".tile");
    const map = tile?.closest(".map");
    if (!tile || !map) return;
    ev.preventDefault();
    const cells = this._cells();
    const id = tile.dataset.id;
    const rect = map.getBoundingClientRect();
    this._drag = {
      id,
      resize: Boolean(ev.target.dataset.resize),
      start: { x: ev.clientX, y: ev.clientY },
      from: { ...cells[id] },
      cell: rect.width / MAP_COLUMNS,
      rows: Number(map.dataset.rows),
      cells,
      moved: false,
    };
    tile.setPointerCapture(ev.pointerId);
    tile.classList.add("dragging");
  }

  _dragMove(ev) {
    const drag = this._drag;
    if (!drag) return;
    const dx = Math.round((ev.clientX - drag.start.x) / drag.cell);
    const dy = Math.round((ev.clientY - drag.start.y) / drag.cell);
    const c = { ...drag.from, placed: true };
    if (drag.resize) {
      c.w = Math.max(MAP_MIN_SIZE, Math.min(MAP_COLUMNS - c.x, drag.from.w + dx));
      c.h = Math.max(MAP_MIN_SIZE, drag.from.h + dy);
    } else {
      c.x = Math.max(0, Math.min(MAP_COLUMNS - c.w, drag.from.x + dx));
      c.y = Math.max(0, drag.from.y + dy);
    }
    const prev = drag.cells[drag.id];
    if (prev.x === c.x && prev.y === c.y && prev.w === c.w && prev.h === c.h) return;
    drag.cells[drag.id] = c;
    drag.moved = true;
    const tile = this.shadowRoot.querySelector(`.tile[data-id="${CSS.escape(drag.id)}"]`);
    if (tile) {
      tile.style.left = `${(c.x / MAP_COLUMNS) * 100}%`;
      tile.style.top = `${(c.y / drag.rows) * 100}%`;
      tile.style.width = `${(c.w / MAP_COLUMNS) * 100}%`;
      tile.style.height = `${(c.h / drag.rows) * 100}%`;
    }
  }

  async _dragEnd() {
    const drag = this._drag;
    if (!drag) return;
    const c = drag.cells[drag.id];
    if (!drag.moved && drag.from.placed) {
      this._drag = null;
      this._render();
      return;
    }
    // Keep showing where it was dropped while it saves.
    const area = this._data.garden.areas.find((a) => a.id === drag.id);
    if (area) area.layout = { x: c.x, y: c.y, width: c.w, height: c.h };
    this._drag = null;
    this._render();
    try {
      await this._call("set_area_layout", {
        area: drag.id,
        x: c.x,
        y: c.y,
        width: c.w,
        height: c.h,
      });
    } catch (err) {
      this._error = err?.message || String(err);
    }
    await this._load();
  }

  _dragCancel() {
    this._drag = null;
    this._render();
  }

  // --- views ---------------------------------------------------------------

  _areasView() {
    const { garden } = this._data;
    if (!garden.areas.length) {
      return `<p class="empty">No garden areas yet. Add one under Settings &rsaquo; Devices &amp; services &rsaquo; Master Gardener.</p>`;
    }
    const site = garden.site;
    const from = site.frost_dates_from?.source;
    const source =
      from === "weather_station"
        ? `from ${esc(site.frost_dates_from.station.name)}`
        : from === "hardiness_zone_estimate"
          ? "estimated from your hardiness zone"
          : "as you entered them";
    const header = `<p class="lead">Last spring frost <b>${day(`2001-${site.last_spring_frost}`)}</b>,
      first autumn frost <b>${day(`2001-${site.first_autumn_frost}`)}</b> (${source}).</p>`;
    const cards = garden.areas
      .map((area) => {
        const counts = area.plantings;
        const chips = [
          area.tasks_overdue ? `<span class="chip bad">${plural(area.tasks_overdue, "overdue task")}</span>` : "",
          area.tasks_due ? `<span class="chip">${plural(area.tasks_due, "task")} due</span>` : "",
          `<span class="chip">${plural(counts.active, "planting")}</span>`,
          counts.proposed ? `<span class="chip note">${counts.proposed} proposed</span>` : "",
          counts.planned ? `<span class="chip">${counts.planned} planned</span>` : "",
        ].join("");
        const soil = Object.entries(area.soil || {})
          .filter(([, s]) => s.needed)
          .map(([, s]) => `<li class="warn">${esc(s.reason)}</li>`);
        const alerts = (area.alerts || []).map(
          (a) => `<li class="warn">${esc(a.reason)} (${day(a.date)})</li>`,
        );
        const adjusted = (area.schedule_adjustments || []).map(
          (a) => `<li>${esc(a.task)}: ${esc(a.why)}</li>`,
        );
        const notes = [...alerts, ...soil, ...adjusted].join("");
        const meta = [
          STYLES[area.style] || area.style,
          LIGHTS[area.light] || "",
          area.size_sqft ? `${Math.round(area.size_sqft)} ft²` : "",
        ]
          .filter(Boolean)
          .join(" · ");
        return `<ha-card class="area">
          <div class="card-body">
            <h2>${esc(area.name)}</h2>
            <div class="meta">${esc(meta)}</div>
            ${area.character ? `<p class="character">${esc(area.character)}</p>` : ""}
            <div class="chips">${chips}</div>
            ${notes ? `<ul class="notes">${notes}</ul>` : ""}
            ${area.next_to.length ? `<div class="meta">Next to ${area.next_to.map(esc).join(", ")}</div>` : ""}
          </div>
          <div class="card-actions">
            <button class="link" data-action="area" data-area="${esc(area.name)}">Plantings</button>
          </div>
        </ha-card>`;
      })
      .join("");
    return `${header}<div class="grid">${cards}</div>`;
  }

  _plantingsView() {
    const { garden, plantings } = this._data;
    const areas = garden.areas.map((a) => a.name);
    const options = [`<option value="">All areas</option>`]
      .concat(
        areas.map(
          (name) =>
            `<option value="${esc(name)}" ${name === this._area ? "selected" : ""}>${esc(name)}</option>`,
        ),
      )
      .join("");
    const shown = plantings.filter((p) => !this._area || p.area === this._area);
    const groups = STATUS_GROUPS.map(([status, title]) => {
      const rows = shown
        .filter((p) => p.status === status)
        .sort((a, b) => short(a.name).localeCompare(short(b.name)));
      if (!rows.length) return "";
      const body = rows
        .map((p) => {
          const when = p.planted_on
            ? `planted ${day(p.planted_on)}${p.planted_on.slice(0, 4) !== String(new Date().getFullYear()) ? ` ${p.planted_on.slice(0, 4)}` : ""}`
            : p.established
              ? "established"
              : status === "active"
                ? "not planted yet"
                : "";
          const actions =
            status === "proposed"
              ? `<div class="row-actions">
                  <button data-action="status" data-id="${esc(p.id)}" data-status="active">Grow it</button>
                  ${p.from_seed ? "" : `<button data-action="status" data-id="${esc(p.id)}" data-status="planned">Plan it</button>`}
                  <button class="quiet" data-action="status" data-id="${esc(p.id)}" data-status="removed">Turn down</button>
                </div>`
              : "";
          const detail = [
            this._area ? "" : esc(p.area),
            p.quantity > 1 ? `${p.quantity}×` : "",
            when,
            p.estimated_cost !== null && status !== "active" ? money(p.estimated_cost, garden.budget.currency) : "",
          ]
            .filter(Boolean)
            .join(" · ");
          return `<li class="planting">
            <div class="planting-main">
              <span class="name">${esc(short(p.name))}</span>
              <span class="meta">${detail}</span>
              ${p.notes && status !== "active" ? `<span class="notes-text">${esc(p.notes)}</span>` : ""}
            </div>
            ${actions}
          </li>`;
        })
        .join("");
      return `<ha-card><div class="card-body"><h3>${esc(title)} <span class="count">${rows.length}</span></h3><ul class="list">${body}</ul></div></ha-card>`;
    }).join("");
    return `<div class="toolbar"><label>Area <select id="area-filter">${options}</select></label></div>
      ${groups || `<p class="empty">No plantings recorded${this._area ? " here" : ""} yet. Use Add plantings on the integration page.</p>`}`;
  }

  _planView() {
    const { plan } = this._data;
    const cur = plan.currency;
    const years = plan.years
      .map((year) => {
        const budget = year.budget;
        let bar = "";
        if (budget) {
          const pct = (v) => Math.max(0, Math.min(100, (v / budget) * 100));
          bar = `<div class="bar" title="Spent ${money(year.spent, cur)}, planned ${money(year.planned, cur)} of ${money(budget, cur)}">
            <span class="spent" style="width:${pct(year.spent)}%"></span>
            <span class="planned" style="width:${pct(year.planned)}%"></span>
          </div>
          <div class="meta">${money(year.spent, cur)} spent · ${money(year.planned, cur)} planned · <b>${money(year.remaining, cur)}</b> left of ${money(budget, cur)}${year.unpriced ? ` · ${year.unpriced} unpriced` : ""}</div>`;
        }
        const items = year.items.length
          ? `<ul class="list">${year.items
              .map(
                (i) => `<li class="planting">
                  <div class="planting-main">
                    <span class="name">${esc(short(i.name))}${i.over_budget ? ` <span class="chip bad">over a year's budget</span>` : ""}</span>
                    <span class="meta">${[esc(i.area), TIERS[i.tier] || i.tier, i.priority !== "normal" ? `${i.priority} priority` : "", i.waits ? esc(i.waits) : ""].filter(Boolean).join(" · ")}</span>
                  </div>
                  <span class="price">${money(i.estimated_cost, cur)}</span>
                </li>`,
              )
              .join("")}</ul>`
          : `<p class="empty">Nothing planned for ${year.year}.</p>`;
        return `<ha-card><div class="card-body"><h3>${year.year}</h3>${bar}${items}</div></ha-card>`;
      })
      .join("");
    const undecided = plan.undecided_proposals.count
      ? `<p class="lead">${plural(plan.undecided_proposals.count, "proposal")} still to decide${plan.undecided_proposals.estimated_cost ? `, about ${money(plan.undecided_proposals.estimated_cost, cur)}` : ""}: see Plantings.</p>`
      : "";
    const budget = plan.annual_budget
      ? ""
      : `<p class="lead">No yearly budget is set, so everything falls in this year. Set one under Configure on the integration page.</p>`;
    return `${budget}${undecided}${years}`;
  }

  _tasksView() {
    const { tasks } = this._data;
    const order = { overdue: 0, due: 1, upcoming: 2 };
    const sorted = [...tasks].sort(
      (a, b) => order[a.state] - order[b.state] || a.end.localeCompare(b.end),
    );
    const section = (title, rows, canTick) =>
      rows.length
        ? `<ha-card><div class="card-body"><h3>${title} <span class="count">${rows.length}</span></h3><ul class="list">${rows
            .map(
              (t) => `<li class="task ${t.state}">
                ${canTick ? `<button class="tick" title="Done" aria-label="Mark ${esc(t.title)} done" data-action="complete" data-id="${esc(t.id)}"></button>` : ""}
                <div class="planting-main">
                  <span class="name">${esc(t.title)}</span>
                  <span class="meta">${esc(t.area || "")} · ${t.state === "upcoming" ? `from ${day(t.start)}` : `by ${day(t.end)}`}</span>
                  ${t.detail ? `<span class="notes-text">${esc(t.detail)}</span>` : ""}
                </div>
              </li>`,
            )
            .join("")}</ul></div></ha-card>`
        : "";
    const now = sorted.filter((t) => t.state !== "upcoming");
    const soon = sorted.filter((t) => t.state === "upcoming");
    return (
      section("To do now", now, true) +
        section("Coming up in the next 30 days", soon, false) ||
      `<p class="empty">Nothing to do in the next 30 days.</p>`
    );
  }

  // --- rendering -------------------------------------------------------------

  _render() {
    if (!this.shadowRoot || this._drag) return; // never redraw mid-drag
    const tabs = VIEWS.map(
      ([key, label]) =>
        `<button class="tab ${key === this._view ? "active" : ""}" data-action="view" data-view="${key}" role="tab" aria-selected="${key === this._view}">${label}</button>`,
    ).join("");
    let body;
    if (!this._data) {
      body = this._error
        ? `<p class="error">Could not read the garden: ${esc(this._error)}</p>`
        : `<p class="empty">Loading the garden…</p>`;
    } else {
      body =
        {
          map: () => this._mapView(),
          areas: () => this._areasView(),
          plantings: () => this._plantingsView(),
          plan: () => this._planView(),
          tasks: () => this._tasksView(),
        }[this._view]() +
        (this._error ? `<p class="error">${esc(this._error)}</p>` : "");
    }
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <div class="header">
        ${this._narrow ? `<button class="menu" data-action="menu" aria-label="Menu">☰</button>` : ""}
        <h1>Garden</h1>
      </div>
      <div class="tabs" role="tablist">${tabs}</div>
      <main class="${this._busy ? "busy" : ""}">${body}</main>`;
  }
}

const STYLE = `
:host {
  display: block;
  min-height: 100vh;
  background: var(--primary-background-color);
  color: var(--primary-text-color);
  font-family: var(--ha-font-family-body, Roboto, sans-serif);
}
.header {
  display: flex; align-items: center; gap: 8px;
  height: var(--header-height, 56px); padding: 0 16px;
  background: var(--app-header-background-color, var(--primary-color));
  color: var(--app-header-text-color, var(--text-primary-color, #fff));
}
.header h1 { font-size: 20px; font-weight: 400; margin: 0; }
.menu { background: none; border: 0; color: inherit; font-size: 22px; cursor: pointer; }
.tabs {
  display: flex; gap: 4px; padding: 0 12px; overflow-x: auto;
  background: var(--app-header-background-color, var(--primary-color));
}
.tab {
  background: none; border: 0; border-bottom: 2px solid transparent;
  color: var(--app-header-text-color, var(--text-primary-color, #fff));
  opacity: 0.75; padding: 12px 14px; font: inherit; cursor: pointer; white-space: nowrap;
}
.tab.active { opacity: 1; border-bottom-color: currentColor; }
main { max-width: 1100px; margin: 0 auto; padding: 16px; display: grid; gap: 16px; }
main.busy { opacity: 0.6; pointer-events: none; }
.grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
ha-card { display: block; }
.card-body { padding: 16px; }
.card-actions { border-top: 1px solid var(--divider-color); padding: 4px 8px; }
h2 { margin: 0 0 4px; font-size: 20px; font-weight: 500; }
h3 { margin: 0 0 8px; font-size: 16px; font-weight: 500; }
.count { color: var(--secondary-text-color); font-weight: 400; }
.meta { color: var(--secondary-text-color); font-size: 14px; }
.lead { margin: 0; color: var(--secondary-text-color); }
.character { font-style: italic; color: var(--secondary-text-color); margin: 8px 0; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.chip {
  font-size: 13px; padding: 2px 10px; border-radius: 12px;
  background: var(--secondary-background-color); color: var(--primary-text-color);
}
.chip.bad { background: var(--error-color); color: var(--text-primary-color, #fff); }
.chip.note { background: var(--warning-color); color: var(--text-primary-color, #fff); }
.notes { margin: 8px 0; padding-left: 18px; font-size: 14px; }
.notes .warn { color: var(--warning-color); }
.list { list-style: none; margin: 0; padding: 0; }
.planting, .task {
  display: flex; gap: 12px; align-items: center; justify-content: space-between;
  padding: 10px 0; border-top: 1px solid var(--divider-color);
}
.list > :first-child { border-top: 0; }
.planting-main { display: grid; gap: 2px; flex: 1; min-width: 0; }
.name { font-weight: 500; }
.notes-text { font-size: 13px; color: var(--secondary-text-color); }
.price { white-space: nowrap; color: var(--secondary-text-color); }
.row-actions { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
button {
  font: inherit; font-size: 14px; cursor: pointer; border-radius: 16px;
  border: 1px solid var(--primary-color); background: var(--primary-color);
  color: var(--text-primary-color, #fff); padding: 4px 12px;
}
button.quiet, button.link { background: none; color: var(--primary-color); }
button.link { border: 0; padding: 8px; }
.tick {
  width: 24px; height: 24px; padding: 0; flex: none; border-radius: 50%;
  background: none; border: 2px solid var(--secondary-text-color);
}
.tick:hover { border-color: var(--primary-color); background: var(--primary-color); }
.task.overdue .meta { color: var(--error-color); }
.bar {
  display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin: 4px 0 6px;
  background: var(--secondary-background-color);
}
.bar .spent { background: var(--primary-color); }
.bar .planned { background: var(--accent-color, var(--primary-color)); opacity: 0.5; }
.toolbar label { color: var(--secondary-text-color); }
select {
  font: inherit; padding: 6px 8px; border-radius: 6px; margin-left: 6px;
  background: var(--card-background-color); color: var(--primary-text-color);
  border: 1px solid var(--divider-color);
}
.map-toolbar { display: flex; gap: 12px; align-items: center; justify-content: space-between; }
.map {
  position: relative; width: 100%;
  background-color: var(--card-background-color);
  border: 1px solid var(--divider-color); border-radius: 12px;
  background-image:
    linear-gradient(var(--divider-color) 1px, transparent 1px),
    linear-gradient(90deg, var(--divider-color) 1px, transparent 1px);
  background-size: calc(100% / 24) calc(100% / var(--rows, 12));
  overflow: hidden; touch-action: none;
}
.map:not(.editing) { background-image: none; }
.links { position: absolute; inset: 0; width: 100%; height: 100%; }
.links line {
  stroke: var(--secondary-text-color); stroke-width: 2; stroke-dasharray: 4 4;
  opacity: 0.5; vector-effect: non-scaling-stroke;
}
.tile {
  position: absolute; box-sizing: border-box; padding: 6px 8px; overflow: hidden;
  display: flex; flex-direction: column; gap: 2px;
  border-radius: 8px; border: 2px solid var(--tile);
  background: color-mix(in srgb, var(--tile) 28%, var(--card-background-color));
  color: var(--primary-text-color); cursor: pointer; user-select: none;
}
.tile.unplaced { border-style: dashed; }
.map.editing .tile { cursor: move; }
.tile.dragging { box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3); z-index: 2; opacity: 0.9; }
.tile:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
.tile-name { font-weight: 500; font-size: 14px; line-height: 1.2; }
.tile-meta { font-size: 12px; color: var(--secondary-text-color); }
.badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: auto; }
.badge {
  font-size: 11px; padding: 0 6px; border-radius: 8px;
  background: var(--card-background-color); color: var(--primary-text-color);
}
.badge.bad { background: var(--error-color); color: var(--text-primary-color, #fff); }
.badge.note { background: var(--warning-color); color: var(--text-primary-color, #fff); }
.resize {
  position: absolute; right: 0; bottom: 0; width: 18px; height: 18px; cursor: nwse-resize;
  background: linear-gradient(135deg, transparent 50%, var(--tile) 50%);
}
.empty { color: var(--secondary-text-color); }
.error { color: var(--error-color); }
@media (max-width: 600px) {
  main { padding: 12px 16px; }
  .planting { flex-wrap: wrap; }
  .planting > .planting-main { flex-basis: 100%; }
  .task > .planting-main { flex-basis: calc(100% - 36px); }
  .row-actions { justify-content: flex-start; }
}
`;

customElements.define("master-gardener-panel", MasterGardenerPanel);
