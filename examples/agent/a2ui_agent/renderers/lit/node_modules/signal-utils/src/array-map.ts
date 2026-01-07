import { signal } from "./index.ts";

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
  values: () => { [K in keyof Elements]: MappedTo };

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
export function arrayMap<
  Elements extends readonly unknown[],
  MapTo = unknown,
>(options: {
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
}): MappedArray<Elements, MapTo> {
  let { data, map } = options;

  return new TrackedArrayMap(data, map) as MappedArray<Elements, MapTo>;
}

const AT = Symbol("__AT__");

/**
 * @private
 */
export class TrackedArrayMap<Element = unknown, MappedTo = unknown>
  implements MappedArray<Element[], MappedTo>
{
  // Tells TS that we can array-index-access
  [index: number]: MappedTo;

  // these can't be real private fields
  // until @cached is a real decorator
  private _mapCache = new WeakMap<Element & object, MappedTo>();
  private _dataFn: () => readonly Element[];
  private _mapFn: (element: Element) => MappedTo;

  constructor(
    data: () => readonly Element[],
    map: (element: Element) => MappedTo,
  ) {
    this._dataFn = data;
    this._mapFn = map;

    // eslint-disable-next-line @typescript-eslint/no-this-alias
    const self = this;

    /**
     * This is what allows square-bracket index-access to work.
     *
     * Unfortunately this means the returned value is
     * Proxy -> Proxy -> wrapper object -> *then* the class instance
     *
     * Maybe JS has a way to implement array-index access, but I don't know how
     */
    return new Proxy(this, {
      get(_target, property) {
        if (typeof property === "string") {
          let parsed = parseInt(property, 10);

          if (!isNaN(parsed)) {
            return self[AT](parsed);
          }
        }

        return self[property as keyof MappedArray<Element[], MappedTo>];
      },
      // Is there a way to do this without lying to TypeScript?
    }) as TrackedArrayMap<Element, MappedTo>;
  }

  @signal
  get _records(): (Element & object)[] {
    let data = this._dataFn();

    if (!data.every((datum) => typeof datum === "object")) {
      throw new Error(
        `Every entry in the data passed to \`map\` must be an object.`,
      );
    }

    return data as Array<Element & object>;
  }

  values = () => [...this];

  get length() {
    return this._records.length;
  }

  [Symbol.iterator](): Iterator<MappedTo> {
    let i = 0;

    return {
      next: () => {
        if (i >= this.length) {
          return { done: true, value: null };
        }

        let value = this[AT](i);

        i++;

        return {
          value,
          done: false,
        };
      },
    };
  }

  /**
   * @private
   *
   * don't conflict with
   *   https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array/at
   */
  [AT] = (i: number) => {
    let record = this._records[i];

    if (!record) {
      throw new Error(
        `Expected record to exist at index ${i}, but it did not. ` +
          `The array item is expected to exist, because the map utility resource lazily iterates along the indices of the original array passed as data. ` +
          `This error could happen if the data array passed to map has been mutated while iterating. ` +
          `To resolve this error, do not mutate arrays while iteration occurs.`,
      );
    }

    let value = this._mapCache.get(record);

    if (!value) {
      value = this._mapFn(record);
      this._mapCache.set(record, value);
    }

    return value;
  };
}
