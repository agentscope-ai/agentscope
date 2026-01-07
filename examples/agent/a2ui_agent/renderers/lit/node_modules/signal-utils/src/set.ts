import {
  createStorage,
  type StorageMap,
  type Storage,
} from "./-private/util.ts";

export class SignalSet<T = unknown> implements Set<T> {
  private collection = createStorage();

  private storages: StorageMap<T> = new Map();

  private vals: Set<T>;

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

  constructor();
  constructor(values: readonly T[] | null);
  constructor(iterable: Iterable<T>);
  constructor(existing?: readonly T[] | Iterable<T> | null | undefined) {
    this.vals = new Set(existing);
  }

  // **** KEY GETTERS ****
  has(value: T): boolean {
    this.storageFor(value).get();

    return this.vals.has(value);
  }

  // **** ALL GETTERS ****
  entries(): IterableIterator<[T, T]> {
    this.collection.get();

    return this.vals.entries();
  }

  keys(): IterableIterator<T> {
    this.collection.get();

    return this.vals.keys();
  }

  values(): IterableIterator<T> {
    this.collection.get();

    return this.vals.values();
  }

  forEach(fn: (value1: T, value2: T, set: Set<T>) => void): void {
    this.collection.get();

    this.vals.forEach(fn);
  }

  get size(): number {
    this.collection.get();

    return this.vals.size;
  }

  [Symbol.iterator](): IterableIterator<T> {
    this.collection.get();

    return this.vals[Symbol.iterator]();
  }

  get [Symbol.toStringTag](): string {
    return this.vals[Symbol.toStringTag];
  }

  // **** KEY SETTERS ****
  add(value: T): this {
    this.dirtyStorageFor(value);
    this.collection.set(null);

    this.vals.add(value);

    return this;
  }

  delete(value: T): boolean {
    this.dirtyStorageFor(value);
    this.collection.set(null);

    return this.vals.delete(value);
  }

  // **** ALL SETTERS ****
  clear(): void {
    this.storages.forEach((s) => s.set(null));
    this.collection.set(null);

    this.vals.clear();
  }
}

// So instanceof works
Object.setPrototypeOf(SignalSet.prototype, Set.prototype);
