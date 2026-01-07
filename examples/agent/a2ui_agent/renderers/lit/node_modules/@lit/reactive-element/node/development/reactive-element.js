import { customElements, HTMLElement } from '@lit-labs/ssr-dom-shim';
import { getCompatibleStyle, adoptStyles } from './css-tag.js';
export { CSSResult, css, supportsAdoptingStyleSheets, unsafeCSS } from './css-tag.js';

// TODO (justinfagnani): Add `hasOwn` here when we ship ES2022
const { is, defineProperty, getOwnPropertyDescriptor, getOwnPropertyNames, getOwnPropertySymbols, getPrototypeOf, } = Object;
// Lets a minifier replace globalThis references with a minified name
const global = globalThis;
{
    global.customElements ??= customElements;
}
let issueWarning;
const trustedTypes = global
    .trustedTypes;
// Temporary workaround for https://crbug.com/993268
// Currently, any attribute starting with "on" is considered to be a
// TrustedScript source. Such boolean attributes must be set to the equivalent
// trusted emptyScript value.
const emptyStringForBooleanAttribute = trustedTypes
    ? trustedTypes.emptyScript
    : '';
const polyfillSupport = global.reactiveElementPolyfillSupportDevMode
    ;
{
    // Ensure warnings are issued only 1x, even if multiple versions of Lit
    // are loaded.
    global.litIssuedWarnings ??= new Set();
    /**
     * Issue a warning if we haven't already, based either on `code` or `warning`.
     * Warnings are disabled automatically only by `warning`; disabling via `code`
     * can be done by users.
     */
    issueWarning = (code, warning) => {
        warning += ` See https://lit.dev/msg/${code} for more information.`;
        if (!global.litIssuedWarnings.has(warning) &&
            !global.litIssuedWarnings.has(code)) {
            console.warn(warning);
            global.litIssuedWarnings.add(warning);
        }
    };
    queueMicrotask(() => {
        issueWarning('dev-mode', `Lit is in dev mode. Not recommended for production!`);
        // Issue polyfill support warning.
        if (global.ShadyDOM?.inUse && polyfillSupport === undefined) {
            issueWarning('polyfill-support-missing', `Shadow DOM is being polyfilled via \`ShadyDOM\` but ` +
                `the \`polyfill-support\` module has not been loaded.`);
        }
    });
}
/**
 * Useful for visualizing and logging insights into what the Lit template system is doing.
 *
 * Compiled out of prod mode builds.
 */
const debugLogEvent = (event) => {
        const shouldEmit = global
            .emitLitDebugLogEvents;
        if (!shouldEmit) {
            return;
        }
        global.dispatchEvent(new CustomEvent('lit-debug', {
            detail: event,
        }));
    }
    ;
/*
 * When using Closure Compiler, JSCompiler_renameProperty(property, object) is
 * replaced at compile time by the munged name for object[property]. We cannot
 * alias this function, so we have to use a small shim that has the same
 * behavior when not compiling.
 */
/*@__INLINE__*/
const JSCompiler_renameProperty = (prop, _obj) => prop;
const defaultConverter = {
    toAttribute(value, type) {
        switch (type) {
            case Boolean:
                value = value ? emptyStringForBooleanAttribute : null;
                break;
            case Object:
            case Array:
                // if the value is `null` or `undefined` pass this through
                // to allow removing/no change behavior.
                value = value == null ? value : JSON.stringify(value);
                break;
        }
        return value;
    },
    fromAttribute(value, type) {
        let fromValue = value;
        switch (type) {
            case Boolean:
                fromValue = value !== null;
                break;
            case Number:
                fromValue = value === null ? null : Number(value);
                break;
            case Object:
            case Array:
                // Do *not* generate exception when invalid JSON is set as elements
                // don't normally complain on being mis-configured.
                // TODO(sorvell): Do generate exception in *dev mode*.
                try {
                    // Assert to adhere to Bazel's "must type assert JSON parse" rule.
                    fromValue = JSON.parse(value);
                }
                catch (e) {
                    fromValue = null;
                }
                break;
        }
        return fromValue;
    },
};
/**
 * Change function that returns true if `value` is different from `oldValue`.
 * This method is used as the default for a property's `hasChanged` function.
 */
const notEqual = (value, old) => !is(value, old);
const defaultPropertyDeclaration = {
    attribute: true,
    type: String,
    converter: defaultConverter,
    reflect: false,
    useDefault: false,
    hasChanged: notEqual,
};
// Ensure metadata is enabled. TypeScript does not polyfill
// Symbol.metadata, so we must ensure that it exists.
Symbol.metadata ??= Symbol('metadata');
// Map from a class's metadata object to property options
// Note that we must use nullish-coalescing assignment so that we only use one
// map even if we load multiple version of this module.
global.litPropertyMetadata ??= new WeakMap();
/**
 * Base element class which manages element properties and attributes. When
 * properties change, the `update` method is asynchronously called. This method
 * should be supplied by subclasses to render updates as desired.
 * @noInheritDoc
 */
