import {describe, expect, it} from 'vitest';
import {Signal} from '../../src/wrapper.js';

describe('Guards', () => {
  it('should work with Signals', () => {
    const state = new Signal.State(1);
    const computed = new Signal.Computed(() => state.get() * 2);
    expect(Signal.isState(state)).toBe(true);
    expect(Signal.isComputed(state)).toBe(false);

    expect(Signal.isState(computed)).toBe(false);
    expect(Signal.isComputed(computed)).toBe(true);
  });

  it("shouldn't error with values", () => {
    expect(Signal.isState(1)).toBe(false);
    expect(Signal.isComputed(2)).toBe(false);

    expect(Signal.isState({})).toBe(false);
    expect(Signal.isComputed({})).toBe(false);
  });
});
