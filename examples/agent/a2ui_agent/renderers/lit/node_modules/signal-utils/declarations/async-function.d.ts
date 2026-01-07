import { SignalAsyncData } from './async-data.ts';

/**
 * Any tracked data accessed in a tracked function _before_ an `await`
 * will "entangle" with the function -- we can call these accessed tracked
 * properties, the "tracked prelude". If any properties within the tracked
 * payload  change, the function will re-run.
 */
export declare function signalFunction<Return>(fn: () => Return): State<Return>;
/**
 * State container that represents the asynchrony of a `signalFunction`
 */
export declare class State<Value> {
    #private;
    get data(): SignalAsyncData<Value> | null;
    get promise(): Value | undefined;
    get caughtError(): unknown;
    constructor(fn: () => Value);
    get state(): "PENDING" | "RESOLVED" | "REJECTED" | "UNSTARTED";
    /**
     * Initially true, and remains true
     * until the underlying promise resolves or rejects.
     */
    get isPending(): boolean;
    /**
     * Alias for `isResolved || isRejected`
     */
    get isFinished(): boolean;
    /**
     * Alias for `isFinished`
     * which is in turn an alias for `isResolved || isRejected`
     */
    get isSettled(): boolean;
    /**
     * Alias for `isPending`
     */
    get isLoading(): boolean;
    /**
     * When true, the function passed to `signalFunction` has resolved
     */
    get isResolved(): boolean;
    /**
     * Alias for `isRejected`
     */
    get isError(): boolean;
    /**
     * When true, the function passed to `signalFunction` has errored
     */
    get isRejected(): boolean;
    /**
     * this.data may not exist yet.
     *
     * Additionally, prior iterations of TrackedAsyncData did
     * not allow the accessing of data before
     * .state === 'RESOLVED'  (isResolved).
     *
     * From a correctness standpoint, this is perfectly reasonable,
     * as it forces folks to handle the states involved with async functions.
     *
     * The original version of `signalFunction` did not use TrackedAsyncData,
     * and did not have these strictnesses upon property access, leaving folks
     * to be as correct or as fast/prototype-y as they wished.
     *
     * For now, `signalFunction` will retain that flexibility.
     */
    get value(): Awaited<Value> | null;
    /**
     * When the function passed to `signalFunction` throws an error,
     * that error will be the value returned by this property
     */
    get error(): {} | null;
    /**
     * Will re-invoke the function passed to `signalFunction`
     * this will also re-set some properties on the `State` instance.
     * This is the same `State` instance as before, as the `State` instance
     * is tied to the `fn` passed to `signalFunction`
     *
     * `error` or `resolvedValue` will remain as they were previously
     * until this promise resolves, and then they'll be updated to the new values.
     */
    retry: () => Promise<void>;
}
//# sourceMappingURL=async-function.d.ts.map