import { Signal } from "signal-polyfill";

/** A very cheap representation of the of a promise. */
type StateRepr<T> =
  | [tag: "PENDING"]
  | [tag: "RESOLVED", value: T]
  | [tag: "REJECTED", error: unknown];

// We only need a single instance of the pending state in our system, since it
// is otherwise unparameterized (unlike the resolved and rejected states).
const PENDING = ["PENDING"] as [tag: "PENDING"];

// NOTE: this class is the implementation behind the types; the public types
// layer on additional safety. See below! Additionally, the docs for the class
// itself are applied to the export, not to the class, so that they will appear
// when users refer to *that*.
export class SignalAsyncData<T> {
  /**
    The internal state management for the promise.

    - `readonly` so it cannot be mutated by the class itself after instantiation
    - uses true native privacy so it cannot even be read (and therefore *cannot*
      be depended upon) by consumers.
   */
  readonly #state = new Signal.State<StateRepr<T>>(PENDING);
  readonly #promise: T | Promise<T>;

  /**
    @param promise The promise to inspect.
   */
  constructor(data: T | Promise<T>) {
    // SAFETY: do not let TS type-narrow this condition,
    //         else, `this` is of type `never`
    if ((this.constructor as any) !== SignalAsyncData) {
      throw new Error("tracked-async-data cannot be subclassed");
    }

    if (!isPromiseLike(data)) {
      this.#state.set(["RESOLVED", data]);
      this.#promise = Promise.resolve(data);
      return;
    }

    this.#promise = data;

    // Otherwise, we know that haven't yet handled that promise anywhere in the
    // system, so we continue creating a new instance.
    this.#promise.then(
      (value) => {
        this.#state.set(["RESOLVED", value]);
      },
      (error) => {
        this.#state.set(["REJECTED", error]);
      },
    );
  }

  then = (
    onResolved: (value: T) => null | undefined,
    onRejected?: (reason: unknown) => void,
  ) => {
    if (isPromiseLike(this.#promise)) {
      return this.#promise.then(onResolved).catch(onRejected);
    }

    if (this.state === "RESOLVED") {
      return onResolved(this.value as T);
    }

    if (this.state === "REJECTED" && onRejected) {
      return onRejected(this.error);
    }

    throw new Error(`Value was not resolveable`);
  };

  /**
   * The resolution state of the promise.
   */
  get state(): StateRepr<T>[0] {
    return this.#state.get()[0];
  }

  /**
    The value of the resolved promise.

    @note It is only valid to access `error` when `.isError` is true, that is,
      when `TrackedAsyncData.state` is `"ERROR"`.
    @warning You should not rely on this returning `T | null`! In a future
      breaking change which drops support for pre-Octane idioms, it will *only*
      return `T` and will *throw* if you access it when the state is wrong.
   */
  get value(): T | null {
    let data = this.#state.get();
    return data[0] === "RESOLVED" ? data[1] : null;
  }

  /**
    The error of the rejected promise.

    @note It is only valid to access `error` when `.isError` is true, that is,
      when `TrackedAsyncData.state` is `"ERROR"`.
    @warning You should not rely on this returning `null` when the state is not
      `"ERROR"`! In a future breaking change which drops support for pre-Octane
      idioms, it will *only* return `E` and will *throw* if you access it when
      the state is wrong.
   */
  get error(): unknown {
    let data = this.#state.get();
    return data[0] === "REJECTED" ? data[1] : null;
  }

  /**
    Is the state `"PENDING"`.
   */
  get isPending(): boolean {
    return this.state === "PENDING";
  }

  /** Is the state `"RESOLVED"`? */
  get isResolved(): boolean {
    return this.state === "RESOLVED";
  }

  /** Is the state `"REJECTED"`? */
  get isRejected(): boolean {
    return this.state === "REJECTED";
  }

  // SAFETY: casts are safe because we uphold these invariants elsewhere in the
  // class. It would be great if we could guarantee them statically, but getters
  // do not return information about the state of the class well.
  toJSON(): JSONRepr<T> {
    const { isPending, isResolved, isRejected } = this;
    if (isPending) {
      return { isPending, isResolved, isRejected } as JSONRepr<T>;
    } else if (isResolved) {
      return {
        isPending,
        isResolved,
        value: this.value,
        isRejected,
      } as JSONRepr<T>;
    } else {
      return {
        isPending,
        isResolved,
        isRejected,
        error: this.error,
      } as JSONRepr<T>;
    }
  }

  toString(): string {
    return JSON.stringify(this.toJSON(), null, 2);
  }
}

