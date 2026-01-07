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
export declare const State: typeof Signal.State;
export declare const Computed: typeof Signal.Computed;
export declare const signal: <T>(value: T, options?: Signal.Options<T>) => Signal.State<T>;
export declare const computed: <T>(callback: () => T, options?: Signal.Options<T>) => Signal.Computed<T>;
//# sourceMappingURL=index.d.ts.map