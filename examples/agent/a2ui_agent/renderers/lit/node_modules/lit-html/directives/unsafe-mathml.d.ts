/**
 * @license
 * Copyright 2024 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
import { UnsafeHTMLDirective } from './unsafe-html.js';
declare class UnsafeMathMLDirective extends UnsafeHTMLDirective {
    static directiveName: string;
    static resultType: number;
}
/**
 * Renders the result as MathML, rather than text.
 *
 * The values `undefined`, `null`, and `nothing`, will all result in no content
 * (empty string) being rendered.
 *
 * Note, this is unsafe to use with any user-provided input that hasn't been
 * sanitized or escaped, as it may lead to cross-site-scripting
 * vulnerabilities.
 */
export declare const unsafeMathML: (value: string | typeof import("../lit-html.js").noChange | typeof import("../lit-html.js").nothing | null | undefined) => import("../directive.js").DirectiveResult<typeof UnsafeMathMLDirective>;
/**
 * The type of the class that powers this directive. Necessary for naming the
 * directive's return type.
 */
export type { UnsafeMathMLDirective as UnsafeMathDirective };
//# sourceMappingURL=unsafe-mathml.d.ts.map