/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
import type { ReactiveElement } from '../reactive-element.js';
import { type Interface } from './base.js';
/**
 * Options for the {@linkcode queryAssignedNodes} decorator. Extends the options
 * that can be passed into [HTMLSlotElement.assignedNodes](https://developer.mozilla.org/en-US/docs/Web/API/HTMLSlotElement/assignedNodes).
 */
export interface QueryAssignedNodesOptions extends AssignedNodesOptions {
    /**
     * Name of the slot to query. Leave empty for the default slot.
     */
    slot?: string;
}
export type QueryAssignedNodesDecorator = {
    (proto: Interface<ReactiveElement>, name: PropertyKey, descriptor?: PropertyDescriptor): void | any;
    <C extends Interface<ReactiveElement>, V extends Array<Node>>(value: ClassAccessorDecoratorTarget<C, V>, context: ClassAccessorDecoratorContext<C, V>): ClassAccessorDecoratorResult<C, V>;
};
/**
 * A property decorator that converts a class property into a getter that
 * returns the `assignedNodes` of the given `slot`.
 *
 * Can be passed an optional {@linkcode QueryAssignedNodesOptions} object.
 *
 * Example usage:
 * ```ts
 * class MyElement {
 *   @queryAssignedNodes({slot: 'list', flatten: true})
 *   listItems!: Array<Node>;
 *
 *   render() {
 *     return html`
 *       <slot name="list"></slot>
 *     `;
 *   }
 * }
 * ```
 *
 * Note the type of this property should be annotated as `Array<Node>`. Use the
 * queryAssignedElements decorator to list only elements, and optionally filter
 * the element list using a CSS selector.
 *
 * @category Decorator
 */
export declare function queryAssignedNodes(options?: QueryAssignedNodesOptions): QueryAssignedNodesDecorator;
//# sourceMappingURL=query-assigned-nodes.d.ts.map