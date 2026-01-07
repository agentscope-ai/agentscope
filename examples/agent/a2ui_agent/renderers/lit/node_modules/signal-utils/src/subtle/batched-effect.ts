import { Signal } from "signal-polyfill";

const notifiedEffects = new Set<{
  computed: Signal.Computed<void>;
  watcher: Signal.subtle.Watcher;
}>();

let batchDepth = 0;

/**
 * Runs the given function inside of a "batch" and calls any batched effects
 * (those created with `batchEffect()`) that depend on updated signals
 * synchronously after the function completes.
 *
 * Batches can be nested, and effects will only be called once at the end of the
 * outermost batch.
 *
 * Batching does not change how the signal graph updates, or change any other
 * watcher or effect system. Accessing signals that are updated within a batch
 * will return their updates value. Other computations, watcher, and effects
 * created outside of a batch that depend on updated signals will be run as
 * usual.
 *
 * @param fn The function to run inside the batch.
 */
export const batch = (fn: () => void) => {
  batchDepth++;
  try {
    // Run the function to notifiy watchers
    fn();
  } finally {
    batchDepth--;

    if (batchDepth !== 0) {
      return;
    }

    // Copy then clear the notified effects
    const effects = [...notifiedEffects];
    notifiedEffects.clear();

    // Run all the batched effect callbacks and re-enable the watchers
    let exceptions!: any[];

    for (const { computed, watcher } of effects) {
      watcher.watch(computed);
      try {
        computed.get();
      } catch (e) {
        (exceptions ??= []).push(e);
      }
    }

    if (exceptions !== undefined) {
      if (exceptions.length === 1) {
        throw exceptions![0];
      } else {
        throw new AggregateError(
          exceptions!,
          "Multiple exceptions thrown in batched effects",
        );
      }
    }
  }
};

/**
 * Creates an effect that runs synchronously at the end of a `batch()` call if
 * any of the signals it depends on have been updated.
 *
 * The effect also runs asynchronously, on the microtask queue, if any of the
 * signals it depends on have been updated outside of a `batch()` call.
 *
 * @param effectFn The function to run as an effect.
 * @returns A function that stops and disposes the effect.
 */
export const batchedEffect = (effectFn: () => void) => {
  const computed = new Signal.Computed(effectFn);
  const watcher = new Signal.subtle.Watcher(async () => {
    // Synchonously add the effect to the notified effects
    notifiedEffects.add(entry);

    // Check if our effect is still in the notified effects
    await 0;

    if (notifiedEffects.has(entry)) {
      // If it is, then we call it async and remove it
      notifiedEffects.delete(entry);
      computed.get();
    }
  });
  const entry = { computed, watcher };
  watcher.watch(computed);
  computed.get();
  return () => {
    watcher.unwatch(computed);
    notifiedEffects.delete(entry);
  };
};
