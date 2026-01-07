/**
 * @license
 * Copyright Google LLC All Rights Reserved.
 *
 * Use of this source code is governed by an MIT-style license that can be
 * found in the LICENSE file at https://angular.io/license
 */

/**
 * An interface representing a comparison strategy to determine if two values are equal.
 *
 * @template T The type of the values to be compared.
 */
export interface ValueEqualityComparer<T> {
  equal(a: T, b: T): boolean;
}

/**
 * The default equality function used for `signal` and `computed`, which uses referential equality.
 */
export function defaultEquals<T>(a: T, b: T) {
  return Object.is(a, b);
}
