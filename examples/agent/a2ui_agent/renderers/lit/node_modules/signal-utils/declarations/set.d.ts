export declare class SignalSet<T = unknown> implements Set<T> {
    private collection;
    private storages;
    private vals;
    private storageFor;
    private dirtyStorageFor;
    constructor();
    constructor(values: readonly T[] | null);
    constructor(iterable: Iterable<T>);
    has(value: T): boolean;
    entries(): IterableIterator<[T, T]>;
    keys(): IterableIterator<T>;
    values(): IterableIterator<T>;
    forEach(fn: (value1: T, value2: T, set: Set<T>) => void): void;
    get size(): number;
    [Symbol.iterator](): IterableIterator<T>;
    get [Symbol.toStringTag](): string;
    add(value: T): this;
    delete(value: T): boolean;
    clear(): void;
}
//# sourceMappingURL=set.d.ts.map