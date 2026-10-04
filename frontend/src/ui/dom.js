// Tiny DOM builder. Text always goes in as text nodes, so data from the API can never inject
// markup; the only HTML strings are the static icons in ui/icons.js.

/**
 * @typedef {Node | string | number | false | null | undefined} Child
 * @typedef {{
 *   class?: string,
 *   data?: Record<string, string>,
 *   attrs?: Record<string, string | boolean | undefined>,
 *   on?: { [K in keyof HTMLElementEventMap]?: (event: HTMLElementEventMap[K]) => void },
 *   text?: string,
 * }} Props
 */

/**
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag
 * @param {Props} [props]
 * @param {...(Child | Child[])} children
 * @returns {HTMLElementTagNameMap[K]}
 */
export function h(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  if (props.class) element.className = props.class;
  if (props.data) Object.assign(element.dataset, props.data);
  for (const [name, value] of Object.entries(props.attrs ?? {})) {
    if (value === true) element.setAttribute(name, "");
    else if (value !== false && value !== undefined) element.setAttribute(name, value);
  }
  for (const [type, listener] of Object.entries(props.on ?? {})) {
    element.addEventListener(type, /** @type {EventListener} */ (listener));
  }
  if (props.text !== undefined) element.textContent = props.text;
  append(element, children);
  return element;
}

/**
 * @param {Element} parent
 * @param {(Child | Child[])[]} children
 */
export function append(parent, children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(typeof child === "number" ? String(child) : child);
  }
}

/** Parses a trusted, static SVG string (icons only).
 * @param {string} markup
 * @returns {SVGSVGElement} */
export function svg(markup) {
  const template = document.createElement("template");
  template.innerHTML = markup.trim();
  return /** @type {SVGSVGElement} */ (template.content.firstElementChild);
}

/**
 * Required element lookup: a missing element is a bug in the markup, so fail loudly.
 * @template {Element} [T=HTMLElement]
 * @param {ParentNode} root
 * @param {string} selector
 * @returns {T}
 */
export function $(root, selector) {
  const element = root.querySelector(selector);
  if (!element) throw new Error(`Missing element: ${selector}`);
  return /** @type {T} */ (element);
}
