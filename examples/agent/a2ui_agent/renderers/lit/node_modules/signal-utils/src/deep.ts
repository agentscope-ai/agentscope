/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable @typescript-eslint/ban-types */

// import { Signal } from "signal-polyfill";
import { createStorage, fnCacheFor, type Storage } from "./-private/util.ts";

// TODO: see if we can utilize these existing implementations
//       would these require yet another proxy?
//       Array clones the whole array and deep object does not
//       what are tradeoffs? does it matter much?
// import { SignalObject } from "./object.ts";
// import { SignalArray } from "./array.ts";

type PropertyList = Array<string | number | Symbol>;
type TrackedProxy<T> = T;

const COLLECTION = Symbol("__ COLLECTION __");

type Key = number | string | symbol;

const STORAGES_CACHE = new WeakMap<
  object | Array<unknown>,
  // The tracked storage for an object or array.
  // ie: TrackedArray, TrackedObject, but all in one
  Map<Key, Storage>
>();

function ensureStorages(context: any) {
  let existing = STORAGES_CACHE.get(context);

  if (!existing) {
    existing = new Map();
    STORAGES_CACHE.set(context, existing);
  }

  return existing;
}

function storageFor(context: any, key: Key) {
  let storages = ensureStorages(context);

  return storages.get(key);
}

export function initStorage(context: any, key: Key, initialValue: any = null) {
  let storages = ensureStorages(context);

  let initialStorage = createStorage(initialValue);

  storages.set(key, initialStorage);

  return initialStorage.get();
}

export function hasStorage(context: any, key: Key) {
  return Boolean(storageFor(context, key));
}

export function readStorage(context: any, key: Key) {
  let storage = storageFor(context, key);

  if (storage === undefined) {
    return initStorage(context, key, null);
  }

  return storage.get();
}

export function updateStorage(context: any, key: Key, value: any = null) {
  let storage = storageFor(context, key);

  if (!storage) {
    initStorage(context, key, value);

    return;
  }

  storage.set(value);
}

export function readCollection(context: any) {
  if (!hasStorage(context, COLLECTION)) {
    initStorage(context, COLLECTION, context);
  }

  return readStorage(context, COLLECTION);
}

export function dirtyCollection(context: any) {
  if (!hasStorage(context, COLLECTION)) {
    initStorage(context, COLLECTION, context);
  }

  return updateStorage(context, COLLECTION, context);
}

/**
 * Deeply track an Array, and all nested objects/arrays within.
 *
 * If an element / value is ever a non-object or non-array, deep-tracking will exit
 *
 */
export function deepSignal<T>(arr: T[]): TrackedProxy<T[]>;
/**
 * Deeply track an Object, and all nested objects/arrays within.
 *
 * If an element / value is ever a non-object or non-array, deep-tracking will exit
 *
 */
export function deepSignal<T extends Record<string, unknown>>(
  obj: T,
): TrackedProxy<T>;
/**
 * Deeply track an Object or Array, and all nested objects/arrays within.
 *
 * If an element / value is ever a non-object or non-array, deep-tracking will exit
 *
 */
export function deepSignal(...args: any): any;

export function deepSignal(...[target, context]: any[]): unknown {
  if ("kind" in context) {
    if (context.kind === "accessor") {
      return deepTrackedForDescriptor(target, context);
    }

    throw new Error(`Decorators of kind ${context.kind} are not supported.`);
  }

  return deep(target);
}

function deepTrackedForDescriptor<Value = any>(
  target: ClassAccessorDecoratorTarget<unknown, Value>,
  context: ClassAccessorDecoratorContext,
): ClassAccessorDecoratorResult<unknown, Value> {
  const { name: key } = context;
  const { get } = target;

  return {
    get(): Value {
      if (hasStorage(this, key)) {
        return readStorage(this, key) as Value;
      }

      let value = get.call(this); // already deep, due to init
      return initStorage(this, key, value) as Value;
    },

    set(value: Value) {
      let deepValue = deep(value);
      updateStorage(this, key, deepValue);
      // set.call(this, deepValue);
      //updateStorage(this, key, deepTracked(value));
      // SAFETY: does TS not allow us to have a different type internally?
      //         maybe I did something goofy.
      //(get.call(this) as Signal.State<Value>).set(value);
    },

    init(value: Value) {
      return deep(value);
    },
  };
}

const TARGET = Symbol("TARGET");
const IS_PROXIED = Symbol("IS_PROXIED");

const SECRET_PROPERTIES: PropertyList = [TARGET, IS_PROXIED];

const ARRAY_COLLECTION_PROPERTIES = ["length"];
const ARRAY_CONSUME_METHODS = [
  Symbol.iterator,
  "at",
  "concat",
  "entries",
  "every",
  "filter",
  "find",
  "findIndex",
  "findLast",
  "findLastIndex",
  "flat",
  "flatMap",
  "forEach",
  "group",
  "groupToMap",
  "includes",
  "indexOf",
  "join",
  "keys",
  "lastIndexOf",
  "map",
  "reduce",
  "reduceRight",
  "slice",
  "some",
  "toString",
  "values",
  "length",
];

