import {describe, expect, it} from 'vitest';
import {Signal} from '../../src/wrapper.js';

describe('Prohibited contexts', () => {
  it('allows writes during computed', () => {
    const s = new Signal.State(1);
    const c = new Signal.Computed(() => (s.set(s.get() + 1), s.get()));
    expect(c.get()).toBe(2);
    expect(s.get()).toBe(2);

    // Note: c is marked clean in this case, even though re-evaluating it
    // would cause it to change value (due to the set inside of it).
    expect(c.get()).toBe(2);
    expect(s.get()).toBe(2);

    s.set(3);

    expect(c.get()).toBe(4);
    expect(s.get()).toBe(4);
  });
  it('disallows reads and writes during watcher notify', () => {
    const s = new Signal.State(1);
    const w = new Signal.subtle.Watcher(() => {
      s.get();
    });
    w.watch(s);
    expect(() => s.set(2)).toThrow();
    w.unwatch(s);
    expect(() => s.set(3)).not.toThrow();

    const w2 = new Signal.subtle.Watcher(() => {
      s.set(4);
    });
    w2.watch(s);
    expect(() => s.set(5)).toThrow();
    w2.unwatch(s);
    expect(() => s.set(3)).not.toThrow();
  });
});
