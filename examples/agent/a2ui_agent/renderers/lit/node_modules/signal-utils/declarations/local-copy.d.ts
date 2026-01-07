/**
 * Forks remote state for local modification
 *
 * ```js
 * import { Signal } from 'signal-polyfill';
 * import { localCopy } from 'signal-utils';
 *
 * const remote = new Signal.State(0);
 *
 * const local = localCopy(() => remote.get());
 * ```
 */
export declare function localCopy<Value = any>(fn: () => Value): {
    get(): Value;
    set(v: Value): void;
};
/**
 * Forks remote state for local modification
 *
 * ```js
 * import { localCopy } from 'signal-utils';
 *
 * class Demo {
 *    @localCopy('remote.value') accessor localValue;
 * }
 * ```
 */
export declare function localCopy<Value = any, This extends WeakKey = WeakKey>(memo: string, initializer?: Value | (() => Value)): (_target: ClassAccessorDecoratorTarget<This, Value>, _context: ClassAccessorDecoratorContext<This, Value>) => ClassAccessorDecoratorResult<This, Value>;
//# sourceMappingURL=local-copy.d.ts.map