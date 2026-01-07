import {afterEach, describe, expect, it, vi} from 'vitest';
import {Signal} from '../../../src/wrapper.js';

describe('Watcher', () => {
  type Destructor = () => void;
  const notifySpy = vi.fn();

  const watcher = new Signal.subtle.Watcher(() => {
    notifySpy();
  });

  function effect(cb: () => Destructor | void): () => void {
    let destructor: Destructor | void;
    const c = new Signal.Computed(() => (destructor = cb()));
    watcher.watch(c);
    c.get();
    return () => {
      destructor?.();
      watcher.unwatch(c);
    };
  }

  function flushPending() {
    for (const signal of watcher.getPending()) {
      signal.get();
    }
    expect(watcher.getPending()).toStrictEqual([]);
  }

  afterEach(() => watcher.unwatch(...Signal.subtle.introspectSources(watcher)));

  it('should work', () => {
    const watchedSpy = vi.fn();
    const unwatchedSpy = vi.fn();
    const stateSignal = new Signal.State(1, {
      [Signal.subtle.watched]: watchedSpy,
      [Signal.subtle.unwatched]: unwatchedSpy,
    });

    stateSignal.set(100);
    stateSignal.set(5);

    const computedSignal = new Signal.Computed(() => stateSignal.get() * 2);

    let calls = 0;
    let output = 0;
    let computedOutput = 0;

    // Ensure the call backs are not called yet
    expect(watchedSpy).not.toHaveBeenCalled();
    expect(unwatchedSpy).not.toHaveBeenCalled();

    // Expect the watcher to not have any sources as nothing has been connected yet
    expect(Signal.subtle.introspectSources(watcher)).toHaveLength(0);
    expect(Signal.subtle.introspectSinks(computedSignal)).toHaveLength(0);
    expect(Signal.subtle.introspectSinks(stateSignal)).toHaveLength(0);

    expect(Signal.subtle.hasSinks(stateSignal)).toEqual(false);

    const destructor = effect(() => {
      output = stateSignal.get();
      computedOutput = computedSignal.get();
      calls++;
      return () => {};
    });

    // The signal is now watched
    expect(Signal.subtle.hasSinks(stateSignal)).toEqual(true);

    // Now that the effect is created, there will be a source
    expect(Signal.subtle.introspectSources(watcher)).toHaveLength(1);
    expect(Signal.subtle.introspectSinks(computedSignal)).toHaveLength(1);

    // Note: stateSignal has more sinks because one is for the computed signal and one is the effect.
    expect(Signal.subtle.introspectSinks(stateSignal)).toHaveLength(2);

    // Now the watched callback should be called
    expect(watchedSpy).toHaveBeenCalled();
    expect(unwatchedSpy).not.toHaveBeenCalled();

    // It should not have notified yet
    expect(notifySpy).not.toHaveBeenCalled();

    stateSignal.set(10);

    // After a signal has been set, it should notify
    expect(notifySpy).toHaveBeenCalled();

    // Initially, the effect should not have run
    expect(calls).toEqual(1);
    expect(output).toEqual(5);
    expect(computedOutput).toEqual(10);

    flushPending();

    // The effect should run, and thus increment the value
    expect(calls).toEqual(2);
    expect(output).toEqual(10);
    expect(computedOutput).toEqual(20);

    // Kicking it off again, the effect should run again
    watcher.watch();
    stateSignal.set(20);
    expect(watcher.getPending()).toHaveLength(1);
    flushPending();

    // After a signal has been set, it should notify again
    expect(notifySpy).toHaveBeenCalledTimes(2);

    expect(calls).toEqual(3);
    expect(output).toEqual(20);
    expect(computedOutput).toEqual(40);

    Signal.subtle.untrack(() => {
      // Untrack doesn't affect set, only get
      stateSignal.set(999);
      expect(calls).toEqual(3);
      flushPending();
      expect(calls).toEqual(4);
    });

    // Destroy and un-subscribe
    destructor();

    // Since now it is un-subscribed, it should now be called
    expect(unwatchedSpy).toHaveBeenCalled();
    // We can confirm that it is un-watched by checking it
    expect(Signal.subtle.hasSinks(stateSignal)).toEqual(false);

    // Since now it is un-subscribed, this should have no effect now
    stateSignal.set(200);
    flushPending();

    // Make sure that effect is no longer running
    // Everything should stay the same
    expect(calls).toEqual(4);
    expect(output).toEqual(999);
    expect(computedOutput).toEqual(1998);

    expect(watcher.getPending()).toHaveLength(0);

    // Adding any other effect after an unwatch should work as expected
    const destructor2 = effect(() => {
      output = stateSignal.get();
      return () => {};
    });

    stateSignal.set(300);
    flushPending();
  });

  it('provides `this` to notify as normal function', () => {
    const mockGetPending = vi.fn();

    const watcher = new Signal.subtle.Watcher(function () {
      this.getPending();
    });
    watcher.getPending = mockGetPending;

    const signal = new Signal.State<number>(0);
    watcher.watch(signal);

    signal.set(1);
    expect(mockGetPending).toBeCalled();
  });

  it('can be closed in if needed in notify as an arrow function', () => {
    const mockGetPending = vi.fn();

    const watcher = new Signal.subtle.Watcher(() => {
      watcher.getPending();
    });
    watcher.getPending = mockGetPending;

    const signal = new Signal.State<number>(0);
    watcher.watch(signal);

    signal.set(1);
    expect(mockGetPending).toBeCalled();
  });

  it('should not break a computed signal to watch it before getting its value', () => {
    const signal = new Signal.State(0);
    const computedSignal = new Signal.Computed(() => signal.get());
    const watcher = new Signal.subtle.Watcher(() => {});
    expect(computedSignal.get()).toBe(0);
    signal.set(1);
    watcher.watch(computedSignal);
    expect(computedSignal.get()).toBe(1);
    watcher.unwatch(computedSignal);
    expect(computedSignal.get()).toBe(1);
  });
});
