import { desc } from './base.js';

/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
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
function queryAssignedNodes(options) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return ((obj, name) => {
        const { slot } = options ?? {};
        const slotSelector = `slot${slot ? `[name=${slot}]` : ':not([name])'}`;
        return desc(obj, name, {
            get() {
                const slotEl = this.renderRoot?.querySelector(slotSelector);
                return (slotEl?.assignedNodes(options) ?? []);
            },
        });
    });
}

export { queryAssignedNodes };
//# sourceMappingURL=query-assigned-nodes.js.map
