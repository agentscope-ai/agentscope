/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
import type { Constructor } from './base.js';
/**
 * Allow for custom element classes with private constructors
 */
type CustomElementClass = Omit<typeof HTMLElement, 'new'>;
export type CustomElementDecorator = {
    (cls: CustomElementClass): void;
    (target: CustomElementClass, context: ClassDecoratorContext<Constructor<HTMLElement>>): void;
};
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
export declare const customElement: (tagName: string) => CustomElementDecorator;
export {};
//# sourceMappingURL=custom-element.d.ts.map