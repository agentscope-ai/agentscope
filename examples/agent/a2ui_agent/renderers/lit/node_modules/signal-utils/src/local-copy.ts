import { signal } from "./index.ts";

class Meta<Value> {
  prevRemote?: Value;
  peek?: () => Value;
  @signal accessor value: Value | undefined;
}

function getOrCreateMeta<Value>(
  instance: WeakKey,
  metas: WeakMap<WeakKey, Meta<Value>>,
  initializer?: Value | Function,
) {
  let meta = metas.get(instance);

  if (meta === undefined) {
    meta = new Meta<Value>();
    metas.set(instance, meta);

    meta.value = meta.peek =
      typeof initializer === "function"
        ? initializer.call(instance)
        : initializer;
  }

  return meta;
}

function get(obj: any, path: string) {
  let current = obj;
  let parts = path.split(".");

  for (let part of parts) {
    if (!current) return current;

    if (!(part in current)) {
      throw new Error(
        `sub-path ${part} (from ${path}) does not exist on ${JSON.stringify(current)}.`,
      );
    }

    current = current[part];
  }

  return current;
}

/**
 * Forks remote state for local modification
 *
 * ```js
 * import { Signal } from 'signal-polyfill';
 * import { localCopy } from 'signal-utils';
 *
 * const remote = new Signal.State(0);
 *
 * const local = localCopy(() => remote.get());
 * ```
 */
export function localCopy<Value = any>(
  fn: () => Value,
): { get(): Value; set(v: Value): void };

/**
 * Forks remote state for local modification
 *
 * ```js
 * import { localCopy } from 'signal-utils';
 *
 * class Demo {
 *    @localCopy('remote.value') accessor localValue;
 * }
 * ```
 */
export function localCopy<Value = any, This extends WeakKey = WeakKey>(
  memo: string,
  initializer?: Value | (() => Value),
): (
  _target: ClassAccessorDecoratorTarget<This, Value>,
  _context: ClassAccessorDecoratorContext<This, Value>,
) => ClassAccessorDecoratorResult<This, Value>;

/**
 * Forks remote state for local modification
 *
 * ```js
 * import { localCopy } from 'signal-utils';
 *
 * class Demo {
 *    @localCopy('remote.value') accessor localValue;
 * }
 * ```
 */
export function localCopy<Value = any, This extends WeakKey = WeakKey>(
  ...args: any[]
) {
  if (typeof args[0] === "function") {
    return localCopyFn<Value, This>(args[0]);
  }

  let [first, second] = args;

  return localCopyDecorator<Value, This>(
    first,
    second as undefined | Value | (() => Value),
  );
}

function localCopyFn<Value = any, This extends WeakKey = WeakKey>(
  memoFn: () => Value,
) {
  let metas = new WeakMap<WeakKey, Meta<Value>>();

  return {
    get(this: This): Value {
      let meta = getOrCreateMeta<Value>(this, metas);
      let { prevRemote } = meta;

      let incomingValue = memoFn();

      if (prevRemote !== incomingValue) {
        // If the incoming value is not the same as the previous incoming value,
        // update the local value to match the new incoming value, and update
        // the previous incoming value.
        meta.value = meta.prevRemote = incomingValue;
      }

      return meta.value as Value;
    },

    set(this: WeakKey, value: Value) {
      if (!metas.has(this)) {
        let meta = getOrCreateMeta(this, metas);
        meta.prevRemote = memoFn();
        meta.value = value;
        return;
      }

      getOrCreateMeta(this, metas).value = value;
    },
  };
}

function localCopyDecorator<Value = any, This extends WeakKey = WeakKey>(
  memo: string,
  initializer: undefined | Value | (() => Value),
) {
  if (typeof memo !== "string") {
    throw new Error(
      `@localCopy() must be given a memo path as its first argument, received \`${String(
        memo,
      )}\``,
    );
  }

  let metas = new WeakMap<WeakKey, Meta<Value>>();

  return function localCopyDecorator(
    _target: ClassAccessorDecoratorTarget<This, Value>,
    _context: ClassAccessorDecoratorContext<This, Value>,
  ): ClassAccessorDecoratorResult<This, Value> {
    let memoFn = (obj: any) => get(obj, memo);

    return {
      get(this: This): Value {
        let meta = getOrCreateMeta<Value>(this, metas, initializer);
        let { prevRemote } = meta;

        let incomingValue = memoFn(this);

        if (prevRemote !== incomingValue) {
          // If the incoming value is not the same as the previous incoming value,
          // update the local value to match the new incoming value, and update
          // the previous incoming value.
          meta.value = meta.prevRemote = incomingValue;
        }

        return meta.value as Value;
      },

      set(this: WeakKey, value: Value) {
        if (!metas.has(this)) {
          let meta = getOrCreateMeta(this, metas, initializer);
          meta.prevRemote = memoFn(this);
          meta.value = value;
          return;
        }

        getOrCreateMeta(this, metas, initializer).value = value;
      },
    };
  };
}
