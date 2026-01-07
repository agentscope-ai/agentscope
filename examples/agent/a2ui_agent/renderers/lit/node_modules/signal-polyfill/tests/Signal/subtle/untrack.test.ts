import {describe, expect, it} from 'vitest';
import {Signal} from '../../../src/wrapper.js';

describe('Untrack', () => {
  it('works', () => {
    const state = new Signal.State(1);
    const computed = new Signal.Computed(() => Signal.subtle.untrack(() => state.get()));
    expect(computed.get()).toBe(1);
    state.set(2);
    expect(computed.get()).toBe(1);
  });

  it('works differently without untrack', () => {
    const state = new Signal.State(1);
    const computed = new Signal.Computed(() => state.get());
    expect(computed.get()).toBe(1);
    state.set(2);
    expect(computed.get()).toBe(2);
  });
});
