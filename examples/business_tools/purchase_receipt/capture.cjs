// Capture the original purchase editor as an independent reference for replay.
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { createRequire } = require('node:module');
const { parseArgs } = require('node:util');

const { values } = parseArgs({ options: {
  client: { type: 'string' }, output: { type: 'string' },
  chromium: { type: 'string' }, input: { type: 'string' },
  'base-url': {type:'string'},
} });
if (!values.client || !values.output) throw new Error('Supply --client <ERP frontend-user> and --output <file>');
const root = path.resolve(values.client);
const local = createRequire(path.join(root, 'package.json'));
const { chromium } = local('playwright-core');
const { createServer } = local('vite');
const vue = local('@vitejs/plugin-vue');
const source = path.join(root, 'src/views/purchase/PurchaseOrderView.vue');
const fixture = JSON.parse(fs.readFileSync(values.input || path.join(__dirname,'two_lines.json'))).data;
const cases = [
  ['base', {}], ['quantity', {expectPcs: 5}], ['rounding', {factoryPricePcs: 5.00125}],
  ['edited', {factoryPricePcs: 6}],
  ['piecework', {packSpec:10, damagePcs:2, damageSelectPrice:1.25, freight:7, weightPer:9,
    inQty:2, inboxFeePer:4, inboxCheckError:1, forkliftCheckError:1,
    workers:[{workerName:'甲',isActive:1,inboxPercent:1,damagePickPercent:1}],
    forklifts:[{driverName:'乙',isActive:1,forkliftPercent:1}],
    batches:[{rowNo:1,inPcs:3,inQty:0.3,colorNo:'A',inboundType:'入筐',frameNo:'F'}]}],
  ['payment', {}], ['empty', {}], ['bad-payment', {}],
  ['no-color', {batches:[{inPcs:3,warehouseId:fixture.details[0].warehouseId}]}],
  ['closed', {}], ['approved', {}], ['void', {}],
];
const requiredCases = ['required-header','required-detail','required-batch','required-complete'];
const codes = ['', '.detail', '.batch', '.worker', '.forklift', '.payment'].map(s => 'purchase.order' + s);
const resources = {configs:'list',permissions:'permission/list',hiddenColumns:'visible-columns',readonlyColumns:'readonly-columns'};
const requiredFields = [
  ['purchase.order','remark','到货说明',1,0],
  ['purchase.order.detail','remark1','验收依据',1,0],
  ['purchase.order.batch','warehouseLocation','存放位置',1,0],
  ['purchase.order.detail','remark2','仅审核填写',0,1],
];
const endpoint = values['base-url'] && new URL(values['base-url'].replace(/\/$/,''));
if (endpoint && (endpoint.origin !== 'http://127.0.0.1:' + endpoint.port || endpoint.pathname !== '/api')) {
  throw new Error('Use a disposable loopback ERP API for reference saves');
}
const token = process.env.BUSINESS_API_TOKEN;
if (endpoint && !token) throw new Error('BUSINESS_API_TOKEN is required for live reference saves');

function configurationFor(name) {
  const body = {field:{}, column:Object.fromEntries(Object.keys(resources).map(key =>
    [key,Object.fromEntries(codes.map(code => [code,[]]))]))};
  if (!requiredCases.includes(name)) return body;
  for (const [moduleCode,fieldKey,fieldLabel,isRequired,auditRequired] of requiredFields) {
    (body.field[moduleCode] ??= {})[fieldKey] = {moduleCode,fieldKey,fieldLabel,isRequired,auditRequired,visible:1,isCollapsed:0};
    body.column.permissions[moduleCode].push({columnKey:fieldKey,canView:1,canEdit:1});
  }
  return body;
}

