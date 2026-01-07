import { Signal } from "signal-polyfill";
import { createStorage } from "./-private/util.ts";

/**
 * Implementation based of tracked-built-ins' TrackedObject
 * https://github.com/tracked-tools/tracked-built-ins/blob/master/addon/src/-private/object.js
 */
export class SignalObjectImpl {
  static fromEntries<T = unknown>(
    entries: Iterable<readonly [PropertyKey, T]>,
  ) {
    return new SignalObjectImpl(Object.fromEntries(entries)) as T;
  }
  #storages = new Map<PropertyKey, Signal.State<null>>();
  #collection = createStorage();

  constructor(obj = {}) {
    let proto = Object.getPrototypeOf(obj);
    let descs = Object.getOwnPropertyDescriptors(obj);

    let clone = Object.create(proto);

    for (let prop in descs) {
      // SAFETY: we just iterated over the property, so having to do an
      //         existence check here is a little silly
      Object.defineProperty(clone, prop, descs[prop]!);
    }

    let self = this;

    return new Proxy(clone, {
      get(target, prop, receiver) {
        // we don't use the signals directly
        // because we don't know (nor care!) what the value would be
        // and the value could be replaced
        // (this is also important for supporting getters)
        self.#readStorageFor(prop);

        return Reflect.get(target, prop, receiver);
      },

      has(target, prop) {
        self.#readStorageFor(prop);

        return prop in target;
      },

      ownKeys(target) {
        self.#collection.get();

        return Reflect.ownKeys(target);
      },

      set(target, prop, value, receiver) {
        let result = Reflect.set(target, prop, value, receiver);

        self.#dirtyStorageFor(prop);
        self.#dirtyCollection();

        return result;
      },

      deleteProperty(target, prop) {
        if (prop in target) {
          delete target[prop];
          self.#dirtyStorageFor(prop);
          self.#dirtyCollection();
        }

        return true;
      },

      getPrototypeOf() {
        return SignalObjectImpl.prototype;
      },
    });
  }

  #readStorageFor(key: PropertyKey) {
    let storage = this.#storages.get(key);

    if (storage === undefined) {
      storage = createStorage();
      this.#storages.set(key, storage);
    }

    storage.get();
  }

  #dirtyStorageFor(key: PropertyKey) {
    const storage = this.#storages.get(key);

    if (storage) {
      storage.set(null);
    }
  }

  #dirtyCollection() {
    this.#collection.set(null);
  }
}

interface SignalObject {
  fromEntries<T = unknown>(
    entries: Iterable<readonly [PropertyKey, T]>,
  ): { [k: string]: T };

  new <T extends Record<PropertyKey, unknown> = Record<PropertyKey, unknown>>(
    obj?: T,
  ): T;
}
// Types are too hard in proxy-implementation
// we want TS to think the SignalObject is Object-like

/**
 * Create a reactive Object, backed by Signals, using a Proxy.
 * This allows dynamic creation and deletion of signals using the object primitive
 * APIs that most folks are familiar with -- the only difference is instantiation.
 * ```js
 * const obj = new SignalObject({ foo: 123 });
 *
 * obj.foo // 123
 * obj.foo = 456
 * obj.foo // 456
 * obj.bar = 2
 * obj.bar // 2
 * ```
 */
export const SignalObject: SignalObject =
  SignalObjectImpl as unknown as SignalObject;

export function signalObject<T extends Record<PropertyKey, unknown>>(
  obj?: T | undefined,
) {
  return new SignalObject(obj);
}
