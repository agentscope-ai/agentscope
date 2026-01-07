import { Signal } from "signal-polyfill";
import { SignalAsyncData } from "./async-data.ts";

/**
 * Any tracked data accessed in a tracked function _before_ an `await`
 * will "entangle" with the function -- we can call these accessed tracked
 * properties, the "tracked prelude". If any properties within the tracked
 * payload  change, the function will re-run.
 */
export function signalFunction<Return>(fn: () => Return): State<Return> {
  if (arguments.length === 1) {
    if (typeof fn !== "function") {
      throw new Error("signalFunction must be called with a function passed");
    }
    return new State(fn);
  }

  throw new Error(
    "Unknown arity: signalFunction must be called with 1 argument",
  );
}

/**
 * State container that represents the asynchrony of a `signalFunction`
 */
export class State<Value> {
  #data = new Signal.State<SignalAsyncData<Value> | null>(null);
  get data() {
    this.#computed.get();
    return this.#data.get();
  }

  #promise = new Signal.State<Value | undefined>(undefined);
  get promise() {
    this.#computed.get();
    return this.#promise.get();
  }

  /**
   * ember-async-data doesn't catch errors,
   * so we can't rely on it to protect us from "leaky errors"
   * during rendering.
   *
   * See also: https://github.com/qunitjs/qunit/issues/1736
   */
  #caughtError = new Signal.State<unknown>(undefined);
  get caughtError() {
    this.#computed.get();
    return this.#caughtError.get();
  }

  #fn: () => Value;

  #computed: Signal.Computed<this>;

  constructor(fn: () => Value) {
    this.#fn = fn;
    this.#computed = new Signal.Computed(() => {
      this.retry();
      return this;
    });
  }

  get state(): "PENDING" | "RESOLVED" | "REJECTED" | "UNSTARTED" {
    this.#computed.get();
    return this.data?.state ?? "UNSTARTED";
  }

  /**
   * Initially true, and remains true
   * until the underlying promise resolves or rejects.
   */
  get isPending() {
    this.#computed.get();
    if (!this.data) return true;

    return this.data.isPending ?? false;
  }

  /**
   * Alias for `isResolved || isRejected`
   */
  get isFinished() {
    this.#computed.get();
    return this.isResolved || this.isRejected;
  }

  /**
   * Alias for `isFinished`
   * which is in turn an alias for `isResolved || isRejected`
   */
  get isSettled() {
    this.#computed.get();
    return this.isFinished;
  }

  /**
   * Alias for `isPending`
   */
  get isLoading() {
    this.#computed.get();
    return this.isPending;
  }

  /**
   * When true, the function passed to `signalFunction` has resolved
   */
  get isResolved() {
    this.#computed.get();
    return this.data?.isResolved ?? false;
  }

  /**
   * Alias for `isRejected`
   */
  get isError() {
    this.#computed.get();
    return this.isRejected;
  }

  /**
   * When true, the function passed to `signalFunction` has errored
   */
  get isRejected() {
    this.#computed.get();
    return this.data?.isRejected ?? Boolean(this.caughtError) ?? false;
  }

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
  get value(): Awaited<Value> | null {
    this.#computed.get();
    if (this.data?.isResolved) {
      // This is sort of a lie, but it ends up working out due to
      // how promises chain automatically when awaited
      return this.data.value as Awaited<Value>;
    }

    return null;
  }

  /**
   * When the function passed to `signalFunction` throws an error,
   * that error will be the value returned by this property
   */
  get error() {
    this.#computed.get();
    if (this.state === "UNSTARTED" && this.caughtError) {
      return this.caughtError;
    }

    if (this.data?.state !== "REJECTED") {
      return null;
    }

    if (this.caughtError) {
      return this.caughtError;
    }

    return this.data?.error ?? null;
  }

  /**
   * Will re-invoke the function passed to `signalFunction`
   * this will also re-set some properties on the `State` instance.
   * This is the same `State` instance as before, as the `State` instance
   * is tied to the `fn` passed to `signalFunction`
   *
   * `error` or `resolvedValue` will remain as they were previously
   * until this promise resolves, and then they'll be updated to the new values.
   */
  retry = async () => {
    try {
      /**
       * This function has two places where it can error:
       * - immediately when inovking `fn` (where auto-tracking occurs)
       * - after an await, "eventually"
       */
      await this.#dangerousRetry();
    } catch (e) {
      this.#caughtError.set(e);
    }
  };

  async #dangerousRetry() {
    // We've previously had data, but we're about to run-again.
    // we need to do this again so `isLoading` goes back to `true` when re-running.
    // NOTE: we want to do this _even_ if this.data is already null.
    //       it's all in the same tracking frame and the important thing is that
    //       we can't *read* data here.
    this.#data.set(null);

    // We need to invoke this before going async so that tracked properties are
    // consumed (entangled with) synchronously
    this.#promise.set(this.#fn());

    // TrackedAsyncData interacts with tracked data during instantiation.
    // We don't want this internal state to entangle with `signalFunction`
    // so that *only* the tracked data in `fn` can be entangled.
    await Promise.resolve();

    /**
     * Before we await to start a new request, let's clear our error.
     * This is detached from the tracking frame (via the above await),
     * se the UI can update accordingly, without causing us to refetch
     */
    this.#caughtError.set(null);
    this.#data.set(new SignalAsyncData<Value>(this.promise!));

    return this.promise;
  }
}
