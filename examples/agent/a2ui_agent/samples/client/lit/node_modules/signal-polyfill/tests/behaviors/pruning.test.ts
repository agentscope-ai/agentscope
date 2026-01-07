import {describe, expect, it} from 'vitest';
import {Signal} from '../../src/wrapper.js';

describe('Pruning', () => {
  it('only recalculates until things are equal', () => {
    const s = new Signal.State(0);
    let n = 0;
    const c = new Signal.Computed(() => (n++, s.get()));
    let n2 = 0;
    const c2 = new Signal.Computed(() => (n2++, c.get(), 5));
    let n3 = 0;
    const c3 = new Signal.Computed(() => (n3++, c2.get()));

    expect(n).toBe(0);
    expect(n2).toBe(0);
    expect(n3).toBe(0);

    expect(c3.get()).toBe(5);
    expect(n).toBe(1);
    expect(n2).toBe(1);
    expect(n3).toBe(1);

    s.set(1);
    expect(n).toBe(1);
    expect(n2).toBe(1);
    expect(n3).toBe(1);

    expect(c3.get()).toBe(5);
    expect(n).toBe(2);
    expect(n2).toBe(2);
    expect(n3).toBe(1);
  });
  it('does similar pruning for live signals', () => {
    const s = new Signal.State(0);
    let n = 0;
    const c = new Signal.Computed(() => (n++, s.get()));
    let n2 = 0;
    const c2 = new Signal.Computed(() => (n2++, c.get(), 5));
    let n3 = 0;
    const c3 = new Signal.Computed(() => (n3++, c2.get()));
    const w = new Signal.subtle.Watcher(() => {});
    w.watch(c3);

    expect(n).toBe(0);
    expect(n2).toBe(0);
    expect(n3).toBe(0);

    expect(c3.get()).toBe(5);
    expect(n).toBe(1);
    expect(n2).toBe(1);
    expect(n3).toBe(1);

    s.set(1);
    expect(n).toBe(1);
    expect(n2).toBe(1);
    expect(n3).toBe(1);

    expect(w.getPending().length).toBe(1);

    expect(c3.get()).toBe(5);
    expect(n).toBe(2);
    expect(n2).toBe(2);
    expect(n3).toBe(1);

    expect(w.getPending().length).toBe(0);
  });
});
