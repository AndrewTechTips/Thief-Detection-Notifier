// Minimal observable value: views subscribe, services set.

/** @template T */
export class Store {
  /** @param {T} initial */
  constructor(initial) {
    this.value = initial;
    /** @type {Set<(value: T) => void>} */
    this.listeners = new Set();
  }

  get() {
    return this.value;
  }

  /** @param {T} next */
  set(next) {
    if (Object.is(next, this.value)) return;
    this.value = next;
    for (const listener of [...this.listeners]) listener(next);
  }

  /** @param {(value: T) => T} change */
  update(change) {
    this.set(change(this.value));
  }

  /**
   * Calls `listener` now and on every change. Returns the unsubscribe function.
   * @param {(value: T) => void} listener
   */
  subscribe(listener) {
    this.listeners.add(listener);
    listener(this.value);
    return () => {
      this.listeners.delete(listener);
    };
  }
}
