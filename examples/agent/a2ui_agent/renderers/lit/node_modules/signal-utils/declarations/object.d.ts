/**
 * Implementation based of tracked-built-ins' TrackedObject
 * https://github.com/tracked-tools/tracked-built-ins/blob/master/addon/src/-private/object.js
 */
export declare class SignalObjectImpl {
    #private;
    static fromEntries<T = unknown>(entries: Iterable<readonly [PropertyKey, T]>): T;
    constructor(obj?: {});
}
interface SignalObject {
    fromEntries<T = unknown>(entries: Iterable<readonly [PropertyKey, T]>): {
        [k: string]: T;
    };
    new <T extends Record<PropertyKey, unknown> = Record<PropertyKey, unknown>>(obj?: T): T;
}
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
export declare const SignalObject: SignalObject;
export declare function signalObject<T extends Record<PropertyKey, unknown>>(obj?: T | undefined): T;
export {};
//# sourceMappingURL=object.d.ts.map