import { Signal } from "signal-polyfill";

/**
 * equality check here is always false so that we can dirty the storage
 * via setting to _anything_
 *
 *
 * This is for a pattern where we don't *directly* use signals to back the values used in collections
 * so that instanceof checks and getters and other native features "just work" without having
 * to do nested proxying.
 *
 * (though, see deep.ts for nested / deep behavior)
 */
export const createStorage = (initial = null) =>
  new Signal.State(initial, { equals: () => false });

/**
 * Just an alias for brevity
 */
export type Storage = Signal.State<null>;
export type StorageMap<T> = Map<T, Storage>;
export type StorageWeakMap<T extends object> = WeakMap<T, Storage>;

const BOUND_FUNS = new WeakMap<object, Map<KeyType, unknown>>();

export function fnCacheFor<T extends object = object>(context: T) {
  let fnCache = BOUND_FUNS.get(context);

  if (!fnCache) {
    fnCache = new Map();
    BOUND_FUNS.set(context, fnCache);
  }

  return fnCache; // as Map<keyof T, T[keyof T]>;
}
