import {describe, expect, it} from 'vitest';
import {Signal} from '../../../src/wrapper.js';

describe('currentComputed', () => {
  it('works', () => {
    expect(Signal.subtle.currentComputed()).toBe(undefined);
    let context;
    let c = new Signal.Computed(() => (context = Signal.subtle.currentComputed()));
    c.get();
    expect(c).toBe(context);
  });
});
