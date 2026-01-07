/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
import { type PropertyDeclaration, type ReactiveElement } from '../reactive-element.js';
import type { Interface } from './base.js';
export type PropertyDecorator = {
    <C extends Interface<ReactiveElement>, V>(target: ClassAccessorDecoratorTarget<C, V>, context: ClassAccessorDecoratorContext<C, V>): ClassAccessorDecoratorResult<C, V>;
    <C extends Interface<ReactiveElement>, V>(target: (value: V) => void, context: ClassSetterDecoratorContext<C, V>): (this: C, value: V) => void;
    (protoOrDescriptor: Object, name: PropertyKey, descriptor?: PropertyDescriptor): any;
};
type StandardPropertyContext<C, V> = (ClassAccessorDecoratorContext<C, V> | ClassSetterDecoratorContext<C, V>) & {
    metadata: object;
};
/**
 * Wraps a class accessor or setter so that `requestUpdate()` is called with the
 * property name and old value when the accessor is set.
 */
export declare const standardProperty: <C extends Interface<ReactiveElement>, V>(options: PropertyDeclaration | undefined, target: ClassAccessorDecoratorTarget<C, V> | ((value: V) => void), context: StandardPropertyContext<C, V>) => ClassAccessorDecoratorResult<C, V> | ((this: C, value: V) => void);
/**
 * A class field or accessor decorator which creates a reactive property that
 * reflects a corresponding attribute value. When a decorated property is set
 * the element will update and render. A {@linkcode PropertyDeclaration} may
 * optionally be supplied to configure property features.
 *
 * This decorator should only be used for public fields. As public fields,
 * properties should be considered as primarily settable by element users,
 * either via attribute or the property itself.
 *
 * Generally, properties that are changed by the element should be private or
 * protected fields and should use the {@linkcode state} decorator.
 *
 * However, sometimes element code does need to set a public property. This
 * should typically only be done in response to user interaction, and an event
 * should be fired informing the user; for example, a checkbox sets its
 * `checked` property when clicked and fires a `changed` event. Mutating public
 * properties should typically not be done for non-primitive (object or array)
 * properties. In other cases when an element needs to manage state, a private
 * property decorated via the {@linkcode state} decorator should be used. When
 * needed, state properties can be initialized via public properties to
 * facilitate complex interactions.
 *
 * ```ts
 * class MyElement {
 *   @property({ type: Boolean })
 *   clicked = false;
 * }
 * ```
 * @category Decorator
 * @ExportDecoratedItems
 */
export declare function property(options?: PropertyDeclaration): PropertyDecorator;
export {};
//# sourceMappingURL=property.d.ts.map