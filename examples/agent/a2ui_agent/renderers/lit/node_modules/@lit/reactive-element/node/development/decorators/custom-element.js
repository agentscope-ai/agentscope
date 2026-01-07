/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
/**
 * Class decorator factory that defines the decorated class as a custom element.
 *
 * ```js
 * @customElement('my-element')
 * class MyElement extends LitElement {
 *   render() {
 *     return html``;
 *   }
 * }
 * ```
 * @category Decorator
 * @param tagName The tag name of the custom element to define.
 */
const customElement = (tagName) => (classOrTarget, context) => {
    if (context !== undefined) {
        context.addInitializer(() => {
            customElements.define(tagName, classOrTarget);
        });
    }
    else {
        customElements.define(tagName, classOrTarget);
    }
};

export { customElement };
//# sourceMappingURL=custom-element.js.map
