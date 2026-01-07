import {afterEach, describe, expect, it, vi} from 'vitest';
import {Signal} from '../../src/wrapper.js';

describe('Errors', () => {
  it('are cached by computed signals', () => {
    const s = new Signal.State('first');
    let n = 0;
    const c = new Signal.Computed(() => {
      n++;
      throw s.get();
    });
    let n2 = 0;
    const c2 = new Signal.Computed(() => {
      n2++;
      return c.get();
    });
    expect(n).toBe(0);
    expect(() => c.get()).toThrowError('first');
    expect(() => c2.get()).toThrowError('first');
    expect(n).toBe(1);
    expect(n2).toBe(1);
    expect(() => c.get()).toThrowError('first');
    expect(() => c2.get()).toThrowError('first');
    expect(n).toBe(1);
    expect(n2).toBe(1);
    s.set('second');
    expect(() => c.get()).toThrowError('second');
    expect(() => c2.get()).toThrowError('second');
    expect(n).toBe(2);
    expect(n2).toBe(2);

    // Doesn't retrigger on setting state to the same value
    s.set('second');
    expect(n).toBe(2);
  });
  it('are cached by computed signals when watched', () => {
    const s = new Signal.State('first');
    let n = 0;
    const c = new Signal.Computed<unknown>(() => {
      n++;
      throw s.get();
    });
    const w = new Signal.subtle.Watcher(() => {});
    w.watch(c);

    expect(n).toBe(0);
    expect(() => c.get()).toThrowError('first');
    expect(n).toBe(1);
    expect(() => c.get()).toThrowError('first');
    expect(n).toBe(1);
    s.set('second');
    expect(() => c.get()).toThrowError('second');
    expect(n).toBe(2);

    s.set('second');
    expect(n).toBe(2);
  });
  it('are cached by computed signals when equals throws', () => {
    const s = new Signal.State(0);
    const cSpy = vi.fn(() => s.get());
    const c = new Signal.Computed(cSpy, {
      equals() {
        throw new Error('equals');
      },
    });

    c.get();
    s.set(1);

    // Error is cached; c throws again without needing to rerun.
    expect(() => c.get()).toThrowError('equals');
    expect(cSpy).toBeCalledTimes(2);
    expect(() => c.get()).toThrowError('equals');
    expect(cSpy).toBeCalledTimes(2);
  });
});
