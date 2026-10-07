// Watch areas: polygons over the live picture, in fractions of the frame (0..1), so they survive
// resolution changes. Outside the areas is dimmed; nothing there triggers motion. In edit mode:
// tap to add corners, tap the first corner to close the shape, drag corners to adjust, tap a
// shape to select it. Presets cover the common cases without drawing (and without a pointer).

import { h, svg } from "./dom.js";

/** @typedef {[number, number]} Point */
/** @typedef {Point[]} Polygon */

const MAX_AREAS = 16;
const MAX_CORNERS = 64;
const SVG_NS = "http://www.w3.org/2000/svg";

/** Ready-made areas. */
export const PRESETS = {
  whole: { label: "Whole frame", areas: /** @type {Polygon[]} */ ([]) },
  lower: {
    label: "Lower half",
    areas: /** @type {Polygon[]} */ ([
      [
        [0, 0.5],
        [1, 0.5],
        [1, 1],
        [0, 1],
      ],
    ]),
  },
  upper: {
    label: "Upper half",
    areas: /** @type {Polygon[]} */ ([
      [
        [0, 0],
        [1, 0],
        [1, 0.5],
        [0, 0.5],
      ],
    ]),
  },
};

/**
 * @param {{
 *   surface: HTMLElement,
 *   areas: Polygon[],
 *   onChange: (areas: Polygon[]) => void,
 * }} options
 * surface: the positioned element the areas cover (the live picture).
 */
