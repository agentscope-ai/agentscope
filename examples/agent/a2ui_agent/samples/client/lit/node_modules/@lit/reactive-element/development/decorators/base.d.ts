/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
/**
 * Generates a public interface type that removes private and protected fields.
 * This allows accepting otherwise incompatible versions of the type (e.g. from
 * multiple copies of the same package in `node_modules`).
 */
export type Interface<T> = {
    [K in keyof T]: T[K];
};
export type Constructor<T> = {
    new (...args: any[]): T;
};
//# sourceMappingURL=base.d.ts.map