class ReactiveElement
// In the Node build, this `extends` clause will be substituted with
// `(globalThis.HTMLElement ?? HTMLElement)`.
//
// This way, we will first prefer any global `HTMLElement` polyfill that the
// user has assigned, and then fall back to the `HTMLElement` shim which has
// been imported (see note at the top of this file about how this import is
// generated by Rollup). Note that the `HTMLElement` variable has been
// shadowed by this import, so it no longer refers to the global.
 extends (globalThis.HTMLElement ?? HTMLElement) {
    /**
     * Adds an initializer function to the class that is called during instance
     * construction.
     *
     * This is useful for code that runs against a `ReactiveElement`
     * subclass, such as a decorator, that needs to do work for each
     * instance, such as setting up a `ReactiveController`.
     *
     * ```ts
     * const myDecorator = (target: typeof ReactiveElement, key: string) => {
     *   target.addInitializer((instance: ReactiveElement) => {
     *     // This is run during construction of the element
     *     new MyController(instance);
     *   });
     * }
     * ```
     *
     * Decorating a field will then cause each instance to run an initializer
     * that adds a controller:
     *
     * ```ts
     * class MyElement extends LitElement {
     *   @myDecorator foo;
     * }
     * ```
     *
     * Initializers are stored per-constructor. Adding an initializer to a
     * subclass does not add it to a superclass. Since initializers are run in
     * constructors, initializers will run in order of the class hierarchy,
     * starting with superclasses and progressing to the instance's class.
     *
     * @nocollapse
     */
    static addInitializer(initializer) {
        this.__prepare();
        (this._initializers ??= []).push(initializer);
    }
    /**
     * Returns a list of attributes corresponding to the registered properties.
     * @nocollapse
     * @category attributes
     */
    static get observedAttributes() {
        // Ensure we've created all properties
        this.finalize();
        // this.__attributeToPropertyMap is only undefined after finalize() in
        // ReactiveElement itself. ReactiveElement.observedAttributes is only
        // accessed with ReactiveElement as the receiver when a subclass or mixin
        // calls super.observedAttributes
        return (this.__attributeToPropertyMap && [...this.__attributeToPropertyMap.keys()]);
    }
    /**
     * Creates a property accessor on the element prototype if one does not exist
     * and stores a {@linkcode PropertyDeclaration} for the property with the
     * given options. The property setter calls the property's `hasChanged`
     * property option or uses a strict identity check to determine whether or not
     * to request an update.
     *
     * This method may be overridden to customize properties; however,
     * when doing so, it's important to call `super.createProperty` to ensure
     * the property is setup correctly. This method calls
     * `getPropertyDescriptor` internally to get a descriptor to install.
     * To customize what properties do when they are get or set, override
     * `getPropertyDescriptor`. To customize the options for a property,
     * implement `createProperty` like this:
     *
     * ```ts
     * static createProperty(name, options) {
     *   options = Object.assign(options, {myOption: true});
     *   super.createProperty(name, options);
     * }
     * ```
     *
     * @nocollapse
     * @category properties
     */
    static createProperty(name, options = defaultPropertyDeclaration) {
        // If this is a state property, force the attribute to false.
        if (options.state) {
            options.attribute = false;
        }
        this.__prepare();
        // Whether this property is wrapping accessors.
        // Helps control the initial value change and reflection logic.
        if (this.prototype.hasOwnProperty(name)) {
            options = Object.create(options);
            options.wrapped = true;
        }
        this.elementProperties.set(name, options);
        if (!options.noAccessor) {
            const key = // Use Symbol.for in dev mode to make it easier to maintain state
                    // when doing HMR.
                    Symbol.for(`${String(name)} (@property() cache)`)
                ;
            const descriptor = this.getPropertyDescriptor(name, key, options);
            if (descriptor !== undefined) {
                defineProperty(this.prototype, name, descriptor);
            }
        }
    }
    /**
     * Returns a property descriptor to be defined on the given named property.
     * If no descriptor is returned, the property will not become an accessor.
     * For example,
     *
     * ```ts
     * class MyElement extends LitElement {
     *   static getPropertyDescriptor(name, key, options) {
     *     const defaultDescriptor =
     *         super.getPropertyDescriptor(name, key, options);
     *     const setter = defaultDescriptor.set;
     *     return {
     *       get: defaultDescriptor.get,
     *       set(value) {
     *         setter.call(this, value);
     *         // custom action.
     *       },
     *       configurable: true,
     *       enumerable: true
     *     }
     *   }
     * }
     * ```
     *
     * @nocollapse
     * @category properties
     */
    static getPropertyDescriptor(name, key, options) {
        const { get, set } = getOwnPropertyDescriptor(this.prototype, name) ?? {
            get() {
                return this[key];
            },
            set(v) {
                this[key] = v;
            },
        };
        if (get == null) {
            if ('value' in (getOwnPropertyDescriptor(this.prototype, name) ?? {})) {
                throw new Error(`Field ${JSON.stringify(String(name))} on ` +
                    `${this.name} was declared as a reactive property ` +
                    `but it's actually declared as a value on the prototype. ` +
                    `Usually this is due to using @property or @state on a method.`);
            }
            issueWarning('reactive-property-without-getter', `Field ${JSON.stringify(String(name))} on ` +
                `${this.name} was declared as a reactive property ` +
                `but it does not have a getter. This will be an error in a ` +
                `future version of Lit.`);
        }
        return {
            get,
            set(value) {
                const oldValue = get?.call(this);
                set?.call(this, value);
                this.requestUpdate(name, oldValue, options);
            },
            configurable: true,
            enumerable: true,
        };
    }
    /**
     * Returns the property options associated with the given property.
     * These options are defined with a `PropertyDeclaration` via the `properties`
     * object or the `@property` decorator and are registered in
     * `createProperty(...)`.
     *
     * Note, this method should be considered "final" and not overridden. To
     * customize the options for a given property, override
     * {@linkcode createProperty}.
     *
     * @nocollapse
     * @final
     * @category properties
     */
    static getPropertyOptions(name) {
        return this.elementProperties.get(name) ?? defaultPropertyDeclaration;
    }
    /**
     * Initializes static own properties of the class used in bookkeeping
     * for element properties, initializers, etc.
     *
     * Can be called multiple times by code that needs to ensure these
     * properties exist before using them.
     *
     * This method ensures the superclass is finalized so that inherited
     * property metadata can be copied down.
     * @nocollapse
     */
    static __prepare() {
        if (this.hasOwnProperty(JSCompiler_renameProperty('elementProperties'))) {
            // Already prepared
            return;
        }
        // Finalize any superclasses
        const superCtor = getPrototypeOf(this);
        superCtor.finalize();
        // Create own set of initializers for this class if any exist on the
        // superclass and copy them down. Note, for a small perf boost, avoid
        // creating initializers unless needed.
        if (superCtor._initializers !== undefined) {
            this._initializers = [...superCtor._initializers];
        }
        // Initialize elementProperties from the superclass
        this.elementProperties = new Map(superCtor.elementProperties);
    }
    /**
     * Finishes setting up the class so that it's ready to be registered
     * as a custom element and instantiated.
     *
     * This method is called by the ReactiveElement.observedAttributes getter.
     * If you override the observedAttributes getter, you must either call
     * super.observedAttributes to trigger finalization, or call finalize()
     * yourself.
     *
     * @nocollapse
     */
    static finalize() {
        if (this.hasOwnProperty(JSCompiler_renameProperty('finalized'))) {
            return;
        }
        this.finalized = true;
        this.__prepare();
        // Create properties from the static properties block:
        if (this.hasOwnProperty(JSCompiler_renameProperty('properties'))) {
            const props = this.properties;
            const propKeys = [
                ...getOwnPropertyNames(props),
                ...getOwnPropertySymbols(props),
            ];
            for (const p of propKeys) {
                this.createProperty(p, props[p]);
            }
        }
        // Create properties from standard decorator metadata:
        const metadata = this[Symbol.metadata];
        if (metadata !== null) {
            const properties = litPropertyMetadata.get(metadata);
            if (properties !== undefined) {
                for (const [p, options] of properties) {
                    this.elementProperties.set(p, options);
                }
            }
        }
        // Create the attribute-to-property map
        this.__attributeToPropertyMap = new Map();
        for (const [p, options] of this.elementProperties) {
            const attr = this.__attributeNameForProperty(p, options);
            if (attr !== undefined) {
                this.__attributeToPropertyMap.set(attr, p);
            }
        }
        this.elementStyles = this.finalizeStyles(this.styles);
        {
            if (this.hasOwnProperty('createProperty')) {
                issueWarning('no-override-create-property', 'Overriding ReactiveElement.createProperty() is deprecated. ' +
                    'The override will not be called with standard decorators');
            }
            if (this.hasOwnProperty('getPropertyDescriptor')) {
                issueWarning('no-override-get-property-descriptor', 'Overriding ReactiveElement.getPropertyDescriptor() is deprecated. ' +
                    'The override will not be called with standard decorators');
            }
        }
    }
    /**
     * Takes the styles the user supplied via the `static styles` property and
     * returns the array of styles to apply to the element.
     * Override this method to integrate into a style management system.
     *
     * Styles are deduplicated preserving the _last_ instance in the list. This
     * is a performance optimization to avoid duplicated styles that can occur
     * especially when composing via subclassing. The last item is kept to try
     * to preserve the cascade order with the assumption that it's most important
     * that last added styles override previous styles.
     *
     * @nocollapse
     * @category styles
     */
    static finalizeStyles(styles) {
        const elementStyles = [];
        if (Array.isArray(styles)) {
            // Dedupe the flattened array in reverse order to preserve the last items.
            // Casting to Array<unknown> works around TS error that
            // appears to come from trying to flatten a type CSSResultArray.
            const set = new Set(styles.flat(Infinity).reverse());
            // Then preserve original order by adding the set items in reverse order.
            for (const s of set) {
                elementStyles.unshift(getCompatibleStyle(s));
            }
        }
        else if (styles !== undefined) {
            elementStyles.push(getCompatibleStyle(styles));
        }
        return elementStyles;
    }
    /**
     * Returns the property name for the given attribute `name`.
     * @nocollapse
     */
    static __attributeNameForProperty(name, options) {
        const attribute = options.attribute;
        return attribute === false
            ? undefined
            : typeof attribute === 'string'
                ? attribute
                : typeof name === 'string'
                    ? name.toLowerCase()
                    : undefined;
    }
    constructor() {
        super();
        this.__instanceProperties = undefined;
        /**
         * True if there is a pending update as a result of calling `requestUpdate()`.
         * Should only be read.
         * @category updates
         */
        this.isUpdatePending = false;
        /**
         * Is set to `true` after the first update. The element code cannot assume
         * that `renderRoot` exists before the element `hasUpdated`.
         * @category updates
         */
        this.hasUpdated = false;
        /**
         * Name of currently reflecting property
         */
        this.__reflectingProperty = null;
        this.__initialize();
    }
    /**
     * Internal only override point for customizing work done when elements
     * are constructed.
     */
    __initialize() {
        this.__updatePromise = new Promise((res) => (this.enableUpdating = res));
        this._$changedProperties = new Map();
        // This enqueues a microtask that must run before the first update, so it
        // must be called before requestUpdate()
        this.__saveInstanceProperties();
        // ensures first update will be caught by an early access of
        // `updateComplete`
        this.requestUpdate();
        this.constructor._initializers?.forEach((i) => i(this));
    }
    /**
     * Registers a `ReactiveController` to participate in the element's reactive
     * update cycle. The element automatically calls into any registered
     * controllers during its lifecycle callbacks.
     *
     * If the element is connected when `addController()` is called, the
     * controller's `hostConnected()` callback will be immediately called.
     * @category controllers
     */
    addController(controller) {
        (this.__controllers ??= new Set()).add(controller);
        // If a controller is added after the element has been connected,
        // call hostConnected. Note, re-using existence of `renderRoot` here
        // (which is set in connectedCallback) to avoid the need to track a
        // first connected state.
        if (this.renderRoot !== undefined && this.isConnected) {
            controller.hostConnected?.();
        }
    }
    /**
     * Removes a `ReactiveController` from the element.
     * @category controllers
     */
    removeController(controller) {
        this.__controllers?.delete(controller);
    }
    /**
     * Fixes any properties set on the instance before upgrade time.
     * Otherwise these would shadow the accessor and break these properties.
     * The properties are stored in a Map which is played back after the
     * constructor runs.
     */
    __saveInstanceProperties() {
        const instanceProperties = new Map();
        const elementProperties = this.constructor
            .elementProperties;
        for (const p of elementProperties.keys()) {
            if (this.hasOwnProperty(p)) {
                instanceProperties.set(p, this[p]);
                delete this[p];
            }
        }
        if (instanceProperties.size > 0) {
            this.__instanceProperties = instanceProperties;
        }
    }
    /**
     * Returns the node into which the element should render and by default
     * creates and returns an open shadowRoot. Implement to customize where the
     * element's DOM is rendered. For example, to render into the element's
     * childNodes, return `this`.
     *
     * @return Returns a node into which to render.
     * @category rendering
     */
    createRenderRoot() {
        const renderRoot = this.shadowRoot ??
            this.attachShadow(this.constructor.shadowRootOptions);
        adoptStyles(renderRoot, this.constructor.elementStyles);
        return renderRoot;
    }
    /**
     * On first connection, creates the element's renderRoot, sets up
     * element styling, and enables updating.
     * @category lifecycle
     */
    connectedCallback() {
        // Create renderRoot before controllers `hostConnected`
        this.renderRoot ??=
            this.createRenderRoot();
        this.enableUpdating(true);
        this.__controllers?.forEach((c) => c.hostConnected?.());
    }
    /**
     * Note, this method should be considered final and not overridden. It is
     * overridden on the element instance with a function that triggers the first
     * update.
     * @category updates
     */
    enableUpdating(_requestedUpdate) { }
    /**
     * Allows for `super.disconnectedCallback()` in extensions while
     * reserving the possibility of making non-breaking feature additions
     * when disconnecting at some point in the future.
     * @category lifecycle
     */
    disconnectedCallback() {
        this.__controllers?.forEach((c) => c.hostDisconnected?.());
    }
    /**
     * Synchronizes property values when attributes change.
     *
     * Specifically, when an attribute is set, the corresponding property is set.
     * You should rarely need to implement this callback. If this method is
     * overridden, `super.attributeChangedCallback(name, _old, value)` must be
     * called.
     *
     * See [responding to attribute changes](https://developer.mozilla.org/en-US/docs/Web/API/Web_components/Using_custom_elements#responding_to_attribute_changes)
     * on MDN for more information about the `attributeChangedCallback`.
     * @category attributes
     */
    attributeChangedCallback(name, _old, value) {
        this._$attributeToProperty(name, value);
    }
    __propertyToAttribute(name, value) {
        const elemProperties = this.constructor.elementProperties;
        const options = elemProperties.get(name);
        const attr = this.constructor.__attributeNameForProperty(name, options);
        if (attr !== undefined && options.reflect === true) {
            const converter = options.converter?.toAttribute !==
                undefined
                ? options.converter
                : defaultConverter;
            const attrValue = converter.toAttribute(value, options.type);
            if (this.constructor.enabledWarnings.includes('migration') &&
                attrValue === undefined) {
                issueWarning('undefined-attribute-value', `The attribute value for the ${name} property is ` +
                    `undefined on element ${this.localName}. The attribute will be ` +
                    `removed, but in the previous version of \`ReactiveElement\`, ` +
                    `the attribute would not have changed.`);
            }
            // Track if the property is being reflected to avoid
            // setting the property again via `attributeChangedCallback`. Note:
            // 1. this takes advantage of the fact that the callback is synchronous.
            // 2. will behave incorrectly if multiple attributes are in the reaction
            // stack at time of calling. However, since we process attributes
            // in `update` this should not be possible (or an extreme corner case
            // that we'd like to discover).
            // mark state reflecting
            this.__reflectingProperty = name;
            if (attrValue == null) {
                this.removeAttribute(attr);
            }
            else {
                this.setAttribute(attr, attrValue);
            }
            // mark state not reflecting
            this.__reflectingProperty = null;
        }
    }
    /** @internal */
    _$attributeToProperty(name, value) {
        const ctor = this.constructor;
        // Note, hint this as an `AttributeMap` so closure clearly understands
        // the type; it has issues with tracking types through statics
        const propName = ctor.__attributeToPropertyMap.get(name);
        // Use tracking info to avoid reflecting a property value to an attribute
        // if it was just set because the attribute changed.
        if (propName !== undefined && this.__reflectingProperty !== propName) {
            const options = ctor.getPropertyOptions(propName);
            const converter = typeof options.converter === 'function'
                ? { fromAttribute: options.converter }
                : options.converter?.fromAttribute !== undefined
                    ? options.converter
                    : defaultConverter;
            // mark state reflecting
            this.__reflectingProperty = propName;
            const convertedValue = converter.fromAttribute(value, options.type);
            this[propName] =
                convertedValue ??
                    this.__defaultValues?.get(propName) ??
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                    convertedValue;
            // mark state not reflecting
            this.__reflectingProperty = null;
        }
    }
    /**
     * Requests an update which is processed asynchronously. This should be called
     * when an element should update based on some state not triggered by setting
     * a reactive property. In this case, pass no arguments. It should also be
     * called when manually implementing a property setter. In this case, pass the
     * property `name` and `oldValue` to ensure that any configured property
     * options are honored.
     *
     * @param name name of requesting property
     * @param oldValue old value of requesting property
     * @param options property options to use instead of the previously
     *     configured options
     * @category updates
     */
    requestUpdate(name, oldValue, options) {
        // If we have a property key, perform property update steps.
        if (name !== undefined) {
            if (name instanceof Event) {
                issueWarning(``, `The requestUpdate() method was called with an Event as the property name. This is probably a mistake caused by binding this.requestUpdate as an event listener. Instead bind a function that will call it with no arguments: () => this.requestUpdate()`);
            }
            const ctor = this.constructor;
            const newValue = this[name];
            options ??= ctor.getPropertyOptions(name);
            const changed = (options.hasChanged ?? notEqual)(newValue, oldValue) ||
                // When there is no change, check a corner case that can occur when
                // 1. there's a initial value which was not reflected
                // 2. the property is subsequently set to this value.
                // For example, `prop: {useDefault: true, reflect: true}`
                // and el.prop = 'foo'. This should be considered a change if the
                // attribute is not set because we will now reflect the property to the attribute.
                (options.useDefault &&
                    options.reflect &&
                    newValue === this.__defaultValues?.get(name) &&
                    !this.hasAttribute(ctor.__attributeNameForProperty(name, options)));
            if (changed) {
                this._$changeProperty(name, oldValue, options);
            }
            else {
                // Abort the request if the property should not be considered changed.
                return;
            }
        }
        if (this.isUpdatePending === false) {
            this.__updatePromise = this.__enqueueUpdate();
        }
    }
    /**
     * @internal
     */
    _$changeProperty(name, oldValue, { useDefault, reflect, wrapped }, initializeValue) {
        // Record default value when useDefault is used. This allows us to
        // restore this value when the attribute is removed.
        if (useDefault && !(this.__defaultValues ??= new Map()).has(name)) {
            this.__defaultValues.set(name, initializeValue ?? oldValue ?? this[name]);
            // if this is not wrapping an accessor, it must be an initial setting
            // and in this case we do not want to record the change or reflect.
            if (wrapped !== true || initializeValue !== undefined) {
                return;
            }
        }
        // TODO (justinfagnani): Create a benchmark of Map.has() + Map.set(
        // vs just Map.set()
        if (!this._$changedProperties.has(name)) {
            // On the initial change, the old value should be `undefined`, except
            // with `useDefault`
            if (!this.hasUpdated && !useDefault) {
                oldValue = undefined;
            }
            this._$changedProperties.set(name, oldValue);
        }
        // Add to reflecting properties set.
        // Note, it's important that every change has a chance to add the
        // property to `__reflectingProperties`. This ensures setting
        // attribute + property reflects correctly.
        if (reflect === true && this.__reflectingProperty !== name) {
            (this.__reflectingProperties ??= new Set()).add(name);
        }
    }
    /**
     * Sets up the element to asynchronously update.
     */
    async __enqueueUpdate() {
        this.isUpdatePending = true;
        try {
            // Ensure any previous update has resolved before updating.
            // This `await` also ensures that property changes are batched.
            await this.__updatePromise;
        }
        catch (e) {
            // Refire any previous errors async so they do not disrupt the update
            // cycle. Errors are refired so developers have a chance to observe
            // them, and this can be done by implementing
            // `window.onunhandledrejection`.
            Promise.reject(e);
        }
        const result = this.scheduleUpdate();
        // If `scheduleUpdate` returns a Promise, we await it. This is done to
        // enable coordinating updates with a scheduler. Note, the result is
        // checked to avoid delaying an additional microtask unless we need to.
        if (result != null) {
            await result;
        }
        return !this.isUpdatePending;
    }
    /**
     * Schedules an element update. You can override this method to change the
     * timing of updates by returning a Promise. The update will await the
     * returned Promise, and you should resolve the Promise to allow the update
     * to proceed. If this method is overridden, `super.scheduleUpdate()`
     * must be called.
     *
     * For instance, to schedule updates to occur just before the next frame:
     *
     * ```ts
     * override protected async scheduleUpdate(): Promise<unknown> {
     *   await new Promise((resolve) => requestAnimationFrame(() => resolve()));
     *   super.scheduleUpdate();
     * }
     * ```
     * @category updates
     */
    scheduleUpdate() {
        const result = this.performUpdate();
        if (this.constructor.enabledWarnings.includes('async-perform-update') &&
            typeof result?.then ===
                'function') {
            issueWarning('async-perform-update', `Element ${this.localName} returned a Promise from performUpdate(). ` +
                `This behavior is deprecated and will be removed in a future ` +
                `version of ReactiveElement.`);
        }
        return result;
    }
    /**
     * Performs an element update. Note, if an exception is thrown during the
     * update, `firstUpdated` and `updated` will not be called.
     *
     * Call `performUpdate()` to immediately process a pending update. This should
     * generally not be needed, but it can be done in rare cases when you need to
     * update synchronously.
     *
     * @category updates
     */
    performUpdate() {
        // Abort any update if one is not pending when this is called.
        // This can happen if `performUpdate` is called early to "flush"
        // the update.
        if (!this.isUpdatePending) {
            return;
        }
        debugLogEvent?.({ kind: 'update' });
        if (!this.hasUpdated) {
            // Create renderRoot before first update. This occurs in `connectedCallback`
            // but is done here to support out of tree calls to `enableUpdating`/`performUpdate`.
            this.renderRoot ??=
                this.createRenderRoot();
            {
                // Produce warning if any reactive properties on the prototype are
                // shadowed by class fields. Instance fields set before upgrade are
                // deleted by this point, so any own property is caused by class field
                // initialization in the constructor.
                const ctor = this.constructor;
                const shadowedProperties = [...ctor.elementProperties.keys()].filter((p) => this.hasOwnProperty(p) && p in getPrototypeOf(this));
                if (shadowedProperties.length) {
                    throw new Error(`The following properties on element ${this.localName} will not ` +
                        `trigger updates as expected because they are set using class ` +
                        `fields: ${shadowedProperties.join(', ')}. ` +
                        `Native class fields and some compiled output will overwrite ` +
                        `accessors used for detecting changes. See ` +
                        `https://lit.dev/msg/class-field-shadowing ` +
                        `for more information.`);
                }
            }
            // Mixin instance properties once, if they exist.
            if (this.__instanceProperties) {
                // TODO (justinfagnani): should we use the stored value? Could a new value
                // have been set since we stored the own property value?
                for (const [p, value] of this.__instanceProperties) {
                    this[p] = value;
                }
                this.__instanceProperties = undefined;
            }
            // Trigger initial value reflection and populate the initial
            // `changedProperties` map, but only for the case of properties created
            // via `createProperty` on accessors, which will not have already
            // populated the `changedProperties` map since they are not set.
            // We can't know if these accessors had initializers, so we just set
            // them anyway - a difference from experimental decorators on fields and
            // standard decorators on auto-accessors.
            // For context see:
            // https://github.com/lit/lit/pull/4183#issuecomment-1711959635
            const elementProperties = this.constructor
                .elementProperties;
            if (elementProperties.size > 0) {
                for (const [p, options] of elementProperties) {
                    const { wrapped } = options;
                    const value = this[p];
                    if (wrapped === true &&
                        !this._$changedProperties.has(p) &&
                        value !== undefined) {
                        this._$changeProperty(p, undefined, options, value);
                    }
                }
            }
        }
        let shouldUpdate = false;
        const changedProperties = this._$changedProperties;
        try {
            shouldUpdate = this.shouldUpdate(changedProperties);
            if (shouldUpdate) {
                this.willUpdate(changedProperties);
                this.__controllers?.forEach((c) => c.hostUpdate?.());
                this.update(changedProperties);
            }
            else {
                this.__markUpdated();
            }
        }
        catch (e) {
            // Prevent `firstUpdated` and `updated` from running when there's an
            // update exception.
            shouldUpdate = false;
            // Ensure element can accept additional updates after an exception.
            this.__markUpdated();
            throw e;
        }
        // The update is no longer considered pending and further updates are now allowed.
        if (shouldUpdate) {
            this._$didUpdate(changedProperties);
        }
    }
    /**
     * Invoked before `update()` to compute values needed during the update.
     *
     * Implement `willUpdate` to compute property values that depend on other
     * properties and are used in the rest of the update process.
     *
     * ```ts
     * willUpdate(changedProperties) {
     *   // only need to check changed properties for an expensive computation.
     *   if (changedProperties.has('firstName') || changedProperties.has('lastName')) {
     *     this.sha = computeSHA(`${this.firstName} ${this.lastName}`);
     *   }
     * }
     *
     * render() {
     *   return html`SHA: ${this.sha}`;
     * }
     * ```
     *
     * @category updates
     */
    willUpdate(_changedProperties) { }
    // Note, this is an override point for polyfill-support.
    // @internal
    _$didUpdate(changedProperties) {
        this.__controllers?.forEach((c) => c.hostUpdated?.());
        if (!this.hasUpdated) {
            this.hasUpdated = true;
            this.firstUpdated(changedProperties);
        }
        this.updated(changedProperties);
        if (this.isUpdatePending &&
            this.constructor.enabledWarnings.includes('change-in-update')) {
            issueWarning('change-in-update', `Element ${this.localName} scheduled an update ` +
                `(generally because a property was set) ` +
                `after an update completed, causing a new update to be scheduled. ` +
                `This is inefficient and should be avoided unless the next update ` +
                `can only be scheduled as a side effect of the previous update.`);
        }
    }
    __markUpdated() {
        this._$changedProperties = new Map();
        this.isUpdatePending = false;
    }
    /**
     * Returns a Promise that resolves when the element has completed updating.
     * The Promise value is a boolean that is `true` if the element completed the
     * update without triggering another update. The Promise result is `false` if
     * a property was set inside `updated()`. If the Promise is rejected, an
     * exception was thrown during the update.
     *
     * To await additional asynchronous work, override the `getUpdateComplete`
     * method. For example, it is sometimes useful to await a rendered element
     * before fulfilling this Promise. To do this, first await
     * `super.getUpdateComplete()`, then any subsequent state.
     *
     * @return A promise of a boolean that resolves to true if the update completed
     *     without triggering another update.
     * @category updates
     */
    get updateComplete() {
        return this.getUpdateComplete();
    }
    /**
     * Override point for the `updateComplete` promise.
     *
     * It is not safe to override the `updateComplete` getter directly due to a
     * limitation in TypeScript which means it is not possible to call a
     * superclass getter (e.g. `super.updateComplete.then(...)`) when the target
     * language is ES5 (https://github.com/microsoft/TypeScript/issues/338).
     * This method should be overridden instead. For example:
     *
     * ```ts
     * class MyElement extends LitElement {
     *   override async getUpdateComplete() {
     *     const result = await super.getUpdateComplete();
     *     await this._myChild.updateComplete;
     *     return result;
     *   }
     * }
     * ```
     *
     * @return A promise of a boolean that resolves to true if the update completed
     *     without triggering another update.
     * @category updates
     */
    getUpdateComplete() {
        return this.__updatePromise;
    }
    /**
     * Controls whether or not `update()` should be called when the element requests
     * an update. By default, this method always returns `true`, but this can be
     * customized to control when to update.
     *
     * @param _changedProperties Map of changed properties with old values
     * @category updates
     */
    shouldUpdate(_changedProperties) {
        return true;
    }
    /**
     * Updates the element. This method reflects property values to attributes.
     * It can be overridden to render and keep updated element DOM.
     * Setting properties inside this method will *not* trigger
     * another update.
     *
     * @param _changedProperties Map of changed properties with old values
     * @category updates
     */
    update(_changedProperties) {
        // The forEach() expression will only run when __reflectingProperties is
        // defined, and it returns undefined, setting __reflectingProperties to
        // undefined
        this.__reflectingProperties &&= this.__reflectingProperties.forEach((p) => this.__propertyToAttribute(p, this[p]));
        this.__markUpdated();
    }
    /**
     * Invoked whenever the element is updated. Implement to perform
     * post-updating tasks via DOM APIs, for example, focusing an element.
     *
     * Setting properties inside this method will trigger the element to update
     * again after this update cycle completes.
     *
     * @param _changedProperties Map of changed properties with old values
     * @category updates
     */
    updated(_changedProperties) { }
    /**
     * Invoked when the element is first updated. Implement to perform one time
     * work on the element after update.
     *
     * ```ts
     * firstUpdated() {
     *   this.renderRoot.getElementById('my-text-area').focus();
     * }
     * ```
     *
     * Setting properties inside this method will trigger the element to update
     * again after this update cycle completes.
     *
     * @param _changedProperties Map of changed properties with old values
     * @category updates
     */
    firstUpdated(_changedProperties) { }
}
/**
 * Memoized list of all element styles.
 * Created lazily on user subclasses when finalizing the class.
 * @nocollapse
 * @category styles
 */
