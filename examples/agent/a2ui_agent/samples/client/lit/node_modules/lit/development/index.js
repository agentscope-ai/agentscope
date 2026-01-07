/**
 * @license
 * Copyright 2021 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
// Although these are re-exported from lit-element.js, we add
// them here to effectively pre-fetch them and avoid the extra
// waterfall when loading the lit package unbundled
import '@lit/reactive-element';
import 'lit-html';
export * from 'lit-element/lit-element.js';
export * from 'lit-html/is-server.js';
//# sourceMappingURL=index.js.map