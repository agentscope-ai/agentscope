import{noChange as t}from"../lit-html.js";import{isPrimitive as s}from"../directive-helpers.js";import{AsyncDirective as i}from"../async-directive.js";import{PseudoWeakRef as e,Pauser as r}from"./private-async-helpers.js";import{directive as o}from"../directive.js";
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const n=t=>!s(t)&&"function"==typeof t.then,h=1073741823;class c extends i{constructor(){super(...arguments),this._$Cwt=h,this._$Cbt=[],this._$CK=new e(this),this._$CX=new r}render(...s){return s.find((t=>!n(t)))??t}update(s,i){const e=this._$Cbt;let r=e.length;this._$Cbt=i;const o=this._$CK,c=this._$CX;this.isConnected||this.disconnected();for(let t=0;t<i.length&&!(t>this._$Cwt);t++){const s=i[t];if(!n(s))return this._$Cwt=t,s;t<r&&s===e[t]||(this._$Cwt=h,r=0,Promise.resolve(s).then((async t=>{for(;c.get();)await c.get();const i=o.deref();if(void 0!==i){const e=i._$Cbt.indexOf(s);e>-1&&e<i._$Cwt&&(i._$Cwt=e,i.setValue(t))}})))}return t}disconnected(){this._$CK.disconnect(),this._$CX.pause()}reconnected(){this._$CK.reconnect(this),this._$CX.resume()}}const m=o(c);export{c as UntilDirective,m as until};
//# sourceMappingURL=until.js.map
