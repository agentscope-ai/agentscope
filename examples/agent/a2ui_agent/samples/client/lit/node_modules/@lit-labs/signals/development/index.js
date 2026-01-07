/**
 * @license
 * Copyright 2023 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
import { Signal } from 'signal-polyfill';
export * from 'signal-polyfill';
export * from './lib/signal-watcher.js';
export * from './lib/watch.js';
export * from './lib/html-tag.js';
export const State = Signal.State;
export const Computed = Signal.Computed;
export const signal = (value, options) => new Signal.State(value, options);
export const computed = (callback, options) => new Signal.Computed(callback, options);
//# sourceMappingURL=index.js.map