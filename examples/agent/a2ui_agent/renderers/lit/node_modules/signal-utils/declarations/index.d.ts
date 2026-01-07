/**
 * Usage:
 * ```js
 * export class Counter {
 *   @signal accessor #value = 0;
 *
 *   get doubled() {
 *     return this.#value * 2;
 *   }
 *
 *   increment() {
 *     this.#value++;
 *   }
 *
 *   decrement() {
 *     if (this.#value > 0) {
 *       this.#value--;
 *     }
 *   }
 * }
 * ```
 */
export declare function signal<Value = any>(...args: Parameters<typeof stateDecorator<Value>>): ReturnType<typeof stateDecorator<Value>>;
/**
 * Usage:
 * ```js
 * import { signal } from 'signal-utils';
 *
 * export class Counter {
 *   @signal accessor #value = 0;
 *
 *   @signal
 *   get expensive() {
 *     // some expensive operation
 *     return this.#value * 2;
 *   }
 *
 *   increment() {
 *     this.#value++;
 *   }
 * }
 * ```
 */
export declare function signal<Value = any>(...args: Parameters<typeof computedDecorator<Value>>): ReturnType<typeof computedDecorator<Value>>;
declare function stateDecorator<Value = any>(target: ClassAccessorDecoratorTarget<unknown, Value>, context: ClassAccessorDecoratorContext): ClassAccessorDecoratorResult<unknown, Value>;
declare function computedDecorator<Value = any>(target: () => Value, context: ClassGetterDecoratorContext): () => Value;
export {};
//# sourceMappingURL=index.d.ts.map