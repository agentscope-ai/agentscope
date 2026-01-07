/**
 * @license
 * Copyright 2019 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
import { ElementInternalsShim } from './lib/element-internals.js';
import { EventTargetShim, EventShim, CustomEventShim, } from './lib/events.js';
export { ariaMixinAttributes, ElementInternals, HYDRATE_INTERNALS_ATTR_PREFIX, } from './lib/element-internals.js';
export { CustomEvent, Event, EventTarget } from './lib/events.js';
// In an empty Node.js vm, we need to patch the global context.
// TODO: Remove these globalThis assignments when we remove support
// for vm modules (--experimental-vm-modules).
globalThis.Event ??= EventShim;
globalThis.CustomEvent ??= CustomEventShim;
const attributes = new WeakMap();
const attributesForElement = (element) => {
    let attrs = attributes.get(element);
    if (attrs === undefined) {
        attributes.set(element, (attrs = new Map()));
    }
    return attrs;
};
// The typings around the exports below are a little funky:
//
// 1. We want the `name` of the shim classes to match the real ones at runtime,
//    hence e.g. `class Element`.
// 2. We can't shadow the global types with a simple class declaration, because
//    then we can't reference the global types for casting, hence e.g.
//    `const ElementShim = class Element`.
// 3. We want to export the classes typed as the real ones, hence e.g.
//    `const ElementShimWithRealType = ElementShim as object as typeof Element;`.
// 4. We want the exported names to match the real ones, hence e.g.
//    `export {ElementShimWithRealType as Element}`.
const ElementShim = class Element extends EventTargetShim {
    constructor() {
        super(...arguments);
        this.__shadowRootMode = null;
        this.__shadowRoot = null;
        this.__internals = null;
    }
    get attributes() {
        return Array.from(attributesForElement(this)).map(([name, value]) => ({
            name,
            value,
        }));
    }
    get shadowRoot() {
        if (this.__shadowRootMode === 'closed') {
            return null;
        }
        return this.__shadowRoot;
    }
    get localName() {
        return this.constructor.__localName;
    }
    get tagName() {
        return this.localName?.toUpperCase();
    }
    setAttribute(name, value) {
        // Emulate browser behavior that silently casts all values to string. E.g.
        // `42` becomes `"42"` and `{}` becomes `"[object Object]""`.
        attributesForElement(this).set(name, String(value));
    }
    removeAttribute(name) {
        attributesForElement(this).delete(name);
    }
    toggleAttribute(name, force) {
        // Steps reference https://dom.spec.whatwg.org/#dom-element-toggleattribute
        if (this.hasAttribute(name)) {
            // Step 5
            if (force === undefined || !force) {
                this.removeAttribute(name);
                return false;
            }
        }
        else {
            // Step 4
            if (force === undefined || force) {
                // Step 4.1
                this.setAttribute(name, '');
                return true;
            }
            else {
                // Step 4.2
                return false;
            }
        }
        // Step 6
        return true;
    }
    hasAttribute(name) {
        return attributesForElement(this).has(name);
    }
    attachShadow(init) {
        const shadowRoot = { host: this };
        this.__shadowRootMode = init.mode;
        if (init && init.mode === 'open') {
            this.__shadowRoot = shadowRoot;
        }
        return shadowRoot;
    }
    attachInternals() {
        if (this.__internals !== null) {
            throw new Error(`Failed to execute 'attachInternals' on 'HTMLElement': ` +
                `ElementInternals for the specified element was already attached.`);
        }
        const internals = new ElementInternalsShim(this);
        this.__internals = internals;
        return internals;
    }
    getAttribute(name) {
        const value = attributesForElement(this).get(name);
        return value ?? null;
    }
};
const ElementShimWithRealType = ElementShim;
export { ElementShimWithRealType as Element };
const HTMLElementShim = class HTMLElement extends ElementShim {
};
const HTMLElementShimWithRealType = HTMLElementShim;
export { HTMLElementShimWithRealType as HTMLElement };
// For convenience, we provide a global instance of a HTMLElement as an event
// target. This facilitates registering global event handlers
// (e.g. for @lit/context ContextProvider).
// We use this in in the SSR render function.
// Note, this is a bespoke element and not simply `document` or `window` since
// user code relies on these being undefined in the server environment.
globalThis.litServerRoot ??= Object.defineProperty(new HTMLElementShimWithRealType(), 'localName', {
    // Patch localName (and tagName) to return a unique name.
    get() {
        return 'lit-server-root';
    },
});
function promiseWithResolvers() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve: resolve, reject: reject };
}
class CustomElementRegistry {
    constructor() {
        this.__definitions = new Map();
        this.__reverseDefinitions = new Map();
        this.__pendingWhenDefineds = new Map();
    }
    define(name, ctor) {
        if (this.__definitions.has(name)) {
            if (process.env.NODE_ENV === 'development') {
                console.warn(`'CustomElementRegistry' already has "${name}" defined. ` +
                    `This may have been caused by live reload or hot module ` +
                    `replacement in which case it can be safely ignored.\n` +
                    `Make sure to test your application with a production build as ` +
                    `repeat registrations will throw in production.`);
            }
            else {
                throw new Error(`Failed to execute 'define' on 'CustomElementRegistry': ` +
                    `the name "${name}" has already been used with this registry`);
            }
        }
        if (this.__reverseDefinitions.has(ctor)) {
            throw new Error(`Failed to execute 'define' on 'CustomElementRegistry': ` +
                `the constructor has already been used with this registry for the ` +
                `tag name ${this.__reverseDefinitions.get(ctor)}`);
        }
        // Provide tagName and localName for the component.
        ctor.__localName = name;
        this.__definitions.set(name, {
            ctor,
            // Note it's important we read `observedAttributes` in case it is a getter
            // with side-effects, as is the case in Lit, where it triggers class
            // finalization.
            //
            // TODO(aomarks) To be spec compliant, we should also capture the
            // registration-time lifecycle methods like `connectedCallback`. For them
            // to be actually accessible to e.g. the Lit SSR element renderer, though,
            // we'd need to introduce a new API for accessing them (since `get` only
            // returns the constructor).
            observedAttributes: ctor.observedAttributes ?? [],
        });
        this.__reverseDefinitions.set(ctor, name);
        this.__pendingWhenDefineds.get(name)?.resolve(ctor);
        this.__pendingWhenDefineds.delete(name);
    }
    get(name) {
        const definition = this.__definitions.get(name);
        return definition?.ctor;
    }
    getName(ctor) {
        return this.__reverseDefinitions.get(ctor) ?? null;
    }
    upgrade(_element) {
        // In SSR this doesn't make a lot of sense, so we do nothing.
        throw new Error(`customElements.upgrade is not currently supported in SSR. ` +
            `Please file a bug if you need it.`);
    }
    async whenDefined(name) {
        const definition = this.__definitions.get(name);
        if (definition) {
            return definition.ctor;
        }
        let withResolvers = this.__pendingWhenDefineds.get(name);
        if (!withResolvers) {
            withResolvers = promiseWithResolvers();
            this.__pendingWhenDefineds.set(name, withResolvers);
        }
        return withResolvers.promise;
    }
}
const CustomElementRegistryShimWithRealType = CustomElementRegistry;
export { CustomElementRegistryShimWithRealType as CustomElementRegistry };
export const customElements = new CustomElementRegistryShimWithRealType();
//# sourceMappingURL=index.js.map