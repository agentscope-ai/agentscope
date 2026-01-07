import {describe, expect, it} from 'vitest';
import {Signal} from '../../src/wrapper.js';

describe('Dynamic dependencies', () => {
  function run(live) {
    const states = Array.from('abcdefgh').map((s) => new Signal.State(s));
    const sources = new Signal.State(states);
    const computed = new Signal.Computed(() => {
      let str = '';
      for (const state of sources.get()) str += state.get();
      return str;
    });
    if (live) {
      const w = new Signal.subtle.Watcher(() => {});
      w.watch(computed);
    }
    expect(computed.get()).toBe('abcdefgh');
    expect(Signal.subtle.introspectSources(computed).slice(1)).toStrictEqual(states);

    sources.set(states.slice(0, 5));
    expect(computed.get()).toBe('abcde');
    expect(Signal.subtle.introspectSources(computed).slice(1)).toStrictEqual(states.slice(0, 5));

    sources.set(states.slice(3));
    expect(computed.get()).toBe('defgh');
    expect(Signal.subtle.introspectSources(computed).slice(1)).toStrictEqual(states.slice(3));
  }
  it('works live', () => run(true));
  it('works not live', () => run(false));
});