const ARRAY_DIRTY_METHODS = [
  "sort",
  "fill",
  "pop",
  "push",
  "shift",
  "splice",
  "unshift",
  "reverse",
];

const ARRAY_QUERY_METHODS: PropertyList = [
  "indexOf",
  "contains",
  "lastIndexOf",
  "includes",
];

export function deep<T>(obj: T | null | undefined): T {
  if (obj === null || obj === undefined) {
    return obj as T;
  }

  if (obj[IS_PROXIED as keyof T]) {
    return obj;
  }

  if (Array.isArray(obj)) {
    return deepProxy(obj, arrayProxyHandler) as unknown as T;
  }

  if (typeof obj === "object") {
    return deepProxy(obj, objProxyHandler) as unknown as T;
  }

  return obj;
}

const arrayProxyHandler: ProxyHandler<Array<unknown>> = {
  get<T extends unknown[]>(target: T, property: keyof T, receiver: T) {
    let value = Reflect.get(target, property, receiver);

    if (property === TARGET) {
      return value;
    }

    if (property === IS_PROXIED) {
      return true;
    }

    if (typeof property === "string") {
      let parsed = parseInt(property, 10);

      if (!isNaN(parsed)) {
        // Why consume the collection?
        // because indices can change if the collection changes
        readCollection(target);
        readStorage(target, parsed);

        return deep(value);
      }

      if (ARRAY_COLLECTION_PROPERTIES.includes(property)) {
        readCollection(target);

        return value;
      }
    }

    if (typeof value === "function") {
      let fnCache = fnCacheFor(target);
      let existing = fnCache.get(property as KeyType);

      if (!existing) {
        let fn = (...args: unknown[]) => {
          if (typeof property === "string") {
            if (ARRAY_QUERY_METHODS.includes(property)) {
              readCollection(target);

              let fn = target[property];

              if (typeof fn === "function") {
                return fn.call(target, ...args.map(unwrap));
              }
            } else if (ARRAY_CONSUME_METHODS.includes(property)) {
              readCollection(target);
            } else if (ARRAY_DIRTY_METHODS.includes(property)) {
              dirtyCollection(target);
            }
          }

          return Reflect.apply(value, receiver, args);
        };

        fnCache.set(property as KeyType, fn);

        return fn;
      }

      return existing;
    }

    return value;
  },
  set(target, property, value, receiver) {
    if (typeof property === "string") {
      let parsed = parseInt(property, 10);

      if (!isNaN(parsed)) {
        updateStorage(target, property, value);
        // when setting, the collection must be dirtied.. :(
        // this is to support updating {{#each}},
        // which uses object identity by default
        dirtyCollection(target);

        return Reflect.set(target, property, value, receiver);
      } else if (property === "length") {
        dirtyCollection(target);

        return Reflect.set(target, property, value, receiver);
      }
    }

    dirtyCollection(target);

    return Reflect.set(target, property, value, receiver);
  },
  has(target, property) {
    if (SECRET_PROPERTIES.includes(property)) {
      return true;
    }

    readStorage(target, property);

    return property in target;
  },
  getPrototypeOf() {
    return Array.prototype;
  },
};

const objProxyHandler = {
  get<T extends object>(target: T, prop: keyof T, receiver: T) {
    if (prop === TARGET) {
      return target;
    }

    if (prop === IS_PROXIED) {
      return true;
    }

    readStorage(target, prop);

    return deep(Reflect.get(target, prop, receiver));
  },
  has<T extends object>(target: T, prop: keyof T) {
    if (SECRET_PROPERTIES.includes(prop)) {
      return true;
    }

    readStorage(target, prop);

    return prop in target;
  },

  ownKeys<T extends object>(target: T) {
    readCollection(target);

    return Reflect.ownKeys(target);
  },

  set<T extends object>(
    target: T,
    prop: keyof T,
    value: T[keyof T],
    receiver: T,
  ) {
    updateStorage(target, prop);
    dirtyCollection(target);

    return Reflect.set(target, prop, value, receiver);
  },

  getPrototypeOf() {
    return Object.prototype;
  },
};

const PROXY_CACHE = new WeakMap<any, object>();

function unwrap<T>(obj: T) {
  if (typeof obj === "object" && obj && TARGET in obj) {
    return obj[TARGET as keyof T];
  }

  return obj;
}

function deepProxy<T extends object>(
  obj: T,
  handler: ProxyHandler<T>,
): TrackedProxy<T> {
  let existing = PROXY_CACHE.get(obj);

  if (existing) {
    return existing as T;
  }

  let proxied = new Proxy(obj, handler);

  PROXY_CACHE.set(obj, proxied);

  return proxied as T;
}
