import{desc as t}from"./base.js";
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */function n(n){return(o,r)=>{const{slot:e}=n??{},s="slot"+(e?`[name=${e}]`:":not([name])");return t(o,r,{get(){const t=this.renderRoot?.querySelector(s);return t?.assignedNodes(n)??[]}})}}export{n as queryAssignedNodes};
//# sourceMappingURL=query-assigned-nodes.js.map
