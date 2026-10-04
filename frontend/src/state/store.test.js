import { describe, expect, it, vi } from "vitest";

import { Store } from "./store.js";

describe("Store", () => {
  it("calls subscribers immediately and on change, not on identical values", () => {
    const store = new Store(1);
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.set(1);
    store.set(2);
    store.update((value) => value + 1);
    unsubscribe();
    store.set(4);
    expect(listener.mock.calls.map(([value]) => value)).toEqual([1, 2, 3]);
  });

  it("lets a listener unsubscribe while being notified", () => {
    const store = new Store(0);
    const second = vi.fn();
    const stop = store.subscribe((value) => value === 1 && stop());
    store.subscribe(second);
    store.set(1);
    expect(second).toHaveBeenLastCalledWith(1);
  });
});