export function roiEditor({ surface, areas: initial, onChange }) {
  /** @type {Polygon[]} */
  let areas = clone(initial);
  /** @type {Point[]} the shape being drawn */
  let draft = [];
  /** @type {number | null} */
  let selected = null;
  let editing = false;
  /** @type {{ area: number, corner: number, pointer: number } | null} */
  let dragging = null;

  const layer = svg(
    `<svg class="roi-layer" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true"></svg>`,
  );
  const handles = h("div", { class: "roi-handles", attrs: { "aria-hidden": "true" } });
  surface.append(layer, handles);

  const hint = h("p", { class: "hint" });
  const finish = h("button", {
    class: "btn btn-secondary btn-sm",
    attrs: { type: "button" },
    text: "Finish shape",
    on: { click: closeDraft },
  });
  const remove = h("button", {
    class: "btn btn-danger btn-sm",
    attrs: { type: "button" },
    text: "Delete area",
    on: { click: deleteSelected },
  });
  const presets = Object.values(PRESETS).map(({ label, areas: preset }) =>
    h("button", {
      class: "btn btn-ghost btn-sm",
      attrs: { type: "button" },
      text: label,
      on: {
        click: () => {
          draft = [];
          selected = null;
          commit(clone(preset));
        },
      },
    }),
  );
  const toolbar = h(
    "div",
    { class: "roi-toolbar", attrs: { hidden: true } },
    hint,
    h("div", { class: "flex flex-wrap gap-2" }, ...presets, finish, remove),
  );

  /** @param {Polygon[]} next */
  function commit(next) {
    areas = next;
    render();
    onChange(clone(areas));
  }

  function closeDraft() {
    if (draft.length < 3 || areas.length >= MAX_AREAS) return;
    const shape = draft;
    draft = [];
    selected = areas.length;
    commit([...areas, shape]);
  }

  function deleteSelected() {
    if (selected === null) return;
    const index = selected;
    selected = null;
    commit(areas.filter((_, i) => i !== index));
  }

  /** @param {PointerEvent} event @returns {Point} */
  function toPoint(event) {
    const rect = surface.getBoundingClientRect();
    const x = clamp((event.clientX - rect.left) / rect.width);
    const y = clamp((event.clientY - rect.top) / rect.height);
    return [round(x), round(y)];
  }

  function render() {
    const parts = [];
    if (areas.length) {
      // Everything outside the areas is dimmed: an outer rectangle with the areas as holes.
      const holes = areas.map((area) => pathOf(area)).join(" ");
      parts.push(`<path class="roi-outside" fill-rule="evenodd" d="M0 0H1V1H0Z ${holes}"></path>`);
    }
    areas.forEach((area, i) => {
      parts.push(
        `<path class="roi-area${i === selected ? " is-selected" : ""}" data-area="${i}" d="${pathOf(area)}" vector-effect="non-scaling-stroke"></path>`,
      );
    });
    if (draft.length) {
      parts.push(
        `<polyline class="roi-draft" points="${draft.map(([x, y]) => `${num(x)},${num(y)}`).join(" ")}" vector-effect="non-scaling-stroke"></polyline>`,
      );
    }
    const fragment = svg(`<svg xmlns="${SVG_NS}">${parts.join("")}</svg>`);
    layer.replaceChildren(...fragment.childNodes);

    handles.replaceChildren();
    if (editing) {
      areas.forEach((area, a) =>
        area.forEach(([x, y], c) => handles.append(handle(x, y, { area: a, corner: c }))),
      );
      draft.forEach(([x, y], c) =>
        handles.append(handle(x, y, { draft: true, first: c === 0 && draft.length >= 3 })),
      );
    }

    surface.toggleAttribute("data-editing", editing);
    finish.disabled = draft.length < 3;
    remove.disabled = selected === null;
    hint.textContent = draft.length
      ? draft.length < 3
        ? "Keep adding corners. A shape needs at least three."
        : "Tap the first corner, or press Finish shape, to close it."
      : areas.length >= MAX_AREAS
        ? `That's the most areas a camera can have (${MAX_AREAS}).`
        : "Tap the picture to add corners. Drag corners to adjust. Tap a shape to select it.";
  }

  /**
   * @param {number} x
   * @param {number} y
   * @param {{ area?: number, corner?: number, draft?: boolean, first?: boolean }} role
   */
  function handle(x, y, role) {
    const element = h("span", {
      class: `roi-handle${role.draft ? " is-draft" : ""}${role.first ? " is-first" : ""}`,
    });
    // Through the CSSOM, not a style attribute: the page CSP forbids inline styles.
    element.style.left = `${x * 100}%`;
    element.style.top = `${y * 100}%`;
    element.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      event.preventDefault();
      if (role.first) {
        closeDraft();
        return;
      }
      if (role.area !== undefined && role.corner !== undefined) {
        dragging = { area: role.area, corner: role.corner, pointer: event.pointerId };
        selected = role.area;
        surface.setPointerCapture(event.pointerId);
        render();
      }
    });
    return element;
  }

  surface.addEventListener("pointerdown", (event) => {
    if (!editing || event.button !== 0) return;
    const target = /** @type {Element} */ (event.target);
    const area = target.closest?.("[data-area]");
    if (area && !draft.length) {
      selected = Number(area.getAttribute("data-area"));
      render();
      return;
    }
    if (areas.length >= MAX_AREAS || draft.length >= MAX_CORNERS) return;
    selected = null;
    draft = [...draft, toPoint(event)];
    render();
  });
  surface.addEventListener("pointermove", (event) => {
    if (!dragging || event.pointerId !== dragging.pointer) return;
    const { area, corner } = dragging;
    areas = areas.map((shape, a) =>
      a === area ? shape.map((point, c) => (c === corner ? toPoint(event) : point)) : shape,
    );
    render();
  });
  const endDrag = (/** @type {PointerEvent} */ event) => {
    if (!dragging || event.pointerId !== dragging.pointer) return;
    dragging = null;
    commit(areas);
  };
  surface.addEventListener("pointerup", endDrag);
  surface.addEventListener("pointercancel", endDrag);

  /** @param {KeyboardEvent} event */
  function onKey(event) {
    if (!editing) return;
    if (event.key === "Escape" && draft.length) {
      draft = [];
      render();
    } else if (event.key === "Enter" && draft.length >= 3) {
      closeDraft();
    } else if ((event.key === "Delete" || event.key === "Backspace") && selected !== null) {
      deleteSelected();
    }
  }
  document.addEventListener("keydown", onKey);

  render();

  return {
    toolbar,
    get editing() {
      return editing;
    },
    /** @param {boolean} on */
    setEditing(on) {
      editing = on;
      draft = [];
      selected = null;
      toolbar.hidden = !on;
      render();
    },
    /** Replaces the areas without reporting a change (e.g. after saving or resetting).
     * @param {Polygon[]} next */
    set(next) {
      areas = clone(next);
      draft = [];
      selected = null;
      render();
    },
    destroy() {
      document.removeEventListener("keydown", onKey);
      layer.remove();
      handles.remove();
    },
  };
}

/** @param {Polygon} area */
function pathOf(area) {
  return `M${area.map(([x, y]) => `${num(x)} ${num(y)}`).join("L")}Z`;
}

/** Coordinates go into SVG markup: only ever as numbers, whatever the data says.
 * @param {unknown} value */
function num(value) {
  return String(Number(value));
}

/** @param {Polygon[]} areas @returns {Polygon[]} */
function clone(areas) {
  return areas.map((area) => area.map(([x, y]) => /** @type {Point} */ ([x, y])));
}

/** @param {number} value */
function clamp(value) {
  return Math.min(1, Math.max(0, value));
}

/** @param {number} value */
function round(value) {
  return Math.round(value * 10_000) / 10_000;
}

/**
 * How the areas read in words.
 * @param {Polygon[]} areas
 */
export function describeAreas(areas) {
  if (areas.length === 0) return "Watching the whole frame.";
  return `Watching ${areas.length === 1 ? "one area" : `${areas.length} areas`}; the rest of the frame is ignored.`;
}