/**
  The JSON representation of a `TrackedAsyncData`, useful for e.g. logging.

  Note that you cannot reconstruct a `TrackedAsyncData` *from* this, because it
  is impossible to get the original promise when in a pending state!
 */
export type JSONRepr<T> =
  | { isPending: true; isResolved: false; isRejected: false }
  | { isPending: false; isResolved: true; isRejected: false; value: T }
  | { isPending: false; isResolved: false; isRejected: true; error: unknown };

// The exported type is the intersection of three narrowed interfaces. Doing it
// this way has two nice benefits:
//
// 1.  It allows narrowing to work. For example:
//
//     ```ts
//     let data = new TrackedAsyncData(Promise.resolve("hello"));
//     if (data.isPending) {
//       data.value;  // null
//       data.error;  // null
//     } else if (data.isPending) {
//       data.value;  // null
//       data.error;  // null
//     } else if (data.isRejected) {
//       data.value;  // null
//       data.error;  // unknown, can now be narrowed
//     }
//     ```
//
//     This dramatically improves the usability of the type in type-aware
//     contexts (including with templates when using Glint!)
//
// 2.  Using `interface extends` means that (a) it is guaranteed to be a subtype
//     of the `_TrackedAsyncData` type, (b) that the docstrings applied to the
//     base type still work, and (c) that the types which are *common* to the
//     shared implementations (i.e. `.toJSON()` and `.toString()`) are shared
//     automatically.

/** Utility type to check whether the string `key` is a property on an object */
function has<K extends PropertyKey, T extends object>(
  key: K,
  t: T,
): t is T & Record<K, unknown> {
  return key in t;
}

function isPromiseLike(data: unknown): data is PromiseLike<unknown> {
  return (
    typeof data === "object" &&
    data !== null &&
    has("then", data) &&
    typeof data.then === "function"
  );
}

/**
  Given a `Promise`, return a `TrackedAsyncData` object which exposes the state
  of the promise, as well as the resolved value or thrown error once the promise
  resolves or fails.

  The function and helper accept any data, so you may use it freely in contexts
  where you are receiving data which may or may not be a `Promise`.

  ## Example

  Given a backing class like this:

  ```js
  import Component from '@glimmer/component';
  import { signal } from 'signal-utils';
  import { load } from 'ember-tracked-data/helpers/load';

  export default class ExtraInfo extends Component {
    @signal
    get someData() {return load(fetch('some-url', this.args.someArg));
    }
  }
  ```

  You can use the result in your template like this:

  ```hbs
  {{#if this.someData.isLoading}}
    loading...
  {{else if this.someData.isLoaded}}
    {{this.someData.value}}
  {{else if this.someData.isError}}
    Whoops! Something went wrong: {{this.someData.error}}
  {{/if}}
  ```

  You can also use the helper directly in your template:

  ```hbs
  {{#let (load @somePromise) as |data|}}
    {{#if data.isLoading}}
      <LoadingSpinner />
    {{else if data.isLoaded}}
      <SomeComponent @data={{data.value}} />
    {{else if data.isError}}
      <Error @cause={{data.error}} />
    {{/if}}
  {{/let}}
  ```

  @param data The (async) data we want to operate on: a value or a `Promise` of
    a value.
  @returns An object containing the state(, value, and error).
  @note Prefer to use `TrackedAsyncData` directly! This function is provided
    simply for symmetry with the helper and backwards compatibility.
 */
export function load<T>(data: T | Promise<T>): SignalAsyncData<T> {
  return new SignalAsyncData(data);
}
