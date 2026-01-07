/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
import type { ReactiveElement } from '../reactive-element.js';
import { type Interface } from './base.js';
export type QueryDecorator = {
    (proto: Interface<ReactiveElement>, name: PropertyKey, descriptor?: PropertyDescriptor): void | any;
    <C extends Interface<ReactiveElement>, V extends Element | null>(value: ClassAccessorDecoratorTarget<C, V>, context: ClassAccessorDecoratorContext<C, V>): ClassAccessorDecoratorResult<C, V>;
};
/**
 * A property decorator that converts a class property into a getter that
 * executes a querySelector on the element's renderRoot.
 *
 * @param selector A DOMString containing one or more selectors to match.
 * @param cache An optional boolean which when true performs the DOM query only
 *     once and caches the result.
 *
 * See: https://developer.mozilla.org/en-US/docs/Web/API/Document/querySelector
 *
 * ```ts
 * class MyElement {
 *   @query('#first')
 *   first: HTMLDivElement;
 *
 *   render() {
 *     return html`
 *       <div id="first"></div>
 *       <div id="second"></div>
 *     `;
 *   }
 * }
 * ```
 * @category Decorator
 */
export declare function query(selector: string, cache?: boolean): QueryDecorator;
//# sourceMappingURL=query.d.ts.map