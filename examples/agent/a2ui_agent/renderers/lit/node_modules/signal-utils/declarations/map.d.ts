export declare class SignalMap<K = unknown, V = unknown> implements Map<K, V> {
    private collection;
    private storages;
    private vals;
    private readStorageFor;
    private dirtyStorageFor;
    constructor();
    constructor(entries: readonly (readonly [K, V])[] | null);
    constructor(iterable: Iterable<readonly [K, V]>);
    get(key: K): V | undefined;
    has(key: K): boolean;
    entries(): IterableIterator<[K, V]>;
    keys(): IterableIterator<K>;
    values(): IterableIterator<V>;
    forEach(fn: (value: V, key: K, map: Map<K, V>) => void): void;
    get size(): number;
    [Symbol.iterator](): IterableIterator<[K, V]>;
    get [Symbol.toStringTag](): string;
    set(key: K, value: V): this;
    delete(key: K): boolean;
    clear(): void;
}
//# sourceMappingURL=map.d.ts.map