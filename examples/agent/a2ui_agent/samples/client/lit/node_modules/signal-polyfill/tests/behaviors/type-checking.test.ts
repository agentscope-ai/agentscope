import {describe, expect, it} from 'vitest';
import {Signal} from '../../src/wrapper.js';

describe('Expected class shape', () => {
  it('should be on the prototype', () => {
    expect(typeof Signal.State.prototype.get).toBe('function');
    expect(typeof Signal.State.prototype.set).toBe('function');
    expect(typeof Signal.Computed.prototype.get).toBe('function');
    expect(typeof Signal.subtle.Watcher.prototype.watch).toBe('function');
    expect(typeof Signal.subtle.Watcher.prototype.unwatch).toBe('function');
    expect(typeof Signal.subtle.Watcher.prototype.getPending).toBe('function');
  });
});

describe('type checks', () => {
  it('checks types in methods', () => {
    let x = {};
    let s = new Signal.State(1);
    let c = new Signal.Computed(() => {});
    let w = new Signal.subtle.Watcher(() => {});

    expect(() => Signal.State.prototype.get.call(x)).toThrowError(TypeError);
    expect(Signal.State.prototype.get.call(s)).toBe(1);
    expect(() => Signal.State.prototype.get.call(c)).toThrowError(TypeError);
    expect(() => Signal.State.prototype.get.call(w)).toThrowError(TypeError);

    expect(() => Signal.State.prototype.set.call(x, 2)).toThrowError(TypeError);
    expect(Signal.State.prototype.set.call(s, 2)).toBe(undefined);
    expect(() => Signal.State.prototype.set.call(c, 2)).toThrowError(TypeError);
    expect(() => Signal.State.prototype.set.call(w, 2)).toThrowError(TypeError);

    expect(() => Signal.Computed.prototype.get.call(x)).toThrowError(TypeError);
    expect(() => Signal.Computed.prototype.get.call(s)).toThrowError(TypeError);
    expect(Signal.Computed.prototype.get.call(c)).toBe(undefined);
    expect(() => Signal.Computed.prototype.get.call(w)).toThrowError(TypeError);

    expect(() => Signal.subtle.Watcher.prototype.watch.call(x, s)).toThrowError(TypeError);
    expect(() => Signal.subtle.Watcher.prototype.watch.call(s, s)).toThrowError(TypeError);
    expect(() => Signal.subtle.Watcher.prototype.watch.call(c, s)).toThrowError(TypeError);
    expect(Signal.subtle.Watcher.prototype.watch.call(w, s)).toBe(undefined);
    expect(() => Signal.subtle.Watcher.prototype.watch.call(w, w)).toThrowError(TypeError);

    expect(() => Signal.subtle.Watcher.prototype.unwatch.call(x, s)).toThrowError(TypeError);
    expect(() => Signal.subtle.Watcher.prototype.unwatch.call(s, s)).toThrowError(TypeError);
    expect(() => Signal.subtle.Watcher.prototype.unwatch.call(c, s)).toThrowError(TypeError);
    expect(Signal.subtle.Watcher.prototype.unwatch.call(w, s)).toBe(undefined);
    expect(() => Signal.subtle.Watcher.prototype.unwatch.call(w, w)).toThrowError(TypeError);

    expect(() => Signal.subtle.Watcher.prototype.getPending.call(x, s)).toThrowError(TypeError);
    expect(() => Signal.subtle.Watcher.prototype.getPending.call(s, s)).toThrowError(TypeError);
    expect(() => Signal.subtle.Watcher.prototype.getPending.call(c, s)).toThrowError(TypeError);
    expect(Signal.subtle.Watcher.prototype.getPending.call(w, s)).toStrictEqual([]);

    // @ts-expect-error
    expect(() => Signal.subtle.introspectSources(x)).toThrowError(TypeError);
    // @ts-expect-error
    expect(() => Signal.subtle.introspectSources(s)).toThrowError(TypeError);
    expect(Signal.subtle.introspectSources(c)).toStrictEqual([]);
    expect(Signal.subtle.introspectSources(w)).toStrictEqual([]);

    // @ts-expect-error
    expect(() => Signal.subtle.hasSinks(x)).toThrowError(TypeError);
    expect(Signal.subtle.hasSinks(s)).toBe(false);
    expect(Signal.subtle.hasSinks(c)).toBe(false);
    // @ts-expect-error
    expect(() => Signal.subtle.hasSinks(w)).toThrowError(TypeError);

    // @ts-expect-error
    expect(() => Signal.subtle.introspectSinks(x)).toThrowError(TypeError);
    expect(Signal.subtle.introspectSinks(s)).toStrictEqual([]);
    expect(Signal.subtle.introspectSinks(c)).toStrictEqual([]);
    // @ts-expect-error
    expect(() => Signal.subtle.introspectSinks(w)).toThrowError(TypeError);
  });
});
