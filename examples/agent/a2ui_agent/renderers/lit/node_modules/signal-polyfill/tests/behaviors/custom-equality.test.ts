import {describe, expect, it, vi} from 'vitest';
import {Signal} from '../../src/wrapper.js';

describe('Custom equality', () => {
  it('works for State', () => {
    let answer = true;
    const s = new Signal.State(1, {
      equals() {
        return answer;
      },
    });
    let n = 0;
    const c = new Signal.Computed(() => (n++, s.get()));

    expect(c.get()).toBe(1);
    expect(n).toBe(1);

    s.set(2);
    expect(s.get()).toBe(1);
    expect(c.get()).toBe(1);
    expect(n).toBe(1);

    answer = false;
    s.set(2);
    expect(s.get()).toBe(2);
    expect(c.get()).toBe(2);
    expect(n).toBe(2);

    s.set(2);
    expect(s.get()).toBe(2);
    expect(c.get()).toBe(2);
    expect(n).toBe(3);
  });
  it('works for Computed', () => {
    let answer = true;
    let value = 1;
    const u = new Signal.State(1);
    const s = new Signal.Computed(() => (u.get(), value), {
      equals() {
        return answer;
      },
    });
    let n = 0;
    const c = new Signal.Computed(() => (n++, s.get()));

    expect(c.get()).toBe(1);
    expect(n).toBe(1);

    u.set(2);
    value = 2;
    expect(s.get()).toBe(1);
    expect(c.get()).toBe(1);
    expect(n).toBe(1);

    answer = false;
    u.set(3);
    expect(s.get()).toBe(2);
    expect(c.get()).toBe(2);
    expect(n).toBe(2);

    u.set(4);
    expect(s.get()).toBe(2);
    expect(c.get()).toBe(2);
    expect(n).toBe(3);
  });
  it('does not leak tracking information', () => {
    const exact = new Signal.State(1);
    const epsilon = new Signal.State(0.1);
    const counter = new Signal.State(1);

    const cutoff = vi.fn((a, b) => Math.abs(a - b) < epsilon.get());
    const innerFn = vi.fn(() => exact.get());
    const inner = new Signal.Computed(innerFn, {
      equals: cutoff,
    });

    const outerFn = vi.fn(() => {
      counter.get();
      return inner.get();
    });
    const outer = new Signal.Computed(outerFn);

    outer.get();

    // Everything runs the first time.
    expect(innerFn).toBeCalledTimes(1);
    expect(outerFn).toBeCalledTimes(1);

    exact.set(2);
    counter.set(2);
    outer.get();

    // `outer` reruns because `counter` changed, `inner` reruns when called by
    // `outer`, and `cutoff` is called for the first time.
    expect(innerFn).toBeCalledTimes(2);
    expect(outerFn).toBeCalledTimes(2);
    expect(cutoff).toBeCalledTimes(1);

    epsilon.set(0.2);
    outer.get();

    // Changing something `cutoff` depends on makes `inner` need to rerun, but
    // (since the new and old values are equal) not `outer`.
    expect(innerFn).toBeCalledTimes(3);
    expect(outerFn).toBeCalledTimes(2);
    expect(cutoff).toBeCalledTimes(2);
  });
});
