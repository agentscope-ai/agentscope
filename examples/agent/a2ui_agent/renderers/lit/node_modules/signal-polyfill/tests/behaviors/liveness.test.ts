import {describe, expect, it, vi} from 'vitest';
import {Signal} from '../../src/wrapper.js';

describe('liveness', () => {
  it('only changes on first and last descendant', () => {
    const watchedSpy = vi.fn();
    const unwatchedSpy = vi.fn();
    const state = new Signal.State(1, {
      [Signal.subtle.watched]: watchedSpy,
      [Signal.subtle.unwatched]: unwatchedSpy,
    });
    const computed = new Signal.Computed(() => state.get());
    computed.get();
    expect(watchedSpy).not.toBeCalled();
    expect(unwatchedSpy).not.toBeCalled();

    const w = new Signal.subtle.Watcher(() => {});
    const w2 = new Signal.subtle.Watcher(() => {});

    w.watch(computed);
    expect(watchedSpy).toBeCalledTimes(1);
    expect(unwatchedSpy).not.toBeCalled();

    w2.watch(computed);
    expect(watchedSpy).toBeCalledTimes(1);
    expect(unwatchedSpy).not.toBeCalled();

    w2.unwatch(computed);
    expect(watchedSpy).toBeCalledTimes(1);
    expect(unwatchedSpy).not.toBeCalled();

    w.unwatch(computed);
    expect(watchedSpy).toBeCalledTimes(1);
    expect(unwatchedSpy).toBeCalledTimes(1);
  });

  it('is tracked well on computed signals', () => {
    const watchedSpy = vi.fn();
    const unwatchedSpy = vi.fn();
    const s = new Signal.State(1);
    const c = new Signal.Computed(() => s.get(), {
      [Signal.subtle.watched]: watchedSpy,
      [Signal.subtle.unwatched]: unwatchedSpy,
    });

    c.get();
    expect(watchedSpy).not.toBeCalled();
    expect(unwatchedSpy).not.toBeCalled();

    const w = new Signal.subtle.Watcher(() => {});
    w.watch(c);
    expect(watchedSpy).toBeCalledTimes(1);
    expect(unwatchedSpy).not.toBeCalled();

    w.unwatch(c);
    expect(watchedSpy).toBeCalledTimes(1);
    expect(unwatchedSpy).toBeCalledTimes(1);
  });
});
