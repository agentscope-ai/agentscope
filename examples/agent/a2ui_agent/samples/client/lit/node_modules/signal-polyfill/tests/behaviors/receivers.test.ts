import {describe, expect, it} from 'vitest';
import {Signal} from '../../src/wrapper.js';

describe('Receivers', () => {
  it('is this for computed', () => {
    let receiver;
    const c = new Signal.Computed(function () {
      receiver = this;
    });
    expect(c.get()).toBe(undefined);
    expect(receiver).toBe(c);
  });
  it('is this for watched/unwatched', () => {
    let r1, r2;
    const s = new Signal.State(1, {
      [Signal.subtle.watched]() {
        r1 = this;
      },
      [Signal.subtle.unwatched]() {
        r2 = this;
      },
    });
    expect(r1).toBe(undefined);
    expect(r2).toBe(undefined);
    const w = new Signal.subtle.Watcher(() => {});
    w.watch(s);
    expect(r1).toBe(s);
    expect(r2).toBe(undefined);
    w.unwatch(s);
    expect(r2).toBe(s);
  });
  it('is this for equals', () => {
    let receiver;
    const options = {
      equals() {
        receiver = this;
        return false;
      },
    };
    const s = new Signal.State(1, options);
    s.set(2);
    expect(receiver).toBe(s);

    const c = new Signal.Computed(() => s.get(), options);
    expect(c.get()).toBe(2);
    s.set(4);
    expect(c.get()).toBe(4);
    expect(receiver).toBe(c);
  });
});
