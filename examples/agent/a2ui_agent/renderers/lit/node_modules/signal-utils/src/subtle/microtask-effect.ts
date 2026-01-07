import { Signal } from "signal-polyfill";

// NOTE: this implementation *LEAKS*
//       because there is nothing to unwatch a computed.

let pending = false;

let watcher = new Signal.subtle.Watcher(() => {
  if (!pending) {
    pending = true;
    queueMicrotask(() => {
      pending = false;
      flushPending();
    });
  }
});

function flushPending() {
  for (const signal of watcher.getPending()) {
    signal.get();
  }

  // Keep watching... we don't know when we're allowed to stop watching
  watcher.watch();
}

/**
 * ⚠️ WARNING: Nothing unwatches ⚠️
 * This will produce a memory leak.
 */
export function effect(cb: () => void) {
  let c = new Signal.Computed(() => cb());

  watcher.watch(c);

  c.get();

  return () => {
    watcher.unwatch(c);
  };
}
