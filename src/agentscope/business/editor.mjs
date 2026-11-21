// Link only the source declarations used by the document editor. The result
// runs in a separate QuickJS heap with JSON inputs and no host APIs.
import ts from 'typescript';
import { composedBindings } from './configuration.mjs';

const globals = new Set(('undefined NaN Infinity Math Number String Boolean Array Object Set Map JSON RegExp Promise Date Symbol console Error TypeError parseFloat parseInt isNaN isFinite').split(' '));
const vue = new Set(['ref', 'reactive', 'computed', 'nextTick']);

function index(module) {
  const file = 'editor.ts';
  const ast = ts.createSourceFile(file, module.ast.text, ts.ScriptTarget.Latest, true);
  const options = { noLib: true, noResolve: true };
  const host = ts.createCompilerHost(options);
  host.getSourceFile = name => name === file ? ast : undefined;
  const checker = ts.createProgram([file], options, host).getTypeChecker();
  const definitions = new Map();
  for (const s of ast.statements) {
    if (ts.isFunctionDeclaration(s) && s.name) definitions.set(s.name.text, s);
    if (ts.isVariableStatement(s)) {
      for (const d of s.declarationList.declarations) {
        if (ts.isIdentifier(d.name)) definitions.set(d.name.text, d);
      }
    }
  }
  return { module, ast, checker, definitions, used: new Map(), links: new Map(), refs: new Set() };
}

function reference(node) {
  const p = node.parent;
  return !(p.name === node && !ts.isShorthandPropertyAssignment(p)) &&
    !(ts.isBindingElement(p) && p.propertyName === node);
}

