import {describe, expect, it} from 'vitest';
import {Signal} from '../../src/wrapper.js';

describe('Computed', () => {
  it('should work', () => {
    const stateSignal = new Signal.State(1);

    const computedSignal = new Signal.Computed(() => {
      const f = stateSignal.get() * 2;
      return f;
    });

    expect(computedSignal.get()).toEqual(2);

    stateSignal.set(5);

    expect(stateSignal.get()).toEqual(5);
    expect(computedSignal.get()).toEqual(10);
  });

  describe('Comparison semantics', () => {
    it('should track Computed by Object.is', () => {
      const state = new Signal.State(1);
      let value = 5;
      let calls = 0;
      const computed = new Signal.Computed(() => (state.get(), value));
      const c2 = new Signal.Computed(() => (calls++, computed.get()));

      expect(calls).toBe(0);
      expect(c2.get()).toBe(5);
      expect(calls).toBe(1);
      state.set(2);
      expect(c2.get()).toBe(5);
      expect(calls).toBe(1);
      value = NaN;
      expect(c2.get()).toBe(5);
      expect(calls).toBe(1);
      state.set(3);
      expect(c2.get()).toBe(NaN);
      expect(calls).toBe(2);
      state.set(4);
      expect(c2.get()).toBe(NaN);
      expect(calls).toBe(2);
    });

    it('applies custom equality in Computed', () => {
      const s = new Signal.State(5);
      let ecalls = 0;
      const c1 = new Signal.Computed(() => (s.get(), 1), {
        equals() {
          ecalls++;
          return false;
        },
      });
      let calls = 0;
      const c2 = new Signal.Computed(() => {
        calls++;
        return c1.get();
      });

      expect(calls).toBe(0);
      expect(ecalls).toBe(0);

      expect(c2.get()).toBe(1);
      expect(ecalls).toBe(0);
      expect(calls).toBe(1);

      s.set(10);
      expect(c2.get()).toBe(1);
      expect(ecalls).toBe(1);
      expect(calls).toBe(2);
    });
  });
});
