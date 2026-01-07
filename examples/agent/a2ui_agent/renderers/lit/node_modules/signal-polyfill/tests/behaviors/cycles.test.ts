import {describe, expect, it} from 'vitest';
import {Signal} from '../../src/wrapper.js';

describe('Cycles', () => {
  it('detects trivial cycles', () => {
    const c = new Signal.Computed(() => c.get());
    expect(() => c.get()).toThrow();
  });

  it('detects slightly larger cycles', () => {
    const c = new Signal.Computed(() => c2.get());
    const c2 = new Signal.Computed(() => c.get());
    const c3 = new Signal.Computed(() => c2.get());
    expect(() => c3.get()).toThrow();
  });
});
