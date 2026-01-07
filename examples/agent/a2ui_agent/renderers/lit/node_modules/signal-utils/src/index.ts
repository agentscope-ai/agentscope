import { Signal } from "signal-polyfill";

/**
 * Usage:
 * ```js
 * export class Counter {
 *   @signal accessor #value = 0;
 *
 *   get doubled() {
 *     return this.#value * 2;
 *   }
 *
 *   increment() {
 *     this.#value++;
 *   }
 *
 *   decrement() {
 *     if (this.#value > 0) {
 *       this.#value--;
 *     }
 *   }
 * }
 * ```
 */
export function signal<Value = any>(
  ...args: Parameters<typeof stateDecorator<Value>>
): ReturnType<typeof stateDecorator<Value>>;

/**
 * Usage:
 * ```js
 * import { signal } from 'signal-utils';
 *
 * export class Counter {
 *   @signal accessor #value = 0;
 *
 *   @signal
 *   get expensive() {
 *     // some expensive operation
 *     return this.#value * 2;
 *   }
 *
 *   increment() {
 *     this.#value++;
 *   }
 * }
 * ```
 */
export function signal<Value = any>(
  ...args: Parameters<typeof computedDecorator<Value>>
): ReturnType<typeof computedDecorator<Value>>;

export function signal<Value = any>(
  ...args:
    | Parameters<typeof stateDecorator<Value>>
    | Parameters<typeof computedDecorator<Value>>
) {
  if (args[1].kind === "accessor") {
    return stateDecorator(
      ...(args as Parameters<typeof stateDecorator<Value>>),
    );
  }

  if (args[1].kind === "getter") {
    return computedDecorator(
      ...(args as Parameters<typeof computedDecorator<Value>>),
    );
  }

  throw new Error(`@signal can only be used on accessors or getters`);
}

function stateDecorator<Value = any>(
  target: ClassAccessorDecoratorTarget<unknown, Value>,
  context: ClassAccessorDecoratorContext,
): ClassAccessorDecoratorResult<unknown, Value> {
  const { get } = target;

  if (context.kind !== "accessor") {
    throw new Error(`Expected to be used on an accessor property`);
  }

  return {
    get(): Value {
      // SAFETY: does TS not allow us to have a different type internally?
      //         maybe I did something goofy.
      return (get.call(this) as Signal.State<Value>).get();
    },

    set(value: Value) {
      // SAFETY: does TS not allow us to have a different type internally?
      //         maybe I did something goofy.
      (get.call(this) as Signal.State<Value>).set(value);
    },

    init(value: Value) {
      // SAFETY: does TS not allow us to have a different type internally?
      //         maybe I did something goofy.
      return new Signal.State(value) as unknown as Value;
    },
  };
}

function computedDecorator<Value = any>(
  target: () => Value,
  context: ClassGetterDecoratorContext,
): () => Value {
  const kind = context.kind;

  if (kind !== "getter") {
    throw new Error(`Can only use @cached on getters.`);
  }

  const caches = new WeakMap<
    typeof target,
    WeakMap<object, Signal.Computed<Value>>
  >();

  return function (this: unknown) {
    let cache = caches.get(target);
    if (!cache) {
      cache = new WeakMap();
      caches.set(target, cache);
    }
    let effect = cache.get(this as object);
    if (!effect) {
      effect = new Signal.Computed(() => target.call(this));
      cache.set(this as object, effect);
    }

    return effect.get();
  };
}