export function linkEditor(page, api, {bindings, calculators, hooks, guards, body, argument, after = [], configured}) {
  const units = new Map();
  function unit(module) {
    if (!units.has(module.real)) units.set(module.real, index(module));
    return units.get(module.real);
  }
  const main = unit(page);
  const sources = [];
  const state = [];
  const replacements = new Map();
  const groups = new Map();
  const compositions = composedBindings({...page, ast:main.ast});
  const initialize = [];

  function includeHook(u, hookName, fields) {
    const name = hookName + '__' + fields.join('_');
    if (u.used.has(name)) return name;
    const fn = u.definitions.get(hookName);
    const last = fn?.body?.statements.find(ts.isReturnStatement)?.expression;
    if (!last || !ts.isObjectLiteralExpression(last)) throw new Error('Cannot slice hook ' + hookName);
    const selected = last.properties.filter(prop => fields.includes(prop.name?.getText()));
    if (selected.length !== fields.length) throw new Error('Missing hook results from ' + hookName);
    u.definitions.set(name,fn);
    const locals = new Map();
    const collect = (binding, statement) => {
      if (ts.isIdentifier(binding)) locals.set(u.checker.getSymbolAtLocation(binding), statement);
      else if (ts.isObjectBindingPattern(binding) || ts.isArrayBindingPattern(binding)) {
        for (const element of binding.elements.filter(ts.isBindingElement)) collect(element.name, statement);
      }
    };
    for (const stmt of fn.body.statements) {
      if (ts.isVariableStatement(stmt)) for (const d of stmt.declarationList.declarations) collect(d.name,stmt);
      if (ts.isFunctionDeclaration(stmt)) collect(stmt.name,stmt);
    }
    const needed = new Set();
    function follow(n) {
      if (ts.isIdentifier(n) && reference(n)) {
        const sym = ts.isShorthandPropertyAssignment(n.parent)
          ? u.checker.getShorthandAssignmentValueSymbol(n.parent) : u.checker.getSymbolAtLocation(n);
        const stmt = locals.get(sym);
        if (stmt && !needed.has(stmt)) { needed.add(stmt); follow(stmt); }
      }
      ts.forEachChild(n,follow);
    }
    selected.forEach(follow);
    const body = fn.body.statements.filter(stmt => needed.has(stmt));
    for (const stmt of body) { scan(u,stmt); emitSource(u,stmt); }
    selected.forEach(prop => scan(u,prop));
    u.used.set(name, 'function ' + name + '(' + fn.parameters.map(p => p.getText()).join(',') + ') {\n' +
      body.map(stmt => stmt.getText()).join('\n') + '\nreturn {' + selected.map(p => p.getText()).join(',') + '};\n}');
    return name;
  }

  function includeComposition(name) {
    const slot = compositions.get(name);
    const group = slot && configured?.groups.get(slot.decl.getStart());
    if (!group) return false;
    if (groups.has(group)) return true;
    groups.set(group,true);
    const [from, original] = group.link;
    const dependency = unit(api.localModule(from,page.real));
    const fields = [...new Set([...group.members.values(), ...(group.load ?? [])])];
    const entry = includeHook(dependency,original,fields);
    const alias = '__hook' + groups.size;
    main.links.set(alias,{dependency, original:entry});
    for (const arg of slot.call.arguments) scan(main,arg);
    const instance = alias + 'Value';
    main.definitions.set(instance,slot.decl);
    main.used.set(instance, 'const ' + instance + ' = ' + alias + '(' + slot.call.arguments.map(a => a.getText()).join(',') + ')');
    for (const [local, member] of group.members) {
      main.definitions.set(local,slot.decl);
      main.used.set(local,'const ' + local + ' = ' + instance + '.' + member);
    }
    for (const load of group.load ?? []) initialize.push(instance + '.' + load + '()');
    emitSource(main,slot.decl);
    return true;
  }

  // Specialize a no-argument lifecycle call's destructured boolean defaults.
  // Compare symbols, so a nested callback which shadows an option is intact.
  for (const name of after) {
    const fn = main.definitions.get(name);
    const param = fn?.parameters?.[0];
    if (fn?.parameters.length !== 1 || param.initializer?.getText() !== '{}') continue;
    const disabled = new Set();
    for (const s of fn.body.statements.filter(ts.isVariableStatement)) {
      if (!(s.declarationList.flags & ts.NodeFlags.Const)) continue;
      for (const d of s.declarationList.declarations) {
        if (d.initializer?.getText() === param.name.getText() && ts.isObjectBindingPattern(d.name)) {
          for (const binding of d.name.elements) {
            if (binding.initializer?.kind === ts.SyntaxKind.FalseKeyword) disabled.add(main.checker.getSymbolAtLocation(binding.name));
          }
        }
      }
    }
    function isFalse(n) {
      if (ts.isIdentifier(n)) return disabled.has(main.checker.getSymbolAtLocation(n));
      return ts.isBinaryExpression(n) && n.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken && isFalse(n.left);
    }
    function visit(n) {
      if (ts.isIfStatement(n) && isFalse(n.expression)) replacements.set(n, n.elseStatement);
      else if (ts.isConditionalExpression(n) && isFalse(n.condition)) replacements.set(n, n.whenFalse);
      else ts.forEachChild(n, visit);
    }
    visit(fn.body);
  }
  function emitSource(u, n) {
    const ast = u.ast;
    sources.push({ file: u.module.relative, start: ast.getLineAndCharacterOfPosition(n.getStart()).line + 1,
      end: ast.getLineAndCharacterOfPosition(n.end - 1).line + 1, code: n.getText() });
  }
  function scan(u, n) {
    if (replacements.has(n)) {
      if (replacements.get(n)) scan(u, replacements.get(n));
      return;
    }
    if (ts.isTypeNode(n)) return;
    if (n.kind === ts.SyntaxKind.ImportKeyword) throw new Error('Runtime imports require the client');
    if (ts.isIdentifier(n) && reference(n)) {
      const sym = ts.isShorthandPropertyAssignment(n.parent)
        ? u.checker.getShorthandAssignmentValueSymbol(n.parent) : u.checker.getSymbolAtLocation(n);
      const decl = sym?.valueDeclaration ?? sym?.declarations?.[0];
      if (u === main && decl && ts.isBindingElement(decl) && includeComposition(n.text)) return;
      if (decl && u.definitions.get(n.text) === decl) include(u, n.text);
      else if (decl && ts.isImportSpecifier(decl)) {
        const from = decl.parent.parent.parent.moduleSpecifier.text;
        const original = (decl.propertyName ?? decl.name).text;
        const transport = configured?.imports.get(u.module.real)?.bindings?.get(n.text);
        if (transport) {
          u.links.set(n.text,{expression:transport});
        } else if (from === 'vue' && vue.has(original)) {
          u.links.set(n.text, { builtin: original });
        } else if (['ant-design-vue'].includes(from) && original === 'message') {
          u.links.set(n.text, { builtin: 'message' });
        } else {
          if (!from.startsWith('.') && !from.startsWith('@/')) throw new Error('Needs external module ' + from);
          const dependency = unit(api.localModule(from, u.module.real));
          u.links.set(n.text, {dependency, original});
          include(dependency, original);
        }
      } else if (decl && (ts.isNamespaceImport(decl) || ts.isImportClause(decl))) {
        throw new Error('Unsupported editor import ' + n.text);
      } else if (!decl && !globals.has(n.text) && !vue.has(n.text)) {
        throw new Error('Needs client binding ' + n.text);
      }
    }
    ts.forEachChild(n, child => { scan(u, child); });
  }
  function headerSeed(u, name, init, value) {
    if (!init || !ts.isCallExpression(init) || init.expression.getText() !== 'reactive' ||
        !init.arguments[0] || !ts.isObjectLiteralExpression(init.arguments[0])) return value;
    const literal = n => ts.isStringLiteralLike(n) || ts.isNumericLiteral(n) ||
      [ts.SyntaxKind.NullKeyword, ts.SyntaxKind.FalseKeyword, ts.SyntaxKind.TrueKeyword].includes(n.kind);
    const staticObject = obj => '{' + obj.properties.filter(p => ts.isPropertyAssignment(p) &&
      !ts.isComputedPropertyName(p.name) && literal(p.initializer)).map(p => p.getText()).join(',') + '}';
    const sections = [...bindings].filter(([, slot]) => slot.ref).map(([key]) => key);
    const resets = [];
    for (const fn of u.definitions.values()) {
      if (!fn.body || !ts.isBlock(fn.body) || !sections.length) continue;
      const lines = fn.body.statements;
      if (!sections.every(key => lines.some(s => ts.isExpressionStatement(s) && ts.isBinaryExpression(s.expression) &&
          s.expression.operatorToken.kind === ts.SyntaxKind.EqualsToken && s.expression.left.getText() === key + '.value' &&
          ts.isArrayLiteralExpression(s.expression.right) && !s.expression.right.elements.length))) continue;
      const target = lines.find(s => ts.isExpressionStatement(s) && ts.isCallExpression(s.expression) &&
        s.expression.expression.getText() === 'Object.assign' && s.expression.arguments[0]?.getText() === name);
      if (target) resets.push([fn, target]);
    }
    if (resets.length > 1) throw new Error('Ambiguous reset of ' + name);
    if (!resets.length) return 'Object.assign(' + staticObject(init.arguments[0]) + ',' + value + ')';
    const [fn, target] = resets[0];
    const reset = target.expression.arguments[1];
    const obj = ts.isCallExpression(reset) ? reset.arguments[0] : reset;
    if (!obj || !ts.isObjectLiteralExpression(obj)) throw new Error('Unresolved editor defaults');
    const steps = ['const ' + name + ' = ' + staticObject(init.arguments[0])];
    let seed = staticObject(obj);
    if (ts.isCallExpression(reset)) {
      if (reset.arguments.length !== 1) throw new Error('Unresolved reset arguments');
      scan(u, reset.expression);
      seed = reset.expression.getText() + '(' + seed + ')';
    }
    steps.push('Object.assign(' + name + ',' + seed + ')');
    emitSource(u, target);
    for (const stmt of fn.body.statements) {
      if (stmt.pos < target.end || !ts.isIfStatement(stmt) || stmt.elseStatement ||
          !ts.isExpressionStatement(stmt.thenStatement)) continue;
      const assign = stmt.thenStatement.expression;
      if (!ts.isBinaryExpression(assign) || assign.operatorToken.kind !== ts.SyntaxKind.EqualsToken ||
          !ts.isPropertyAccessExpression(assign.left) || assign.left.expression.getText() !== name || !literal(assign.right)) continue;
      // The fallback can read only this header and its own fields.
      let supported = true;
      function check(n) {
        if (ts.isCallExpression(n) || (ts.isIdentifier(n) && reference(n) && n.text !== name)) supported = false;
        ts.forEachChild(n, check);
      }
      check(stmt.expression);
      if (supported) { steps.push(stmt.getText()); emitSource(u, stmt); }
    }
    steps.push('return Object.assign(' + name + ',' + value + ')');
    return '(() => {' + steps.join(';\n') + '})()';
  }
  function include(u, name) {
    if (u.used.has(name)) return;
    const decl = u.definitions.get(name);
    if (!decl) throw new Error('Missing editor dependency ' + u.module.relative + '#' + name);
    u.used.set(name, '');
    emitSource(u, decl);
    const slot = u === main && bindings.get(name);
    if (slot) {
      const value = '__body[' + JSON.stringify(slot.key) + ']';
      const seed = slot.ref ? '{value:' + value + '}' : headerSeed(u, name, decl.initializer, value);
      u.used.set(name, 'const ' + name + ' = ' + seed);
      return;
    }
    const init = decl.initializer;
    if (init && ts.isCallExpression(init) && init.expression.getText() === 'ref') {
      if (!name.endsWith('Ref')) {
        state.push(name);
        u.refs.add(name);
      }
    }
    const stored = configured?.imports.get(u.module.real)?.refs?.get(name);
    if (stored) {
      u.used.set(name,'const ' + name + ' = ref(__requiredState(' + JSON.stringify(stored) + '))');
      return;
    }
    scan(u, decl);
    let code;
    if (ts.isFunctionDeclaration(decl)) {
      code = decl.getText();
      for (const [node, replacement] of [...replacements].reverse()) {
        if (node.getSourceFile() === decl.getSourceFile() && node.pos >= decl.pos && node.end <= decl.end) {
          code = code.slice(0, node.getStart() - decl.getStart()) + (replacement?.getText() ?? ';') +
            code.slice(node.end - decl.getStart());
        }
      }
      code = code.replace(/^export\s+/, '');
    }
    else {
      let value = init?.getText() ?? 'undefined';
      if (u.refs.has(name)) value = '__reference(' + JSON.stringify(name) + ', () => ' + value + ')';
      code = 'const ' + name + ' = ' + value;
    }
    u.used.set(name, code);
  }
  for (const {name} of calculators) include(main, name);
  for (const hook of hooks) include(main, hook);
  for (const name of after) include(main, name);
  if (body.name) include(main, body.name);
  else scan(main, matchingNode(body.expression));
  // Guard statements use nodes from the checker's source file, preserving
  // lexical scope without importing the rest of the form or its HTTP calls.
  function matchingNode(original) {
    let match;
    function visit(n) {
      if (n.pos === original.pos && n.end === original.end && n.kind === original.kind) match = n;
      else if (n.pos <= original.pos && n.end >= original.end) ts.forEachChild(n, visit);
    }
    visit(main.ast);
    if (!match) throw new Error('Cannot locate editor guard');
    return match;
  }
  for (const g of guards) scan(main, matchingNode(g));
  const header = configured?.header;
  if (header) {
    include(main,header.fields);
    for (const [index, hook] of header.hooks.entries()) {
      const dependency = unit(hook.file);
      const original = includeHook(dependency,hook.name,[hook.member]);
      main.links.set('__headerHook' + index,{dependency,original});
    }
  }
  const watches = [];
  for (const statement of main.ast.statements) {
    const call = ts.isExpressionStatement(statement) && statement.expression;
    if (!call || !ts.isCallExpression(call) || call.expression.getText() !== 'watch') continue;
    const [source, callback] = call.arguments;
    if (!source || !ts.isArrowFunction(source) || !ts.isArrayLiteralExpression(source.body) ||
        !callback || !ts.isArrowFunction(callback) || !ts.isCallExpression(callback.body) ||
        callback.body.arguments.length) continue;
    const deps = source.body.elements;
    if (!deps.length || !deps.every(d => ts.isPropertyAccessExpression(d) && bindings.has(d.expression.getText()))) continue;
    if (!deps.some(d => sources.some(s => s.code.includes(d.getText() + ' =')))) continue;
    const name = callback.body.expression.getText();
    if (!main.definitions.has(name)) continue;
    include(main, name);
    watches.push({ name, values: source.body.getText(), evidence: api.evidence(page.ast.statements.find(s => s.pos === statement.pos)) });
  }
  const blocks = [], emitted = new Map(), visiting = new Set();
  function emit(u) {
    if (emitted.has(u)) return emitted.get(u);
    if (visiting.has(u)) throw new Error('Circular module dependency in editor program');
    visiting.add(u);
    const imports = [];
    for (const [alias, entry] of u.links) {
      if (entry.dependency) imports.push('const ' + alias + ' = ' + emit(entry.dependency) + '[' + JSON.stringify(entry.original) + ']');
      else if (entry.expression) imports.push('const ' + alias + ' = ' + entry.expression);
      else if (alias !== entry.builtin) imports.push('const ' + alias + ' = __vue.' + entry.builtin);
    }
    const decls = [...u.used].sort(([a], [b]) => u.definitions.get(a).pos - u.definitions.get(b).pos).map(([,code]) => code);
    let result = 'return {' + [...u.used.keys()].join(',') + '}';
    if (u === main) {
      const statements = calculators.map(({name, key}) => '__body[' + JSON.stringify(key) + '].forEach(row => ' + name + '(row))');
      watches.forEach(({name, values}, i) => {
        statements.unshift('const __before' + i + ' = ' + values);
        statements.push('if (' + values + '.some((v,i) => !Object.is(v,__before' + i + '[i]))) ' + name + '()');
      });
      if (initialize.length) statements.push('await Promise.all([' + initialize.join(',') + '])');
      if (guards.length) {
        const checks = guards.map(g => g.getText());
        if (header) checks.splice(header.index,0,
          'for (const cfg of ' + header.fields + '.value) {\n' +
          'const props = {cfg, form:' + header.form + ', module:' + header.module + ', formEditable:true};\n' +
          'const {resolvedDisabled} = __headerHook0(props);\n' +
          'const {rules} = __headerHook1(props,{resolvedDisabled});\n' +
          'const value = props.form[cfg.key];\n' +
          'for (const rule of rules.value) {\n' +
          'if (rule.required && (value == null || value === "" || (Array.isArray(value) && !value.length))) throw new Error(rule.message);\n' +
          '}\n}');
        statements.push('if ((() => {' + checks.join('\n') + '\nreturn true})() !== true) throw new Error("Client rejected the document")');
      }
      statements.push(...hooks.map(name => name + '()'));
      statements.push(...after.map(name => 'await ' + name + '()'));
      result = statements.join(';\n') + ';\nreturn ' + (body.name ? body.name + '()' : body.expression.getText());
    }
    const id = '__unit' + blocks.length;
    blocks.push('const ' + id + ' = (' + (u === main ? 'async ' : '') + '() => {\n' + [...imports, ...decls, result].join(';\n') + '\n})();');
    visiting.delete(u);
    emitted.set(u, id);
    return id;
  }
  const result = emit(main);
  const js = ts.transpileModule(blocks.join('\n') + '\nreturn ' + result, {
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None}
  }).outputText;
  return {argument, code: js, sources:[...sources,...(configured?.sources ?? [])], state:[...new Set([...state,...Object.keys(configured?.lookups ?? {})])], calculators, hooks, after, watches,
    guards: guards.map(n => api.evidence(n)), builder: body.name ?? 'inline'};
}