const org = fixture.order.companyId;
const user = {id:1, username:'admin', userType:1, permissions:[], permissionConfigured:false};
const masters = {
  material: fixture.details.map(({materialId,warehouseId},i) => ({id:materialId, materialCode:'QA'+i,
    materialName:'验收材料'+i, companyId:org, teamId:org, teamName:'验收团队',
    supplierId:fixture.order.supplierId, supplierName:'验收供应商', packageSpec:10,
    hasColor:'无色号', defaultWarehouseId:warehouseId})),
  team: [{id:org,teamName:'验收团队',companyId:org}],
  supplier: [{id:fixture.order.supplierId,supplierName:'验收供应商'}],
  company: [{id:org,companyName:'验收公司'}],
  warehouse: [{id:fixture.details[0].warehouseId,warehouseName:'验收仓库'}],
};

// Applied in memory by the local test server. The component's calculations,
// validation, request builder, Axios adapter and save handler run unmodified.
const probe = `
window.receiptReference = {
  ready: () => formEditable.value && purchaseEditorDetailTablesReady.value,
  async load(input) {
    Object.assign(form, input.order)
    details.value = input.details
    payments.value = input.payments
    details.value.forEach(ensureDetailAttachmentState)
    payments.value.forEach(ensurePaymentAttachmentState)
    await nextTick()
    const snapshot = JSON.parse(JSON.stringify({
      data: {order:form, details:details.value, payments:payments.value},
      editorState: {materialList:materialList.value, teamList:teamList.value,
        companyList:companyList.value, paymentDisplayBaseline:paymentDisplayBaseline.value}
    }))
    details.value.forEach(calcDetailRow)
    await nextTick()
    return snapshot
  },
  save: () => handleOk()
}
`;

