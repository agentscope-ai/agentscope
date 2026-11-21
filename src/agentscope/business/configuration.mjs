// Trace save-time configuration through the composables used by the editor.
import ts from 'typescript';

export function visit(node, fn) {
  fn(node);
  ts.forEachChild(node, child => { visit(child, fn); });
}

export function composedBindings(module) {
  const result = new Map();
  for (const statement of module.ast.statements.filter(ts.isVariableStatement)) {
    for (const decl of statement.declarationList.declarations) {
      const call = decl.initializer;
      if (!call || !ts.isCallExpression(call) || !ts.isObjectBindingPattern(decl.name)) continue;
      const link = module.imports.get(call.expression.getText());
      if (!link) continue;
      for (const member of decl.name.elements) {
        result.set(member.name.getText(), {decl, link, call, member: (member.propertyName ?? member.name).getText()});
      }
    }
  }
  return result;
}

export function adapterCalls(module, method) {
  const calls = new Map();
  const aliases = new Set(module.ast.statements.filter(ts.isImportDeclaration)
    .map(stmt => stmt.importClause?.name?.text).filter(Boolean));
  for (const [name, fn] of module.defs) {
    visit(fn.body, node => {
      if (!ts.isCallExpression(node) || !ts.isPropertyAccessExpression(node.expression)) return;
      const {expression, name: operation} = node.expression;
      if (!aliases.has(expression.getText()) || operation.text !== method) return;
      const url = node.arguments[0];
      if (!url) return;
      if (ts.isStringLiteralLike(url)) calls.set(name, {path: url.text, node});
      else if (ts.isTemplateExpression(url) && url.templateSpans.length === 1) {
        calls.set(name, {path: url.head.text + '{}' + url.templateSpans[0].literal.text, node});
      }
    });
  }
  return calls;
}

function linked(module, name, api) {
  const [from, original] = module.imports.get(name) ?? [];
  if (!from) throw new Error('Unresolved configuration import ' + name);
  return [api.localModule(from, module.real), original];
}

function headerValidation(page, api) {
  // Find the form-field renderer that receives the rule-bearing header config.
  const renderers = [];
  for (const imp of page.ast.statements.filter(ts.isImportDeclaration)) {
    const name = imp.importClause?.name?.text;
    if (!name || !imp.moduleSpecifier.text.endsWith('.vue')) continue;
    const tag = name.replace(/[A-Z]/g,(c,i) => (i ? '-' : '') + c.toLowerCase());
    let bound;
    function walk(node) {
      if (node.tag === tag || node.tag === name) {
        const bindings = (node.props ?? []).filter(p => p.name === 'bind').map(p => p.arg?.content);
        if (bindings.includes('cfg') && bindings.includes('form')) bound = node.loc;
      }
      for (const child of node.children ?? []) if (typeof child === 'object') walk(child);
    }
    if (page.template) walk(page.template);
    if (!bound) continue;
    const component = api.localModule(imp.moduleSpecifier.text,page.real);
    const bindings = composedBindings(component);
    const hooks = ['resolvedDisabled','rules'].map(key => {
      const item = bindings.get(key);
      if (!item) throw new Error('Cannot trace form field ' + key);
      return {file:api.localModule(item.link[0],component.real), name:item.link[1], member:item.member};
    });
    renderers.push({hooks,evidence:[
      {file:page.relative,start:bound.start.line,end:bound.end.line,code:bound.source},
      api.evidence(component.ast.statements.find(stmt =>
        ts.isVariableStatement(stmt) && stmt.getText().includes('rules,')))
    ]});
  }
  if (renderers.length !== 1) return;
  // The header's own missing-value collector points at the same required
  // field list used by the renderer. Use its list, then the renderer's rules.
  const lists = new Set();
  for (const fn of page.defs.values()) {
    if (!ts.isBlock(fn.body)) continue;
    visit(fn.body,n => {
      if (!ts.isCallExpression(n) || n.expression.getText() !== 'formatRequiredFieldMissing') return;
      visit(fn.body,other => {
        if (!ts.isCallExpression(other) || !ts.isPropertyAccessExpression(other.expression) ||
            other.expression.name.text !== 'filter') return;
        const input = other.expression.expression, callback = other.arguments[0];
        if (ts.isPropertyAccessExpression(input) && input.name.text === 'value' && callback &&
            ts.isArrowFunction(callback) && ts.isPropertyAccessExpression(callback.body) &&
            callback.body.name.text === 'required') lists.add(input.expression.getText());
      });
    });
  }
  if (lists.size !== 1) return;
  return {...renderers[0], fields:[...lists][0]};
}

