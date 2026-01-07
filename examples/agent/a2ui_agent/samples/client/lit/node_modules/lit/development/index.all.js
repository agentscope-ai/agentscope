/**
 * @license
 * Copyright 2021 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
export * from './index.js';
export * from './async-directive.js';
export * from './directive-helpers.js';
export * from './directive.js';
export * from './directives/async-append.js';
export * from './directives/async-replace.js';
export * from './directives/cache.js';
export * from './directives/choose.js';
export * from './directives/class-map.js';
export * from './directives/guard.js';
export * from './directives/if-defined.js';
export * from './directives/join.js';
export * from './directives/keyed.js';
export * from './directives/live.js';
export * from './directives/map.js';
export * from './directives/range.js';
export * from './directives/ref.js';
export * from './directives/repeat.js';
export * from './directives/style-map.js';
export * from './directives/template-content.js';
export * from './directives/unsafe-html.js';
export * from './directives/unsafe-svg.js';
export * from './directives/until.js';
export * from './directives/when.js';
// Any new exports in `packages/lit-html/src/static.ts` need to be added here.
export { html as staticHtml, literal, svg as staticSvg, unsafeStatic, withStatic, } from './static-html.js';
if (!window.litDisableBundleWarning) {
    console.warn('Lit has been loaded from a bundle that combines all core features into ' +
        'a single file. To reduce transfer size and parsing cost, consider ' +
        'using the `lit` npm package directly in your project.');
}
//# sourceMappingURL=index.all.js.map