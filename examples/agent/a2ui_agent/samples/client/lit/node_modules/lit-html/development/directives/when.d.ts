/**
 * @license
 * Copyright 2021 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
type Falsy = null | undefined | false | 0 | -0 | 0n | '';
/**
 * When `condition` is true, returns the result of calling `trueCase()`, else
 * returns the result of calling `falseCase()` if `falseCase` is defined.
 *
 * This is a convenience wrapper around a ternary expression that makes it a
 * little nicer to write an inline conditional without an else.
 *
 * @example
 *
 * ```ts
 * render() {
 *   return html`
 *     ${when(this.user, () => html`User: ${this.user.username}`, () => html`Sign In...`)}
 *   `;
 * }
 * ```
 */
export declare function when<C extends Falsy, T, F = undefined>(condition: C, trueCase: (c: C) => T, falseCase?: (c: C) => F): F;
export declare function when<C, T, F>(condition: C extends Falsy ? never : C, trueCase: (c: C) => T, falseCase?: (c: C) => F): T;
export declare function when<C, T, F = undefined>(condition: C, trueCase: (c: Exclude<C, Falsy>) => T, falseCase?: (c: Extract<C, Falsy>) => F): C extends Falsy ? F : T;
export {};
//# sourceMappingURL=when.d.ts.map