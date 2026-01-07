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
export declare const batch: (fn: () => void) => void;
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
export declare const batchedEffect: (effectFn: () => void) => () => void;
//# sourceMappingURL=batched-effect.d.ts.map