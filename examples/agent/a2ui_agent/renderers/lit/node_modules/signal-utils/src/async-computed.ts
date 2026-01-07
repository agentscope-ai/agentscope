import { Signal } from "signal-polyfill";

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
export class AsyncComputed<T> {
  // Whether we have been notified of a pending update from the watcher. This is
  // set synchronously when any dependencies of the compute function change.
  #isNotified = false;
  #status = new Signal.State<AsyncComputedStatus>("initial");

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
  get status() {
    this.run();
    // Unconditionally read the status signal to ensure that any signals that
    // read it are tracked as dependents.
    const currentState = this.#status.get();
    // Read from the non-signal #isNotified field, which can be set by the
    // watcher synchronously.
    return this.#isNotified ? "pending" : currentState;
  }

  #value: Signal.State<T | undefined>;

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
  get value() {
    this.run();
    return this.#value.get();
  }

  #error = new Signal.State<unknown | undefined>(undefined);

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
  get error() {
    this.run();
    return this.#error.get();
  }

  #deferred = new Signal.State<PromiseWithResolvers<T> | undefined>(undefined);

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
  get complete(): Promise<T> {
    this.run();
    // run() will have created a new deferred if needed.
    return this.#deferred.get()!.promise;
  }

  #computed: Signal.Computed<void>;

  #watcher: Signal.subtle.Watcher;

  // A unique ID for the current run. This is used to ensure that runs that have
  // been preempted by a new run do not update state or resolve the deferred
  // with the wrong result.
  #currentRunId = 0;

  #currentAbortController?: AbortController;

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
  constructor(
    fn: (abortSignal: AbortSignal) => Promise<T>,
    options?: AsyncComputedOptions<T>,
  ) {
    this.#value = new Signal.State(options?.initialValue);
    this.#computed = new Signal.Computed(() => {
      const runId = ++this.#currentRunId;
      // Untrack reading the status signal to avoid triggering the computed when
      // the status changes.
      const status = Signal.subtle.untrack(() => this.#status.get());

      // If we're not already pending, create a new deferred to track the
      // completion of the run.
      if (status !== "pending") {
        this.#deferred.set(Promise.withResolvers());
      }
      this.#isNotified = false;
      this.#status.set("pending");

      this.#currentAbortController?.abort();
      this.#currentAbortController = new AbortController();

      fn(this.#currentAbortController.signal).then(
        (result) => {
          // If we've been preempted by a new run, don't update the status or
          // resolve the deferred.
          if (runId !== this.#currentRunId) {
            return;
          }
          this.#status.set("complete");
          this.#value.set(result);
          this.#error.set(undefined);
          this.#deferred.get()!.resolve(result);
        },
        (error) => {
          // If we've been preempted by a new run, don't update the status or
          // resolve the deferred.
          if (runId !== this.#currentRunId) {
            return;
          }
          this.#status.set("error");
          this.#error.set(error);
          this.#value.set(undefined);
          this.#deferred.get()!.reject(error);
        },
      );
    });
    this.#watcher = new Signal.subtle.Watcher(async () => {
      // Set the #isNotified flag synchronously when any dependencies change, so
      // that it can be read synchronously by the status getter.
      this.#isNotified = true;
      this.#watcher.watch();
    });
    this.#watcher.watch(this.#computed);
  }

  /**
   * Returns the last value that the compute function resolved with, or
   * the initial value if the compute function has not yet been run.
   *
   * @throws The last error that the compute function threw, is the last run of
   * the compute function threw an error.
   */
  get() {
    const status = this.status;
    if (
      status === "error" ||
      (status === "pending" && this.error !== undefined)
    ) {
      throw this.error;
    }
    return this.value;
  }

  /**
   * Runs the compute function if it is not already running and its dependencies
   * have changed.
   */
  run() {
    this.#computed.get();
  }
}
