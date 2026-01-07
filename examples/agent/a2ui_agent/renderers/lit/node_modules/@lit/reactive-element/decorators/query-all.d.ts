/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
import type { ReactiveElement } from '../reactive-element.js';
import { type Interface } from './base.js';
export type QueryAllDecorator = {
    (proto: Interface<ReactiveElement>, name: PropertyKey, descriptor?: PropertyDescriptor): void | any;
    <C extends Interface<ReactiveElement>, V extends NodeList>(value: ClassAccessorDecoratorTarget<C, V>, context: ClassAccessorDecoratorContext<C, V>): ClassAccessorDecoratorResult<C, V>;
};
/**
 * A property decorator that converts a class property into a getter
 * that executes a querySelectorAll on the element's renderRoot.
 *
 * @param selector A DOMString containing one or more selectors to match.
 *
 * See:
 * https://developer.mozilla.org/en-US/docs/Web/API/Document/querySelectorAll
 *
 * ```ts
 * class MyElement {
 *   @queryAll('div')
 *   divs: NodeListOf<HTMLDivElement>;
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
export declare function queryAll(selector: string): QueryAllDecorator;
//# sourceMappingURL=query-all.d.ts.map