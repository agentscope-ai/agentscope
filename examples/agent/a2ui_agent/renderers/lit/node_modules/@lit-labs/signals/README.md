# @lit-labs/signals

TC39 Signals Proposal integration for Lit.

[![Build Status](https://github.com/lit/lit/workflows/Tests/badge.svg)](https://github.com/lit/lit/actions?query=workflow%3ATests)
[![Published on npm](https://img.shields.io/npm/v/@lit-labs/signals.svg?logo=npm)](https://www.npmjs.com/package/@lit-labs/signals)
[![Join our Discord](https://img.shields.io/badge/discord-join%20chat-5865F2.svg?logo=discord&logoColor=fff)](https://lit.dev/discord/)
[![Mentioned in Awesome Lit](https://awesome.re/mentioned-badge.svg)](https://github.com/web-padawan/awesome-lit)

> [!WARNING]
>
> This package is part of [Lit Labs](https://lit.dev/docs/libraries/labs/). It
> is published in order to get feedback on the design and may receive breaking
> changes or stop being supported.
>
> Please read our [Lit Labs documentation](https://lit.dev/docs/libraries/labs/)
> before using this library in production.
>
> Give feedback: https://github.com/lit/lit/discussions/4779
>
> RFC: https://github.com/lit/rfcs/blob/main/rfcs/0005-standard-signals.md

## Documentation

Full documentation is available at
[lit.dev/docs/data/signals/](https://lit.dev/docs/data/signals/).

## Overview

`@lit-labs/signals` integrates the [TC39 Signals
Proposal](https://github.com/tc39/proposal-signals) with Lit's template system
and reactive update lifecycle. Signals used within an element's update
lifecycle, such as in a template, will cause the element to re-render when the
signal value changes. Signals can also be used for targetted or "pin-point" DOM
updates, which can update the DOM without running the entire `render()` method.

### The TC39 Signals Proposal

The [TC39 Signals Proposal](https://github.com/tc39/proposal-signals) is a
proposal to add standard signals to the JavaScript language.

This is very exciting for web components, since it means that different web
components that don't use the same libraries can interoperably consume and
produce signals.

It also means that many existing state management systems and observability
libraries that might currently each require their own adapter to integrated with
the Lit lifecycle, might converge on using standard signals so that we only need
one Lit adapter, and eventually no adapter at all as support for signals is
directly added to Lit

### Why Signals?

Signals have several nice attributes for use with reactive components like Lit:

1. Signals are an easy way to create shared observable state - state that many
   elements can use and update when it changes. This is great for things like a
   game state that many components need to read.

2. Signals can be individually observed, and when used in a template binding,
   can be handled so that they only update the DOM their bound to. These
   targetted DOM updates don't re-render the entire template.

3. Standard signals are an observability interoperabiliy point that many
   different libraries can use. Any library that produces signals will work with
   any standard signal watcher.

4. Signals can be good for performance. Signals track dependencies and changes
   so that only signals that miht have changed and have been read are
   re-computd. This can help perform minimal computations and DOM updates when
   doing small updates to large signal graphs or UIs.

5. Signal auto-tracking can reduce the need for component-specific lifecycle
   APIs. For example, rather than having lifecycle callbacks for when updates
   have happened, or when specific reactive properties have changed, any code
   could create a reactive effect that simple accesses the signals it uses, and
   is automatically re-run when they change.

6. Signals may allow for interoperable _synchronous_ and _batched_ DOM updates.
   There are ways to respond to signal changes synchronously but also batched,
   so if reactive properties were backed by signals, an element could re-render
   itself once a batch of them had been updated. Elements could take care to
   update children inside of batches, meaning entire subtrees could be updated
   synchrously. The batching mechanism isn't standard yet, but could be an
   extension to the proposal.

Signals are a natural fit for Lit: a LitElement render method is already
somewhat like a computed signal in that it is computed based on updates to
inputs (reactive properties).

The difference between Lit renders and signals is
that in Lit the data flow is push-based, rather than pull-based as in signals.
Lit elements react when changes are pushed into them, whereas signals
automatically subscribe to the other signals they access. But these approaches
are very compatible, and we can easily make elements subscribe to the signals
they access and trigger an update with an integration library like this one.

### On Proposals and Polyfills

Like all Lit Labs packages, `@lit-labs/signals` package may change frequently,
have serious bugs, or not be maintained as well as Lit's core packages.

Additionally, this package depends on the API defined in the [TC39 Signals
proposal](https://github.com/tc39/proposal-signals) and directly depends on the
[Signals polyfill](https://github.com/proposal-signals/signal-polyfill), which
add more potential sources of instability and bugs. The proposal may change, and
the polfyill may have bugs or serious performance issues. If multiple versions
of the polyfill are included on a page, interoperabiilty may fail.

As the Signals proposal and polyfill progress we will update this package. At
some point we will remove the dependency on the polyfill and assume the standard
signal APIs exist, and pages will have to install the polyfill if needed.

So `@lit-labs/signals` is not recommended for production use. If you choose to
use it, please thouroughly test and check the performance of your components
and/or app _at scale_, with the number of signals and component instances that
you expect in real-world usage.

Please file feedback and bugs with the [Lit
project](https://github.com/lit/lit/issues), the [Signals
Proposal](https://github.com/tc39/proposal-signals), and the [Signals
polyfill](https://github.com/proposal-signals/signal-polyfill) a appropriate.

## Usage

There are three main exports:

- The `SignalWatcher` mixin
- The `watch()` directive
- The `html` template tag, and `withWatch()` template tag factory

### SignalWatcher

`SignalWatcher` is the core of signals integration. It's a mixin that makes an
element watch all signal accesses during the element's reactive update
lifecycle, then triggers an element update when signals change. This includes
signals read in `shouldUpdate()`, `willUpdate()`, `update()`, `render()`,
`updated()`, `firstUpdated()`, and reactive controller's `hostUpdate()` and
`hostUpdated()`.

This effectively makes the the return result of `render()` a computed signal.

```ts
import {LitElement, html} from 'lit';
import {customElement, property} from 'lit/decorators.js';
import {SignalWatcher, signal} from '@lit-labs/signals';

const count = signal(0);

@customElement('signal-example')
export class SignalExample extends SignalWatcher(LitElement) {
  static styles = css`
    :host {
      display: block;
    }
  `;

  render() {
    return html`
      <p>The count is ${count.get()}</p>
      <button @click=${this.#onClick}>Increment</button>
    `;
  }

  #onClick() {
    count.set(count.get() + 1);
  }
}
```

Elements should not _write_ to signals in these lifecycle methods or they might
cause an infinite loop.

### watch() directive

The `watch()` directive accepts a single Signal and renders its value,
subscribing to updates and updating the DOM when the signal changes. This allows
for very targeted updates of the DOM, which can be good for performance (but as
always, measure!).

```ts
import {LitElement, html} from 'lit';
import {customElement, property} from 'lit/decorators.js';
import {SignalWatcher, watch, signal} from '@lit-labs/signals';

const count = signal(0);

@customElement('signal-example')
export class SignalExample extends SignalWatcher(LitElement) {
  static styles = css`
    :host {
      display: block;
    }
  `;

  render() {
    return html`
      <p>The count is ${watch(count)}</p>
      <button @click=${this.#onClick}>Increment</button>
    `;
  }

  #onClick() {
    count.set(count.get() + 1);
  }
}
```

`watch()` updates are batched and run in coordination with the reactive update
lifecycle. When a watched signal changes, it is added to a batch and a reactive
update is requested. Other changes, to reactive properties or signals accessed
outside of `watch()`, are trigger reactive updates as usual.

> [!NOTE]
>
> <!-- -->
>
> During a reactive update, if there are only updates from `watch()` directives,
> then those updates are commited directly _without_ a full template render. If
> any other changes triggered the reactive update, then the whole template is
> re-rendered, along with the latest signal values.

This approach preserves both DOM coherence and targeted updates, and coalesces
updates when both signals and reactive properties change.

`watch()` must be used in conjunction with the `SignalWatcher` mixin.

You can mix and match targeted updates with `watch()` directive and
auto-tracking with `SignalWatcher`. When you pass a signal directly to `watch()`
it is not accessed in a callback watched by `SignalWatcher`, so an update to
that signal will only cause a targeted DOM update and not an full template
render.

### html tag and withWatch()

This package also exports an `html` template tag that can be used in place of
Lit's default `html` tag and automatically wraps any signals in `watch()`.

```ts
import {LitElement} from 'lit';
import {customElement, property} from 'lit/decorators.js';
import {SignalWatcher, html, signal} from '@lit-labs/signals';

const count = signal(0);

@customElement('signal-example')
export class SignalExample extends SignalWatcher(LitElement) {
  static styles = css`
    :host {
      display: block;
    }
  `;

  render() {
    return html`
      <p>The count is ${count}</p>
      <button @click=${this.#onClick}>Increment</button>
    `;
  }

  #onClick() {
    count.set(count.get() + 1);
  }
}
```

#### `withWatch()`

`withWatch()` is a function that wraps an `html` tag function with the
auto-watching functionality. The `html` tag exported by `@lit-labs/signals` is a
convenient export of the core lit-html template tag wrapped with `withWatch()`.

`withWatch()` allows you to compose the signal watching wrapper with other
lit-html tag wrappers like Lit's `withStatic()` utility.

```ts
import {html as coreHtml} from 'lit';
import {withStatic} from 'lit/static-html.js';
import {withWatch} from '@lit-labs/signals';

/**
 * A Lit template tag that support static values and pinpoint signal updates.
 */
const html = withWatch(withStatic(coreHtml));
```

## Future Work

This library will change based on feedback from developers. Some existing dieas we have for futher development are:

- A signal-aware `repeat()` directive that can update items in a list independently of the entire list.
- Signal aware `when()` directive that wraps the condition in a computed signal and watches it.
- A `@property()` decorator that creates a signal-backed property that can be watched.
- An `@effect()` method decorator that runs a method inside a watched computed signal, and re-runs it when any signal dependencies change. This would be an alternative the the common `@observe()` feature request.
- Batched synchronous updates, when using a utility like []`batchedEffect()`](https://github.com/proposal-signals/signal-utils?tab=readme-ov-file#batched-effects)

## Related Libraries

### signal-utils

The [`signal-utils` project](https://github.com/proposal-signals/signal-utils)
contains a lot of utilities for building signals-based data models.

Some of these are especially useful for use cases around shared observable state. The signal-backed collections (arrays, maps, and sets) can help address cases where Lit's reactive properties cannot see internal changes to objects.
