export declare class SignalWeakSet<T extends object = object> implements WeakSet<T> {
    private storages;
    private vals;
    private storageFor;
    private dirtyStorageFor;
    constructor(values?: readonly T[] | null);
    has(value: T): boolean;
    add(value: T): this;
    delete(value: T): boolean;
    get [Symbol.toStringTag](): string;
}
//# sourceMappingURL=weak-set.d.ts.map