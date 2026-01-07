# LitElement

A simple base class for creating fast, lightweight web components.

[![Build Status](https://github.com/lit/lit/workflows/Tests/badge.svg)](https://github.com/lit/lit/actions?query=workflow%3ATests)
[![Published on npm](https://img.shields.io/npm/v/lit-element.svg?logo=npm)](https://www.npmjs.com/package/lit-element)
[![Join our Discord](https://img.shields.io/badge/discord-join%20chat-5865F2.svg?logo=discord&logoColor=fff)](https://lit.dev/discord/)
[![Mentioned in Awesome Lit](https://awesome.re/mentioned-badge.svg)](https://github.com/web-padawan/awesome-lit)

LitElement is the base class that powers the [Lit](https://lit.dev) library for building fast web components.

Most users should import `LitElement` from the [`lit`](https://www.npmjs.com/package/lit) package rather than installing and importing from the `lit-element` package directly.

## Documentation

Full documentation is available at [lit.dev/docs/components/overview/](https://lit.dev/docs/components/overview/).

## Overview

LitElement is a base class for custom elements that extends the Lit project's
core ReactiveElement (from
[`@lit/reactive-element'](https://www.npmjs.com/package/@lit/reactive-element)) base class with
lit-html templating. ReactiveElement enhances HTMLElement with reactive
properties, additional lifecycle callbacks, convenient inline CSS authoring, and
a set of useful class decorators, while lit-html provides fast, declarative HTML
templating.

### Example

```ts
import {LitElement, html, css} from 'lit';
import {customElement, property} from 'lit/decorators.js';

// Registers the element
@customElement('my-element')
export class MyElement extends LitElement {
  // Styles are applied to the shadow root and scoped to this element
  static styles = css`
    span {
      color: green;
    }
  `;

  // Creates a reactive property that triggers rendering
  @property()
  mood = 'great';

  // Render the component's DOM by returning a Lit template
  render() {
    return html`Web Components are <span>${this.mood}</span>!`;
  }
}
```

## Contributing

Please see [CONTRIBUTING.md](../../CONTRIBUTING.md).