ReactiveElement.elementStyles = [];
/**
 * Options used when calling `attachShadow`. Set this property to customize
 * the options for the shadowRoot; for example, to create a closed
 * shadowRoot: `{mode: 'closed'}`.
 *
 * Note, these options are used in `createRenderRoot`. If this method
 * is customized, options should be respected if possible.
 * @nocollapse
 * @category rendering
 */
ReactiveElement.shadowRootOptions = { mode: 'open' };
// Assigned here to work around a jscompiler bug with static fields
// when compiling to ES5.
// https://github.com/google/closure-compiler/issues/3177
ReactiveElement[JSCompiler_renameProperty('elementProperties')] = new Map();
ReactiveElement[JSCompiler_renameProperty('finalized')] = new Map();
// Apply polyfills if available
polyfillSupport?.({ ReactiveElement });
// Dev mode warnings...
{
    // Default warning set.
    ReactiveElement.enabledWarnings = [
        'change-in-update',
        'async-perform-update',
    ];
    const ensureOwnWarnings = function (ctor) {
        if (!ctor.hasOwnProperty(JSCompiler_renameProperty('enabledWarnings'))) {
            ctor.enabledWarnings = ctor.enabledWarnings.slice();
        }
    };
    ReactiveElement.enableWarning = function (warning) {
        ensureOwnWarnings(this);
        if (!this.enabledWarnings.includes(warning)) {
            this.enabledWarnings.push(warning);
        }
    };
    ReactiveElement.disableWarning = function (warning) {
        ensureOwnWarnings(this);
        const i = this.enabledWarnings.indexOf(warning);
        if (i >= 0) {
            this.enabledWarnings.splice(i, 1);
        }
    };
}
// IMPORTANT: do not change the property name or the assignment expression.
// This line will be used in regexes to search for ReactiveElement usage.
(global.reactiveElementVersions ??= []).push('2.1.1');
if (global.reactiveElementVersions.length > 1) {
    queueMicrotask(() => {
        issueWarning('multiple-versions', `Multiple versions of Lit loaded. Loading multiple versions ` +
            `is not recommended.`);
    });
}

export { ReactiveElement, adoptStyles, defaultConverter, getCompatibleStyle, notEqual };
//# sourceMappingURL=reactive-element.js.map
