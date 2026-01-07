import {
  createStorage,
  type StorageWeakMap,
  type Storage,
} from "./-private/util.ts";

export class SignalWeakSet<T extends object = object> implements WeakSet<T> {
  private storages: StorageWeakMap<T> = new WeakMap();

  private vals: WeakSet<T>;

  private storageFor(key: T): Storage {
    const storages = this.storages;
    let storage = storages.get(key);

    if (storage === undefined) {
      storage = createStorage();
      storages.set(key, storage);
    }

    return storage;
  }

  private dirtyStorageFor(key: T): void {
    const storage = this.storages.get(key);

    if (storage) {
      storage.set(null);
    }
  }

  constructor(values?: readonly T[] | null) {
    this.vals = new WeakSet(values);
  }

  has(value: T): boolean {
    this.storageFor(value).get();

    return this.vals.has(value);
  }

  add(value: T): this {
    // Add to vals first to get better error message
    this.vals.add(value);

    this.dirtyStorageFor(value);

    return this;
  }

  delete(value: T): boolean {
    this.dirtyStorageFor(value);

    return this.vals.delete(value);
  }

  get [Symbol.toStringTag](): string {
    return this.vals[Symbol.toStringTag];
  }
}

// So instanceof works
Object.setPrototypeOf(SignalWeakSet.prototype, WeakSet.prototype);
