# @lit/context

Make data available to entire component subtrees with the context protocol
implementation for Lit.

[![Build Status](https://github.com/lit/lit/workflows/Tests/badge.svg)](https://github.com/lit/lit/actions?query=workflow%3ATests)
[![Published on npm](https://img.shields.io/npm/v/@lit/context.svg?logo=npm)](https://www.npmjs.com/package/@lit/context)
[![Join our Discord](https://img.shields.io/badge/discord-join%20chat-5865F2.svg?logo=discord&logoColor=fff)](https://lit.dev/discord/)
[![Mentioned in Awesome Lit](https://awesome.re/mentioned-badge.svg)](https://github.com/web-padawan/awesome-lit)

## Documentation

Full documentation is available at [lit.dev/docs/data/context/](https://lit.dev/docs/data/context/).

## Overview

This module defines an implementation of controllers and decorators for using the [Context Protocol](https://github.com/webcomponents-cg/community-protocols/blob/main/proposals/context.md) as defined by the Web Components Community Group.

This protocol facilitates the communication between components lower in the DOM hierarchy with their ancestors, allowing data to be passed down the tree without having to be passed via 'prop drilling' where each element in the path passes on the data.

For further explanation of the Context Protocol please see the [community protocol documentation](https://github.com/webcomponents-cg/community-protocols/blob/main/proposals/context.md).

## Usage

There are several different usages of the Context API.

### Creating a Context

First lets define a context key we can use elsewhere in our examples:

#### **`logger.ts`**:

```ts
import {createContext} from '@lit/context';

export interface Logger {
  log: (msg: string) => void;
}

export const loggerContext = createContext<Logger>('logger');
```

### Consuming a Context

Now we can define a consumer for this context - some component in our app needs the logger.

Here we're using the `@consume` property decorator to make a `ContextConsumer` controller
and update its value when the context changes:

#### **`my-element.ts`**:

```ts
import {LitElement, property} from 'lit';
import {consume} from '@lit/context';
import {Logger, loggerContext} from './logger.js';

export class MyElement extends LitElement {
  @consume({context: loggerContext, subscribe: true})
  @property({attribute: false})
  public logger?: Logger;

  private doThing() {
    this.logger?.log('a thing was done');
  }
}
```

Another way we can use a context in a component is via the `ContextConsumer` controller directly:

#### **`my-element.ts`**:

```ts
import {LitElement, property} from 'lit';
import {ContextConsumer} from '@lit/context';
import {Logger, loggerContext} from './logger.js';

export class MyElement extends LitElement {
  public logger = new ContextConsumer(
    this,
    loggerContext,
    undefined, // don't need to pass a callback
    true // pass true to get updates if the logger changes
  );

  private doThing() {
    this.logger.value?.log('a thing was done');
  }
}
```

### Providing a Context

Finally we want to be able to provide this context from somewhere higher in the DOM.

Here we're using a `@provide` property decorator to make a `ContextProvider`
controller and update its value when the property value changes.

#### **`my-app.ts`**:

```ts
import {LitElement, property, html} from 'lit';
import {provide} from '@lit/context';
import {loggerContext, Logger} from './logger.js';

export class MyApp extends LitElement {
  @provide({context: loggerContext})
  @property({attribute: false})
  public logger: Logger = {
    log: (msg) => {
      console.log(`[my-app] ${msg}`);
    },
  };

  protected render(): TemplateResult {
    return html`<my-thing></my-thing>`;
  }
}
```

We can also use the `ContextProvider` controller directly:

#### **`my-app.ts`**:

```ts
import {LitElement, html} from 'lit';
import {ContextProvider} from '@lit/context';
import {loggerContext, Logger} from './logger.js';

export class MyApp extends LitElement {
  // create a provider controller and a default logger
  private provider = new ContextProvider(this, loggerContext, {
    log: (msg) => {
      console.log(`[my-app] ${msg}`);
    },
  });

  protected render(): TemplateResult {
    return html`<my-thing></my-thing>`;
  }

  public setLogger(newLogger: Logger) {
    // update the provider with a new logger value
    this.provider.setValue(newLogger);
  }
}
```

`ContextProvider` can also be used with plain HTML elements. This can be
useful to provide a context provider without introducing a custom element:

#### **`my-app.js`**:

```js
import {ContextProvider} from '@lit/context';
import {loggerContext, Logger} from './logger.js';

// create a provider for the whole document body.
const loggingProvider = new ContextProvider(document.body, {
    context: loggerContext,
    initialValue: {
      log: (msg) => {
        console.log(`[global] ${msg}`);
      },
    },
);
```

If the provider is being added when there is already a consumer registered with
a parent of the specified element or with a `ContextRoot`, then
`.hostConnected()` must be called on the provider after creating it. This
ensures existing downstream consumers will now get their context values from the
closest parent provider.

## Known Issues

### Late upgraded Context Providers

In some cases you might have a context providing element that is upgraded late. LightDOM content below this provider may end up requesting a context that is currently not provided by any provider.

To solve this case we provide a `ContextRoot` class which can intercept and track unsatisfied `context-request` events and then redispatch these requests when providers are updated.

Example usage:

#### **`index.ts`**:

```ts
import {ContextRoot} from '@lit/context';
const root = new ContextRoot();
root.attach(document.body);
```

The `ContextRoot` can be attached to any element and it will gather a list of any context requests which are received at the attached element. The `ContextProvider` controllers will emit `context-provider` events when they are connected to the DOM. These events act as triggers for the `ContextRoot` to redispatch these `context-request` events from their sources.

This solution has a small overhead, in that if a provider is not within the DOM hierarchy of the unsatisfied requests we are unnecessarily refiring these requests, but this approach is safest and most correct in that it is very hard to manage unstable DOM hierarchies with the semantics of slotting and reparenting that is common in web components implementations.

Note that ContextRoot uses [WeakRefs](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/WeakRef) which are not supported in IE11.

### Protected / Private Properties

You can use the `@consume` and `@provide` decorators on TypeScript `protected` and `private` properties, but be aware that there is no type checking between the type of the context and the type of the property. This is because the TypeScript compiler does not make type information for protected or private properties available to decorators. Standard `#private` properties are not supported at all.

We expect to fix all of this when we switch to standard decorators. See [#3926](https://github.com/lit/lit/issues/3926).

## Contributing

Please see [CONTRIBUTING.md](../../CONTRIBUTING.md).
