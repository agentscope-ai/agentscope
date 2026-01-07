import {describe, expect, it, vi} from 'vitest';
import {Signal} from '../../../src/wrapper.js';

describe('watch and unwatch', () => {
  it('handles multiple watchers well', () => {
    const s = new Signal.State(1);
    const s2 = new Signal.State(2);
    let n = 0;
    const w = new Signal.subtle.Watcher(() => n++);
    w.watch(s, s2);

    s.set(4);
    expect(n).toBe(1);
    expect(w.getPending()).toStrictEqual([]);

    w.watch();
    s2.set(8);
    expect(n).toBe(2);

    w.unwatch(s);
    s.set(3);
    expect(n).toBe(2);

    w.watch();
    s2.set(3);
    expect(n).toBe(3);

    w.watch();
    s.set(2);
    expect(n).toBe(3);
  });
  it('understands dynamic dependency sets', () => {
    let w1 = 0,
      u1 = 0,
      w2 = 0,
      u2 = 0,
      n = 0,
      d = 0;
    let s1 = new Signal.State(1, {
      [Signal.subtle.watched]() {
        w1++;
      },
      [Signal.subtle.unwatched]() {
        u1++;
      },
    });
    let s2 = new Signal.State(2, {
      [Signal.subtle.watched]() {
        w2++;
      },
      [Signal.subtle.unwatched]() {
        u2++;
      },
    });
    let which: {get(): number} = s1;
    let c = new Signal.Computed(() => (d++, which.get()));
    let w = new Signal.subtle.Watcher(() => n++);

    w.watch(c);
    expect(w1 + w2 + u1 + u2 + n + d).toBe(0);
    expect(Signal.subtle.hasSinks(s1)).toBe(false);
    expect(Signal.subtle.hasSinks(s2)).toBe(false);
    expect(w.getPending()).toStrictEqual([c]);

    expect(c.get()).toBe(1);
    expect(w1).toBe(1);
    expect(u1).toBe(0);
    expect(w2).toBe(0);
    expect(u2).toBe(0);
    expect(n).toBe(0);
    expect(Signal.subtle.hasSinks(s1)).toBe(true);
    expect(Signal.subtle.hasSinks(s2)).toBe(false);
    expect(w.getPending()).toStrictEqual([]);
    expect(d).toBe(1);

    s1.set(3);
    expect(w1).toBe(1);
    expect(u1).toBe(0);
    expect(w2).toBe(0);
    expect(u2).toBe(0);
    expect(n).toBe(1);
    expect(Signal.subtle.hasSinks(s1)).toBe(true);
    expect(Signal.subtle.hasSinks(s2)).toBe(false);
    expect(w.getPending()).toStrictEqual([c]);
    expect(d).toBe(1);

    expect(c.get()).toBe(3);
    expect(w1).toBe(1);
    expect(u1).toBe(0);
    expect(w2).toBe(0);
    expect(u2).toBe(0);
    expect(n).toBe(1);
    expect(Signal.subtle.hasSinks(s1)).toBe(true);
    expect(Signal.subtle.hasSinks(s2)).toBe(false);
    expect(w.getPending()).toStrictEqual([]);
    expect(d).toBe(2);

    which = s2;
    w.watch();
    s1.set(4);
    expect(w1).toBe(1);
    expect(u1).toBe(0);
    expect(w2).toBe(0);
    expect(u2).toBe(0);
    expect(n).toBe(2);
    expect(Signal.subtle.hasSinks(s1)).toBe(true);
    expect(Signal.subtle.hasSinks(s2)).toBe(false);
    expect(w.getPending()).toStrictEqual([c]);
    expect(d).toBe(2);

    expect(c.get()).toBe(2);
    expect(w1).toBe(1);
    expect(u1).toBe(1);
    expect(w2).toBe(1);
    expect(u2).toBe(0);
    expect(n).toBe(2);
    expect(Signal.subtle.hasSinks(s1)).toBe(false);
    expect(Signal.subtle.hasSinks(s2)).toBe(true);
    expect(w.getPending()).toStrictEqual([]);
    expect(d).toBe(3);

    w.watch();
    which = {
      get() {
        return 10;
      },
    };
    s1.set(5);
    expect(c.get()).toBe(2);
    expect(w1).toBe(1);
    expect(u1).toBe(1);
    expect(w2).toBe(1);
    expect(u2).toBe(0);
    expect(n).toBe(2);
    expect(Signal.subtle.hasSinks(s1)).toBe(false);
    expect(Signal.subtle.hasSinks(s2)).toBe(true);
    expect(w.getPending()).toStrictEqual([]);
    expect(d).toBe(3);

    w.watch();
    s2.set(0);
    expect(w1).toBe(1);
    expect(u1).toBe(1);
    expect(w2).toBe(1);
    expect(u2).toBe(0);
    expect(n).toBe(3);
    expect(Signal.subtle.hasSinks(s1)).toBe(false);
    expect(Signal.subtle.hasSinks(s2)).toBe(true);
    expect(w.getPending()).toStrictEqual([c]);
    expect(d).toBe(3);

    expect(c.get()).toBe(10);
    expect(w1).toBe(1);
    expect(u1).toBe(1);
    expect(w2).toBe(1);
    expect(u2).toBe(1);
    expect(n).toBe(3);
    expect(Signal.subtle.hasSinks(s1)).toBe(false);
    expect(Signal.subtle.hasSinks(s2)).toBe(false);
    expect(w.getPending()).toStrictEqual([]);
    expect(d).toBe(4);
  });
  it('can unwatch multiple signals', async () => {
    const signals = [...Array(7)].map((_, i) => new Signal.State(i));
    const notify = vi.fn();
    const watcher = new Signal.subtle.Watcher(notify);
    const expectSources = (expected: typeof signals) => {
      const sources = Signal.subtle.introspectSources(watcher) as typeof signals;
      sources.sort((a, b) => signals.indexOf(a) - signals.indexOf(b));
      expected.sort((a, b) => signals.indexOf(a) - signals.indexOf(b));
      return expect(sources).toEqual(expected);
    };

    watcher.watch(...signals);
    expectSources(signals);

    const unwatched = [0, 3, 4, 6].map((i) => signals[i]);
    const watched = signals.filter((s) => !unwatched.includes(s));

    watcher.unwatch(...unwatched);
    expectSources(watched);

    let expectedNotifyCalls = 0;
    for (const signal of signals) {
      signal.set(signal.get() + 1);
      if (watched.includes(signal)) ++expectedNotifyCalls;

      expect(notify).toHaveBeenCalledTimes(expectedNotifyCalls);

      watcher.watch();
    }
  });
});