async function capture(browser, address, name, patch) {
  const page = await browser.newPage({ serviceWorkers: 'block' });
  try {
    const writes = [], reads = [];
    const configuration = configurationFor(name);
    let liveUser = user;
    if (endpoint) {
      const response = await fetch(endpoint + '/auth/info',{headers:{Authorization:'Bearer ' + token}});
      const payload = await response.json();
      if (payload.code !== 200) throw new Error('Unable to read the live account');
      liveUser = payload.data;
    }
    await page.addInitScript(({user,token}) => {
      localStorage.setItem('erp_userInfo', JSON.stringify(user));
      localStorage.setItem('erp_token',token);
      localStorage.setItem('erp_tenantId','1000');
      localStorage.setItem('erp_region','["430000"]');
    }, {user:liveUser,token:token || "fixture"});
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      if (url.origin !== address) return route.abort();
      if (!p.startsWith('/api/')) return route.continue();
      if (endpoint) {
        const isSave = req.method() === 'POST' && p === '/api/purchase/order';
        const isConfigRead = req.method() === 'POST' && p === '/api/system/column-config/bootstrap';
        if (req.method() !== 'GET' && !isSave && !isConfigRead) return route.abort();
        if (isSave) writes.push({method:req.method(),path:p.slice(4),body:req.postDataJSON()});
        if (/^\/api\/purchase\/order\/\d+$/.test(p)) reads.push(p.slice(4));
        return route.continue();
      }
      let data = [];
      const list = p.match(/^\/api\/master\/(\w+)\/list$/);
      if (list) data = masters[list[1]] ?? [];
      else if (p.endsWith('/auth/info')) data = user;
      else if (req.method() === 'POST' && p.endsWith('/purchase/order')) {
        writes.push({method:req.method(), path:p.slice(4), body:req.postDataJSON()});
        data = 918;
      } else if (req.method() === 'GET' && p.endsWith('/purchase/order/918')) {
        reads.push(p.slice(4));
        const body = writes.at(-1).body;
        data = {...body, order:{...body.order, id:918, billNo:'REF-918'}};
      } else if (p.endsWith('/page')) data = {records:[],total:0};
      else if (p.endsWith('/field-description/all')) data = configuration.field;
      else if (p.endsWith('/column-config/bootstrap')) data = configuration.column;
      else if (['/options-map','/init-fields'].some(s => p.endsWith(s))) data = {};
      else {
        for (const [key, route] of Object.entries(resources)) {
          const prefix = '/api/system/column-config/' + route + '/';
          if (p.startsWith(prefix)) data = configuration.column[key][p.slice(prefix.length)] ?? [];
        }
      }
      if (p.includes('/system/approval-flow/status/')) data = null;
      await route.fulfill({json:{code:200, data, message:'success'}});
    });
    await page.goto(address + '/#/purchase/order/new', {waitUntil:'networkidle', timeout:90000});
    await page.waitForFunction(() => window.receiptReference?.ready(), null, {timeout:30000});
    const draft = structuredClone(fixture);
    Object.assign(draft.details[1],patch);
    if (name === 'empty') draft.details = [];
    if (name === 'bad-payment') draft.payments = [{paymentAmount:0,freeAmount:0}];
    if (name === 'payment') draft.payments = [{paymentAmount:5,freeAmount:1}];
    if (name === 'closed') draft.order.closedStatus = 1;
    if (name === 'approved') draft.order.approveStatus = 2;
    if (name === 'void') draft.order.billStatus = 4;
    if (requiredCases.includes(name)) {
      draft.details[0].remark1 = '首行检查通过';
      draft.details[1].batches = [{rowNo:1,colorNo:'A',inPcs:3,inQty:0.3,frameNo:'F',inboundType:'入筐'}];
      if (name !== 'required-header') draft.order.remark = '按验收要求入库';
      if (name === 'required-batch' || name === 'required-complete') draft.details[1].remark1 = '次行检查通过';
      if (name === 'required-complete') draft.details[1].batches[0].warehouseLocation = 'A-01';
    }
    const input = await page.evaluate(data => window.receiptReference.load(data), draft);
    await page.evaluate(() => window.receiptReference.save());
    if (!endpoint) Object.assign(input.editorState,{__fieldConfiguration:configuration.field,__columnConfiguration:configuration.column});
    const notices = page.locator('.ant-message-notice-content');
    await notices.first().waitFor({state:'visible',timeout:5000});
    const message = await notices.first().textContent();
    const uniqueReads = [...new Set(reads)];

    if (!message || writes.length > 1 || writes.length !== uniqueReads.length) {
      throw new Error(name + ': inconsistent reference (' + writes.length + ' saves, ' + uniqueReads.length + ' receipts)');
    }
    console.log(name, writes.length, message);
    return {name, input, request:writes[0] ?? null, reads:uniqueReads, message};
  } finally {
    await page.unrouteAll({behavior:'wait'});
    await page.close();
  }
}

(async () => {
  const server = await createServer({
    configFile:false, root, logLevel:'error', base:'./',
    resolve:{alias:{'@':path.join(root,'src'),'@shared':path.resolve(root,'../shared')}},
    plugins:[{name:'purchase-reference',enforce:'pre',transform(code,id) {
      if (id !== source) return;
      if (!code.includes('</script>')) throw new Error('Expected inline purchase editor');
      return code.replace('</script>', probe + '</script>');
    }},vue()],
    server:{host:'127.0.0.1', port:0, fs:{allow:[root,path.resolve(root,'../shared')]},
      ...(endpoint && {proxy:{'/api':{target:endpoint.origin,changeOrigin:true}}})},
  });
  try {
    await server.listen();
    const address = 'http://127.0.0.1:' + server.httpServer.address().port;
    const browser = await chromium.launch({executablePath:values.chromium,headless:true});
    try {
      const snapshots = [];
      const scenarios = endpoint ? [] : [...cases];
      scenarios.push(...requiredCases.map(name => [name,{}]));
      for (const [name, patch] of scenarios) snapshots.push(await capture(browser,address,name,patch));
      const pageSha256 = createHash('sha256').update(fs.readFileSync(source)).digest('hex');
      fs.writeFileSync(values.output, JSON.stringify({pageSha256,cases:snapshots},null,2) + '\n');
    } finally { await browser.close(); }
  } finally { await server.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
