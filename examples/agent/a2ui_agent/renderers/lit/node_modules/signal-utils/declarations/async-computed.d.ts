export type AsyncComputedStatus = "initial" | "pending" | "complete" | "error";
export interface AsyncComputedOptions<T> {
    /**
     * The initial value of the AsyncComputed.
     */
    initialValue?: T;
}
/**
 * A signal-like object that represents an asynchronous computation.
 *
 * AsyncComputed takes a compute function that performs an asynchronous
 * computation and runs it inside a computed signal, while tracking the status
 * of the computation, including its most recent completion value and error.
 *
 * Compute functions are run when the `value`, `error`, or `complete` properties
 * are read, or when `get()` or `run()` are called, and are re-run when any
 * signals that they read change.
 *
 * If a new run of the compute function is started before the previous run has
 * completed, the previous run will have its AbortSignal aborted, and the result
 * of the previous run will be ignored.
 */
export declare class AsyncComputed<T> {
    #private;
    /**
     * The current status of the AsyncComputed, which is one of 'initial',
     * 'pending', 'complete', or 'error'.
     *
     * The status will be 'initial' until the compute function is first run.
     *
     * The status will be 'pending' while the compute function is running. If the
     * status is 'pending', the `value` and `error` properties will be the result
     * of the previous run of the compute function.
     *
     * The status will be 'complete' when the compute function has completed
     * successfully. If the status is 'complete', the `value` property will be the
     * result of the previous run of the compute function and the `error` property
     * will be `undefined`.
     *
     * The status will be 'error' when the compute function has completed with an
     * error. If the status is 'error', the `error` property will be the error
     * that was thrown by the previous run of the compute function and the `value`
     * property will be `undefined`.
     *
     * This value is read from a signal, so any signals that read it will be
     * tracked as dependents of it.
     *
     * Accessing this property will cause the compute function to run if it hasn't
     * already.
     */
    get status(): AsyncComputedStatus;
    /**
     * The last value that the compute function resolved with, or `undefined` if
     * the last run of the compute function threw an error. If the compute
     * function has not yet been run `value` will be the value of the
     * `initialValue` or `undefined`.
     *
     * This value is read from a signal, so any signals that read it will be
     * tracked as dependents of it.
     *
     * Accessing this property will cause the compute function to run if it hasn't
     * already.
     */
    get value(): T | undefined;
    /**
     * The last error that the compute function threw, or `undefined` if the last
     * run of the compute function resolved successfully, or if the compute
     * function has not yet been run.
     *
     * This value is read from a signal, so any signals that read it will be
     * tracked as dependents of it.
     *
     * Accessing this property will cause the compute function to run if it hasn't
     * already.
     */
    get error(): unknown;
    /**
     * A promise that resolves when the compute function has completed, or rejects
     * if the compute function throws an error.
     *
     * If a new run of the compute function is started before the previous run has
     * completed, the promise will resolve with the result of the new run.
     *
     * This value is read from a signal, so any signals that read it will be
     * tracked as dependents of it. The identity of the promise will change if the
     * compute function is re-run after having completed or errored.
     *
     * Accessing this property will cause the compute function to run if it hasn't
     * already.
     */
    get complete(): Promise<T>;
    /**
     * Creates a new AsyncComputed signal.
     *
     * @param fn The function that performs the asynchronous computation. Any
     * signals read synchronously - that is, before the first await - will be
     * tracked as dependencies of the AsyncComputed, and cause the function to
     * re-run when they change.
     *
     * @param options.initialValue The initial value of the AsyncComputed.
     */
    constructor(fn: (abortSignal: AbortSignal) => Promise<T>, options?: AsyncComputedOptions<T>);
    /**
     * Returns the last value that the compute function resolved with, or
     * the initial value if the compute function has not yet been run.
     *
     * @throws The last error that the compute function threw, is the last run of
     * the compute function threw an error.
     */
    get(): T | undefined;
    /**
     * Runs the compute function if it is not already running and its dependencies
     * have changed.
     */
    run(): void;
}
//# sourceMappingURL=async-computed.d.ts.map