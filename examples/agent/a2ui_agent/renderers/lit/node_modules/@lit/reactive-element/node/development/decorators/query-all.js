import { desc } from './base.js';

/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
// Shared fragment used to generate empty NodeLists when a render root is
// undefined
let fragment;
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
function queryAll(selector) {
    return ((obj, name) => {
        return desc(obj, name, {
            get() {
                const container = this.renderRoot ?? (fragment ??= document.createDocumentFragment());
                return container.querySelectorAll(selector);
            },
        });
    });
}

export { queryAll };
//# sourceMappingURL=query-all.js.map
