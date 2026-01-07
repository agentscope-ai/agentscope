/**
 * Public API of the return value of the [[map]] utility.
 */
export interface MappedArray<Elements extends readonly unknown[], MappedTo> {
    /**
     * Array-index access to specific mapped data.
     *
     * If the map function hasn't run yet on the source data, it will be run, and cached
     * for subsequent accesses.
     *
     * ```js
     *  class Foo {
     *    myMappedData = map({
     *      data: () => [1, 2, 3],
     *      map: (num) => `hi, ${num}!`
     *    });
     *
     *    get first() {
     *      return this.myMappedData[0];
     *    }
     *  }
     * ```
     */
    [index: number]: MappedTo;
    /**
     * Evaluate and return an array of all mapped items.
     *
     * This is useful when you need to do other Array-like operations
     * on the mapped data, such as filter, or find.
     *
     * ```js
     *  class Foo {
     *    myMappedData = map({
     *      data: () => [1, 2, 3],
     *      map: (num) => `hi, ${num}!`
     *    });
     *
     *    get everything() {
     *      return this.myMappedData.values();
     *    }
     *  }
     * ```
     */
    values: () => {
        [K in keyof Elements]: MappedTo;
    };
    /**
     * Without evaluating the map function on each element,
     * provide the total number of elements.
     *
     * ```js
     *  class Foo {
     *    myMappedData = map({
     *      data: () => [1, 2, 3],
     *      map: (num) => `hi, ${num}!`
     *    });
     *
     *    get numItems() {
     *      return this.myMappedData.length;
     *    }
     *  }
     * ```
     */
    get length(): number;
    /**
     * Iterate over the mapped array, lazily invoking the passed map function
     * that was passed to [[map]].
     *
     * This will always return previously mapped records without re-evaluating
     * the map function, so the default `{{#each}}` behavior in ember will
     * be optimized on "object-identity". e.g.:
     *
     * ```js
     *  // ...
     *  myMappedData = map({
     *    data: () => [1, 2, 3],
     *    map: (num) => `hi, ${num}!`
     *  });
     *  // ...
     * ```
     * ```hbs
     *  {{#each this.myMappedData as |datum|}}
     *     loop body only invoked for changed entries
     *     {{datum}}
     *  {{/each}}
     * ```
     *
     * Iteration in javascript is also provided by this iterator
     * ```js
     *  class Foo {
     *    myMappedData = map(this, {
     *      data: () => [1, 2, 3],
     *      map: (num) => `hi, ${num}!`
     *    });
     *
     *    get mapAgain() {
     *      let results = [];
     *
     *      for (let datum of this.myMappedData) {
     *        results.push(datum);
     *      }
     *
     *      return datum;
     *    }
     *  }
     * ```
     */
    [Symbol.iterator](): Iterator<MappedTo>;
}
/**
 * Reactivily apply a `map` function to each element in an array,
 * persisting map-results for each object, based on identity.
 *
 * This is useful when you have a large collection of items that
 * need to be transformed into a different shape (adding/removing/modifying data/properties)
 * and you want the transform to be efficient when iterating over that data.
 *
 * A common use case where this `map` utility provides benefits over is
 * ```js
 * class MyClass {\
 *   @signal
 *   get wrappedRecords() {
 *     return this.records.map(record => new SomeWrapper(record));
 *   }
 * }
 * ```
 *
 * Even though the above is a cached computed (via `@signal`), if any signal data accessed during the evaluation of `wrappedRecords`
 * changes, the entire array.map will re-run, often doing duplicate work for every unchanged item in the array.
 *
 * @return {MappedArray} an object that behaves like an array. This shouldn't be modified directly. Instead, you can freely modify the data returned by the `data` function, which should be auto-tracked in order to benefit from this abstraction.
 *
 * @example
 *
 * ```js
 *  import { arrayMap } from 'signal-utils/array-map';
 *
 *  class MyClass {
 *    wrappedRecords = map({
 *      data: () => this.records,
 *      map: (record) => new SomeWrapper(record),
 *    }),
 *  }
 * ```
 */
export declare function arrayMap<Elements extends readonly unknown[], MapTo = unknown>(options: {
    /**
     * Array of non-primitives to map over
     *
     * This can be class instances, plain objects, or anything supported by WeakMap's key
     */
    data: () => Elements;
    /**
     * Transform each element from `data`, reactively equivalent to `Array.map`.
     *
     * This function will be called only when needed / on-demand / lazily.
     * - if iterating over part of the data, map will only be called for the elements observed
     * - if not iterating, map will only be called for the elements observed.
     */
    map: (element: Elements[0]) => MapTo;
}): MappedArray<Elements, MapTo>;
declare const AT: unique symbol;
/**
 * @private
 */
export declare class TrackedArrayMap<Element = unknown, MappedTo = unknown> implements MappedArray<Element[], MappedTo> {
    [index: number]: MappedTo;
    private _mapCache;
    private _dataFn;
    private _mapFn;
    constructor(data: () => readonly Element[], map: (element: Element) => MappedTo);
    get _records(): (Element & object)[];
    values: () => MappedTo[];
    get length(): number;
    [Symbol.iterator](): Iterator<MappedTo>;
    /**
     * @private
     *
     * don't conflict with
     *   https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array/at
     */
    [AT]: (i: number) => MappedTo;
}
export {};
//# sourceMappingURL=array-map.d.ts.map