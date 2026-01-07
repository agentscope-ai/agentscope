import{render as t,nothing as i}from"../lit-html.js";import{directive as s,Directive as e}from"../directive.js";import{isTemplateResult as o,getCommittedValue as n,setCommittedValue as r,insertPart as l,clearPart as c,isCompiledTemplateResult as u}from"../directive-helpers.js";
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const d=t=>u(t)?t._$litType$.h:t.strings,h=s(class extends e{constructor(t){super(t),this.et=new WeakMap}render(t){return[t]}update(s,[e]){const u=o(this.it)?d(this.it):null,h=o(e)?d(e):null;if(null!==u&&(null===h||u!==h)){const e=n(s).pop();let o=this.et.get(u);if(void 0===o){const s=document.createDocumentFragment();o=t(i,s),o.setConnected(!1),this.et.set(u,o)}r(o,[e]),l(o,void 0,e)}if(null!==h){if(null===u||u!==h){const t=this.et.get(h);if(void 0!==t){const i=n(t).pop();c(s),l(s,void 0,i),r(s,[i])}}this.it=e}else this.it=void 0;return this.render(e)}});export{h as cache};
//# sourceMappingURL=cache.js.map
