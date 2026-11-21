// Recover the read requests that populate reference lists used by a Vue editor.
import ts from 'typescript';

function visit(node, fn) {
  fn(node);
  ts.forEachChild(node, child => { visit(child, fn); });
}

function identifiers(node) {
  const names = new Set();
  visit(node, n => {
    if (ts.isIdentifier(n) && n.parent.name !== n && n.parent.propertyName !== n) names.add(n.text);
  });
  return names;
}

// Keep only enclosing, preceding const declarations. Nested scopes and other
// Promise.all entries can contain unrelated requests and are never executed.
function visibleConstants(node) {
  const locals = new Map();
  for (let n = node; n; n = n.parent) {
    for (const stmt of n.statements ?? []) {
      if (stmt.end > node.pos || !ts.isVariableStatement(stmt) || !(stmt.declarationList.flags & ts.NodeFlags.Const)) continue;
      for (const d of stmt.declarationList.declarations) {
        if (ts.isIdentifier(d.name) && d.initializer && !locals.has(d.name.text)) locals.set(d.name.text, d);
      }
    }
  }
  return locals;
}

function dependencies(node, locals, stop) {
  const used = new Map();
  function walk(n) {
    for (const name of identifiers(n)) {
      const d = locals.get(name);
      if (!d || used.has(name) || stop.has(name)) continue;
      used.set(name, d);
      walk(d.initializer ?? d.body);
    }
  }
  walk(node);
  return [...used.values()].sort((a,b) => a.pos - b.pos);
}

export function referenceLookups(page, api, bindings, state, reads) {
  const lookups = {};
  const stoppers = new Set([...page.imports.keys(), ...bindings.keys()]);
  visit(page.ast, n => {
    if (!ts.isBinaryExpression(n) || n.operatorToken.kind !== ts.SyntaxKind.EqualsToken ||
        !ts.isPropertyAccessExpression(n.left) || n.left.name.text !== 'value') return;
    const slot = n.left.expression.getText();
    if (!state.includes(slot)) return;
    const indices = [];
    visit(n.right, v => {
      if (ts.isElementAccessExpression(v) && ts.isNumericLiteral(v.argumentExpression)) indices.push(v);
    });
    if (!indices.length) return;
    const at = indices[0];
    const result = visibleConstants(n).get(at.expression.getText())?.initializer;
    const call = result && ts.isAwaitExpression(result) && result.expression;
    if (!call || !ts.isCallExpression(call) || call.expression.getText() !== 'Promise.all' ||
        !ts.isArrayLiteralExpression(call.arguments[0])) return;
    const element = call.arguments[0].elements[Number(at.argumentExpression.text)];
    const loader = element && ts.isCallExpression(element) && element.arguments.find(ts.isArrowFunction);
    if (!loader || loader.parameters.length) return;
    const deps = dependencies(loader.body, visibleConstants(loader), stoppers);
    const referenced = new Set([...identifiers(loader.body), ...deps.flatMap(d => [...identifiers(d.initializer)])]);
    const imports = [...page.imports].filter(([name]) => referenced.has(name));
    if (imports.length !== 1 || imports[0][1][0] === 'vue') return;
    const [alias, [from, symbol]] = imports[0];
    const module = api.localModule(from, page.real);
    const methods = [];
    const paths = new Set();
    for (const [name, fn] of module.defs) {
      if (!name.startsWith(symbol + '.') || !ts.isArrowFunction(fn) || !ts.isCallExpression(fn.body)) continue;
      const req = fn.body, url = req.arguments[0];
      if (!ts.isPropertyAccessExpression(req.expression) || req.expression.name.text !== 'get' ||
          !ts.isStringLiteralLike(url) || !reads.includes(url.text)) continue;
      const member = name.slice(symbol.length + 1);
      let used = false;
      for (const src of [loader.body, ...deps.map(d => d.initializer)]) {
        visit(src, v => { if (ts.isPropertyAccessExpression(v) && v.name.text === member) used = true; });
      }
      if (!used) continue;
      const support = dependencies(req, module.defs, new Set([name, ...module.imports.keys()]));
      const transport = req.expression.expression.getText();
      methods.push(member + ': (() => {const ' + transport + ' = {get:(path,config) => ({path,query:config?.params ?? {}})};\n' +
        support.map(d => d.getText().replace(/^export\s+/, '')).join('\n') + '\nreturn ' + fn.getText() + '})()');
      paths.add(url.text);
    }
    if (!methods.length) return;
    const body = [...bindings].filter(([name]) => referenced.has(name)).map(([name, field]) =>
      'const ' + name + ' = __body[' + JSON.stringify(field.key) + ']');
    const code = [...body, 'const ' + alias + ' = {' + methods.join(',') + '}',
      ...deps.map(d => 'const ' + d.getText()), 'return (' + loader.getText() + ')()'].join(';\n');
    if (Object.hasOwn(lookups, slot)) throw new Error('Ambiguous editor lookup ' + slot);
    lookups[slot] = {code: ts.transpileModule(code, {compilerOptions: {target: ts.ScriptTarget.ES2022}}).outputText,
      paths: [...paths], source: api.evidence(element), adapterFile: module.relative,
      records: n.right.getText().includes('?.records')};
  });
  return lookups;
}
