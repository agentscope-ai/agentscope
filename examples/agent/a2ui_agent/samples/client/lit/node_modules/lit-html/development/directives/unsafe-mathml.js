/**
 * @license
 * Copyright 2024 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
import { directive } from '../directive.js';
import { UnsafeHTMLDirective } from './unsafe-html.js';
const MATHML_RESULT = 3;
class UnsafeMathMLDirective extends UnsafeHTMLDirective {
}
UnsafeMathMLDirective.directiveName = 'unsafeMath';
UnsafeMathMLDirective.resultType = MATHML_RESULT;
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
export const unsafeMathML = directive(UnsafeMathMLDirective);
//# sourceMappingURL=unsafe-mathml.js.map