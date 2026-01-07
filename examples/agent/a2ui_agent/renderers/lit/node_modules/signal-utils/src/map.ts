import { createStorage, type StorageMap } from "./-private/util.ts";

export class SignalMap<K = unknown, V = unknown> implements Map<K, V> {
  private collection = createStorage();

  private storages: StorageMap<K> = new Map();

  private vals: Map<K, V>;

  private readStorageFor(key: K): void {
    const { storages } = this;
    let storage = storages.get(key);

    if (storage === undefined) {
      storage = createStorage();
      storages.set(key, storage);
    }

    storage.get();
  }

  private dirtyStorageFor(key: K): void {
    const storage = this.storages.get(key);

    if (storage) {
      storage.set(null);
    }
  }

  constructor();
  constructor(entries: readonly (readonly [K, V])[] | null);
  constructor(iterable: Iterable<readonly [K, V]>);
  constructor(
    existing?:
      | readonly (readonly [K, V])[]
      | Iterable<readonly [K, V]>
      | null
      | undefined,
  ) {
    // TypeScript doesn't correctly resolve the overloads for calling the `Map`
    // constructor for the no-value constructor. This resolves that.
    this.vals = existing ? new Map(existing) : new Map();
  }

  // **** KEY GETTERS ****
  get(key: K): V | undefined {
    // entangle the storage for the key
    this.readStorageFor(key);

    return this.vals.get(key);
  }

  has(key: K): boolean {
    this.readStorageFor(key);

    return this.vals.has(key);
  }

  // **** ALL GETTERS ****
  entries(): IterableIterator<[K, V]> {
    this.collection.get();

    return this.vals.entries();
  }

  keys(): IterableIterator<K> {
    this.collection.get();

    return this.vals.keys();
  }

  values(): IterableIterator<V> {
    this.collection.get();

    return this.vals.values();
  }

  forEach(fn: (value: V, key: K, map: Map<K, V>) => void): void {
    this.collection.get();

    this.vals.forEach(fn);
  }

  get size(): number {
    this.collection.get();

    return this.vals.size;
  }

  [Symbol.iterator](): IterableIterator<[K, V]> {
    this.collection.get();

    return this.vals[Symbol.iterator]();
  }

  get [Symbol.toStringTag](): string {
    return this.vals[Symbol.toStringTag];
  }

  // **** KEY SETTERS ****
  set(key: K, value: V): this {
    this.dirtyStorageFor(key);
    this.collection.set(null);

    this.vals.set(key, value);

    return this;
  }

  delete(key: K): boolean {
    this.dirtyStorageFor(key);
    this.collection.set(null);

    return this.vals.delete(key);
  }

  // **** ALL SETTERS ****
  clear(): void {
    this.storages.forEach((s) => s.set(null));
    this.collection.set(null);

    this.vals.clear();
  }
}

// So instanceof works
Object.setPrototypeOf(SignalMap.prototype, Map.prototype);
