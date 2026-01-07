type TrackedProxy<T> = T;
type Key = number | string | symbol;
export declare function initStorage(context: any, key: Key, initialValue?: any): null;
export declare function hasStorage(context: any, key: Key): boolean;
export declare function readStorage(context: any, key: Key): null;
export declare function updateStorage(context: any, key: Key, value?: any): void;
export declare function readCollection(context: any): null;
export declare function dirtyCollection(context: any): void;
/**
 * Deeply track an Array, and all nested objects/arrays within.
 *
 * If an element / value is ever a non-object or non-array, deep-tracking will exit
 *
 */
export declare function deepSignal<T>(arr: T[]): TrackedProxy<T[]>;
/**
 * Deeply track an Object, and all nested objects/arrays within.
 *
 * If an element / value is ever a non-object or non-array, deep-tracking will exit
 *
 */
export declare function deepSignal<T extends Record<string, unknown>>(obj: T): TrackedProxy<T>;
/**
 * Deeply track an Object or Array, and all nested objects/arrays within.
 *
 * If an element / value is ever a non-object or non-array, deep-tracking will exit
 *
 */
export declare function deepSignal(...args: any): any;
export declare function deep<T>(obj: T | null | undefined): T;
export {};
//# sourceMappingURL=deep.d.ts.map