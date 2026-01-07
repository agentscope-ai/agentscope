export declare class SignalWeakMap<K extends object = object, V = unknown> implements WeakMap<K, V> {
    private storages;
    private vals;
    private readStorageFor;
    private dirtyStorageFor;
    constructor();
    constructor(iterable: Iterable<readonly [K, V]>);
    constructor(entries: readonly [K, V][] | null);
    get(key: K): V | undefined;
    has(key: K): boolean;
    set(key: K, value: V): this;
    delete(key: K): boolean;
    get [Symbol.toStringTag](): string;
}
//# sourceMappingURL=weak-map.d.ts.map