export function configuration(page, api, contract, guard, headerForm) {
  // The check dominates the save and its arguments identify the save phase.
  const names = new Set();
  visit(guard, n => { if (ts.isIdentifier(n)) names.add(n.text); });
  const composition = composedBindings(page);
  const header = headerForm && headerValidation(page, api);
  if (header) names.add(header.fields);
  // Follow the guard's transitive page declarations to their composables.
  const declarations = new Map(page.defs);
  for (const stmt of page.ast.statements.filter(ts.isVariableStatement)) {
    for (const decl of stmt.declarationList.declarations) {
      if (ts.isIdentifier(decl.name)) declarations.set(decl.name.text, decl);
    }
  }
  for (const name of names) {
    if (declarations.has(name)) visit(declarations.get(name), n => { if (ts.isIdentifier(n)) names.add(n.text); });
  }
  const imports = new Map(), groups = new Map(), lookups = {};
  const add = (name, item) => {
    let group = groups.get(item.decl.getStart());
    if (!group) {
      group = {call: item.call, link: item.link, members: new Map()};
      groups.set(item.decl.getStart(), group);
    }
    group.members.set(name, item.member);
    return group;
  };

  const sources = [];
  for (const [name, item] of composition) {
    if (!names.has(name)) continue;
    const [from, hook] = item.link;
    const module = api.localModule(from, page.real);
    const fn = module.defs.get(hook);
    if (!fn || !ts.isBlock(fn.body)) continue;
    // Select the field resolver only when its sibling loader reads a map from a documented GET.
    const loader = [...composition.values()].find(other => other.decl === item.decl && other.member === 'load');
    if (!loader) continue;
    let endpoint;
    visit(fn.body, n => {
      if (!ts.isCallExpression(n) || !ts.isPropertyAccessExpression(n.expression)) return;
      const target = n.expression.expression.getText();
      if (!module.imports.has(target) || n.arguments.length !== 0) return;
      const enclosing = [];
      for (let p = n; p !== fn; p = p.parent) if (ts.isVariableDeclaration(p)) enclosing.push(p.name.getText());
      if (!enclosing.includes(loader.member)) return;
      const [adapter, symbol] = linked(module, target, api);
      const request = adapterCalls(adapter, 'get').get(symbol + '.' + n.expression.name.text);
      if (request) {
        if (!contract.maps.includes(request.path)) {
          const error = new Error('Undocumented field configuration map ' + request.path);
          error.configurationRequired = true;
          throw error;
        }
        endpoint = {adapter,request};
      }
    });
    if (!endpoint) continue;
    const cacheReturn = fn.body.statements.find(ts.isReturnStatement)?.expression;
    const cache = cacheReturn?.properties?.find(prop => prop.name?.getText() === 'cache');
    if (!cache || !ts.isShorthandPropertyAssignment(cache)) continue;
    add(name, item);
    const state = '__fieldConfiguration';
    const file = module.real;
    imports.set(file, {refs: new Map([[cache.name.text, state]])});
    const {adapter, request} = endpoint;
    lookups[state] = {paths:[request.path], code:'return {path:' + JSON.stringify(request.path) + ',query:{}};',
      kind:'object', authoritative:true, adapterFile:adapter.relative, source:api.evidence(request.node)};
    sources.push(api.evidence(composition.get(name).decl));
  }
  if (!lookups.__fieldConfiguration) return;

  // Resolve the loaders and module constants of the same column hook instances.
  const columns = [...composition].filter(([name, item]) => names.has(name) &&
    item.call.arguments.length === 1 && [...composition.values()].some(binding =>
      binding.decl === item.decl && binding.member === 'loadVisibleColumns'));
  if (!columns.length) throw new Error('Cannot trace editor field visibility');
  for (const [name, item] of columns) add(name,item);
  const instance = columns[0][1];
  const [hookFile] = linked(page, instance.call.expression.getText(), api);
  const hookFn = hookFile.defs.get(instance.link[1]);
  const transport = [...hookFile.imports.keys()].find(name => {
    let used = false;
    visit(hookFn.body, n => {
      if (ts.isPropertyAccessExpression(n) && n.expression.getText() === name &&
          n.name.text === 'getPermissionList') used = true;
    });
    return used;
  });
  const [apiFile, apiName] = linked(hookFile, transport, api);
  const standard = ['getList','getPermissionList','getVisibleColumns','getReadonlyColumns'];
  const operations = adapterCalls(apiFile, 'get');
  const responseKeys = {getList:'configs',getPermissionList:'permissions',
    getVisibleColumns:'hiddenColumns',getReadonlyColumns:'readonlyColumns'};
  const templates = Object.fromEntries(standard.map(name => {
    const request = operations.get(apiName + '.' + name);
    const route = request && contract.reads.find(path => path.replace(/\{[^{}]+\}/g,'{}') === request.path);
    if (!route) {
      const error = new Error('Undocumented column configuration operation ' + name);
      error.configurationRequired = true;
      throw error;
    }
    sources.push(api.evidence(request.node));
    return [responseKeys[name], route];
  }));
  const moduleCodes = [...new Set(columns.map(([,item]) => {
    const arg = item.call.arguments[0];
    const value = ts.isIdentifier(arg) ? declarations.get(arg.text)?.initializer : arg;
    if (!value || !ts.isStringLiteralLike(value)) throw new Error('Expected a static configuration module code');
    return value.text;
  }))];
  imports.set(hookFile.real, {bindings: new Map([[transport,
    'Object.fromEntries(Object.entries(' + JSON.stringify(responseKeys) + ').map(([method, key]) => [method, code => {\n' +
    'const row = __requiredState("__columnConfiguration")[key];\n' +
    'if (!Object.hasOwn(row,code)) throw new Error("Missing ERP column configuration for " + code);\n' +
    'return Promise.resolve(row[code]); }]))'
  ]])});
  for (const group of groups.values()) {
    if (group.link[0] !== instance.link[0]) continue;
    group.load = ['loadPermissions','loadVisibleColumns'];
    if ([...group.members.values()].includes('columnConfigs')) group.load.push('loadConfig');
  }
  lookups.__columnConfiguration = {templates,moduleCodes,authoritative:true,adapterFile:apiFile.relative, source:sources.slice(-4)};
  if (header) {
    header.form = headerForm;
    header.module = columns.find(([name,item]) => name === 'columnConfigs')?.[1].call.arguments[0].getText();
    if (!header.module) throw new Error('Cannot resolve header configuration module');
    sources.push(...header.evidence);
  }
  return {groups, imports, lookups, sources, header};
}
