/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
import type { ReactiveElement } from '../reactive-element.js';
import type { Interface } from './base.js';
export type EventOptionsDecorator = {
    (proto: Interface<ReactiveElement>, name: PropertyKey): void | any;
    <C, V extends (this: C, ...args: any) => any>(value: V, _context: ClassMethodDecoratorContext<C, V>): void;
};
/**
 * Adds event listener options to a method used as an event listener in a
 * lit-html template.
 *
 * @param options An object that specifies event listener options as accepted by
 * `EventTarget#addEventListener` and `EventTarget#removeEventListener`.
 *
 * Current browsers support the `capture`, `passive`, and `once` options. See:
 * https://developer.mozilla.org/en-US/docs/Web/API/EventTarget/addEventListener#Parameters
 *
 * ```ts
 * class MyElement {
 *   clicked = false;
 *
 *   render() {
 *     return html`
 *       <div @click=${this._onClick}>
 *         <button></button>
 *       </div>
 *     `;
 *   }
 *
 *   @eventOptions({capture: true})
 *   _onClick(e) {
 *     this.clicked = true;
 *   }
 * }
 * ```
 * @category Decorator
 */
export declare function eventOptions(options: AddEventListenerOptions): EventOptionsDecorator;
//# sourceMappingURL=event-options.d.ts.map