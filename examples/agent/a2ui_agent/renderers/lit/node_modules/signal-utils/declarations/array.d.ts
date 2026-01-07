export interface SignalArray<T = unknown> extends Array<T> {
}
export declare class SignalArray<T = unknown> {
    #private;
    /**
     * Creates an array from an iterable object.
     * @param iterable An iterable object to convert to an array.
     */
    static from<T>(iterable: Iterable<T> | ArrayLike<T>): SignalArray<T>;
    /**
     * Creates an array from an iterable object.
     * @param iterable An iterable object to convert to an array.
     * @param mapfn A mapping function to call on every element of the array.
     * @param thisArg Value of 'this' used to invoke the mapfn.
     */
    static from<T, U>(iterable: Iterable<T> | ArrayLike<T>, mapfn: (v: T, k: number) => U, thisArg?: unknown): SignalArray<U>;
    static of<T>(...arr: T[]): SignalArray<T>;
    constructor(arr?: T[]);
}
export declare function signalArray<Item>(x?: Item[]): SignalArray<Item>;
//# sourceMappingURL=array.d.ts.map