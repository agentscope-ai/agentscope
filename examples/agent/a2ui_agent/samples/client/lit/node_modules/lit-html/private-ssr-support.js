import{_$LH as e,noChange as t}from"./lit-html.js";
/**
 * @license
 * Copyright 2019 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
let r=null;const i={boundAttributeSuffix:e.M,marker:e.P,markerMatch:e.A,HTML_RESULT:e.C,getTemplateHtml:e.L,overrideDirectiveResolve:(e,t)=>class extends e{_$AS(e,r){return t(this,r)}},patchDirectiveResolve:(e,t)=>{if(e.prototype._$AS.name!==t.name){r??=e.prototype._$AS.name;for(let i=e.prototype;i!==Object.prototype;i=Object.getPrototypeOf(i))if(i.hasOwnProperty(r))return void(i[r]=t);throw Error("Internal error: It is possible that both dev mode and production mode Lit was mixed together during SSR. Please comment on the issue: https://github.com/lit/lit/issues/4527")}},setDirectiveClass(e,t){e._$litDirective$=t},getAttributePartCommittedValue:(e,r,i)=>{let o=t;return e.j=e=>o=e,e._$AI(r,e,i),o},connectedDisconnectable:e=>({...e,_$AU:!0}),resolveDirective:e.V,AttributePart:e.H,PropertyPart:e.B,BooleanAttributePart:e.N,EventPart:e.U,ElementPart:e.F,TemplateInstance:e.R,isIterable:e.D,ChildPart:e.I};export{i as _$LH};
//# sourceMappingURL=private-ssr-support.js.map
