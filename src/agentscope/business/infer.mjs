// Trace a documented create/read pair through a Vue editor without running it.
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import { configuration } from './configuration.mjs';
import { referenceLookups } from './lookups.mjs';
import { linkEditor } from './editor.mjs';

const ignored = new Set(['node_modules', 'dist', 'build', 'target', 'coverage', 'vendor']);

function* sources(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.') || ignored.has(entry.name) || entry.isSymbolicLink()) continue;
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* sources(file);
    else if (/\.(?:vue|js|ts|mjs)$/.test(file) && !file.endsWith('.d.ts')) yield file;
  }
}

function walk(node, visit) {
  if (!node) return;
  visit(node);
  ts.forEachChild(node, child => { walk(child, visit); });
}

function unique(items, label) {
  if (items.length !== 1) throw new Error(`Expected one ${label}, found ${items.length}`);
  return items[0];
}

function returned(fn) {
  if (!ts.isBlock(fn.body)) return fn.body;
  const returns = fn.body.statements.filter(ts.isReturnStatement);
  return returns.length === 1 ? returns[0].expression : undefined;
}

function unwrap(node) {
  while (node && (ts.isAwaitExpression(node) || ts.isParenthesizedExpression(node))) node = node.expression;
  return node;
}

// Restrict aliases to visible preceding declarations in this block or a parent.
function initializer(name, use) {
  for (let n = use.parent; n && !ts.isSourceFile(n); n = n.parent) {
    if (!ts.isBlock(n)) continue;
    for (const statement of n.statements) {
      if (statement.pos >= use.pos) break;
      if (!ts.isVariableStatement(statement) || !(statement.declarationList.flags & ts.NodeFlags.Const)) continue;
      for (const d of statement.declarationList.declarations) {
        if (d.name.getText() === name && d.end <= use.pos) return d.initializer;
      }
    }
  }
}

function resolve(node) {
  node = unwrap(node);
  if (!node || !ts.isIdentifier(node)) return node;
  const value = initializer(node.text, node);
  return value ? resolve(value) : node;
}

function routeOf(url) {
  if (!url) return;
  if (ts.isStringLiteralLike(url)) return url.text;
  if (ts.isTemplateExpression(url)) return url.head.text + url.templateSpans.map(s => '{}' + s.literal.text).join('');
}

function adapters(files, contracts, api) {
  const found = { save: [], detail: [] };
  for (const file of files) {
    if (!fs.readFileSync(file, 'utf8').includes(contracts.save.route)) continue;
    const module = api.load(file);
    for (const [name, fn] of module.defs) {
      const req = unwrap(returned(fn));
      if (!req || !ts.isCallExpression(req) || !ts.isPropertyAccessExpression(req.expression)) continue;
      for (const role of Object.keys(found)) {
        const op = contracts[role];
        if (req.expression.name.text.toUpperCase() !== op.method ||
            routeOf(req.arguments[0]) !== op.route.replace(/\{[^{}]+\}/g, '{}')) continue;
        const local = req.expression.expression.getText();
        const imp = module.ast.statements.find(s => ts.isImportDeclaration(s) && s.importClause?.name?.text === local);
        if (!imp) continue;
        const transport = api.transportEvidence(imp.moduleSpecifier.text, module.real, imp);
        found[role].push({ module, name, req, client: imp.moduleSpecifier.text, transport });
      }
    }
  }
  return found;
}

function callsFrom(module, choices, api) {
  const targets = new Map();
  for (const role of Object.keys(choices)) {
    for (const adapter of choices[role]) {
      const [symbol, ...members] = adapter.name.split('.');
      for (const [alias, [from, imported]] of module.imports) {
        if (symbol !== imported || api.localModule(from, module.real).real !== adapter.module.real) continue;
        targets.set([alias, ...members].join('.'), { role, adapter });
      }
    }
  }
  return targets;
}

// Prove that the GET uses the preceding POST's return, including the common
// "wrapped.data || wrapped" client convention. Unrelated reads do not qualify.
function receiptLink(arg, saveCall) {
  const value = resolve(arg);
  if (!value) return;
  if (value === saveCall) return { direct: true };
  if (ts.isPropertyAccessExpression(value) && resolve(value.expression) === saveCall) {
    return { property: value.name.text };
  }
  if (ts.isBinaryExpression(value) && ['||', '??'].includes(value.operatorToken.getText())) {
    const left = receiptLink(value.left, saveCall), right = receiptLink(value.right, saveCall);
    if (left && right && (left.property === right.property || right.direct)) return left;
  }
}

function buttonBindings(module, name) {
  const refs = [];
  function visit(node) {
    for (const attr of node.props ?? []) {
      if (attr.name !== 'on' || !attr.exp) continue;
      const handler = attr.exp.loc.source.trim();
      if (handler === name || handler.startsWith(name + '(')) {
        refs.push({ file: module.relative, start: attr.loc.start.line, end: attr.loc.end.line, code: attr.loc.source });
      }
    }
    for (const child of node.children ?? []) if (typeof child === 'object') visit(child);
  }
  if (module.template) visit(module.template);
  return refs;
}

function editors(files, choices, api) {
  const matches = [];
  const symbols = choices.save.map(a => a.name.split('.')[0]);
  for (const file of files.filter(f => f.endsWith('.vue'))) {
    const raw = fs.readFileSync(file, 'utf8');
    if (!symbols.some(s => raw.includes(s))) continue;
    const page = api.load(file);
    const targets = callsFrom(page, choices, api);
    if (!targets.size) continue;
    for (const [name, fn] of page.defs) {
      const calls = [];
      walk(fn.body, n => {
        if (ts.isCallExpression(n) && targets.has(n.expression.getText())) {
          calls.push({ ...targets.get(n.expression.getText()), node: n });
        }
      });
      for (const save of calls.filter(c => c.role === 'save')) {
        for (const detail of calls.filter(c => c.role === 'detail' && c.node.pos > save.node.end)) {
          const link = receiptLink(detail.node.arguments[0], save.node);
          if (link) matches.push({page, name, fn, save, detail, link, buttons: buttonBindings(page, name)});
        }
      }
    }
  }
  return matches;
}

function payloadOf(editor) {
  const { page, save } = editor;
  let value = resolve(save.node.arguments[0]);
  if (value && ts.isCallExpression(value)) {
    const builder = page.defs.get(value.expression.getText());
    if (builder) value = resolve(returned(builder));
  }
  if (!value || !ts.isObjectLiteralExpression(value)) throw new Error('Cannot trace the saved object to a client payload');
  const result = new Map();
  for (const prop of value.properties) {
    if (ts.isPropertyAssignment(prop) || ts.isShorthandPropertyAssignment(prop)) {
      result.set(prop.name.text, { expression: resolve(prop.initializer ?? prop.name), prop });
    }
  }
  return result;
}

function rowSource(expression) {
  if (ts.isCallExpression(expression) && ts.isPropertyAccessExpression(expression.expression) &&
      expression.expression.name.text === 'map') return expression.expression.expression.getText();
  if (ts.isIdentifier(expression) || ts.isPropertyAccessExpression(expression)) return expression.getText();
}

function rowCalculators(editor, collection) {
  const calculators = new Map(), visited = new Set();
  function search(name, path) {
    if (visited.has(name)) return;
    visited.add(name);
    const fn = editor.page.defs.get(name);
    walk(fn.body, node => {
      if (!ts.isCallExpression(node)) return;
      if (name === editor.name && node.pos >= editor.save.node.pos) return;
      const member = node.expression;
      if (ts.isPropertyAccessExpression(member) && member.expression.getText() === collection &&
          ['map', 'forEach'].includes(member.name.text)) {
        const callback = node.arguments[0];
        if (callback && ts.isIdentifier(callback)) calculators.set(callback.text, [...path, node]);
        if (callback && ts.isArrowFunction(callback) && callback.parameters.length) {
          const row = callback.parameters[0].name.getText();
          walk(callback.body, c => {
            if (ts.isCallExpression(c) && c.arguments.length === 1 && c.arguments[0].getText() === row) {
              calculators.set(c.expression.getText(), [...path, c]);
            }
          });
        }
      }
      // Only follow local invocations which precede the create in its handler.
      if (ts.isIdentifier(member) && editor.page.defs.has(member.text)) search(member.text, [...path, node]);
    });
  }
  search(editor.name, []);
  return calculators;
}

function rowReads(node, row, reads) {
  if (!node || typeof node !== 'object') return;
  if (node.call && node.args.some(a => a.variable === row)) {
    throw new Error('Helper consumes the whole row; its prior field writes require review');
  }
  if (node.of?.variable === row && node.property) reads.add(node.property);
  for (const child of Object.values(node)) rowReads(child, row, reads);
}

function inferredRules(editor, contract, payload, api) {
  const bodyParam = editor.save.adapter.req.arguments[1];
  if (!bodyParam || !ts.isIdentifier(bodyParam)) throw new Error('Expected a named API body parameter');
  const rules = [], omitted = [], traces = [], calculations = new Map();
  for (const [key, fields] of Object.entries(contract.rows)) {
    const binding = payload.get(key);
    if (!binding?.expression) continue;
    const collection = rowSource(binding.expression);
    if (!collection) continue;
    const claimed = new Set();
    for (const [name, callPath] of rowCalculators(editor, collection)) {
      const fn = editor.page.defs.get(name);
      if (!fn || fn.parameters.length !== 1 || !ts.isBlock(fn.body)) continue;
      const row = fn.parameters[0].name.getText();
      const derived = new Set();
      for (const statement of fn.body.statements) {
        if (!ts.isExpressionStatement(statement) || !ts.isBinaryExpression(statement.expression)) continue;
        const { left, operatorToken, right } = statement.expression;
        if (operatorToken.kind !== ts.SyntaxKind.EqualsToken || !ts.isPropertyAccessExpression(left) ||
            left.expression.getText() !== row || !fields.includes(left.name.text)) continue;
        // Constants and clears are initialization, not calculations.
        if (!ts.isCallExpression(right) && !ts.isBinaryExpression(right) &&
            !ts.isConditionalExpression(right) && !ts.isPropertyAccessExpression(right)) continue;
        calculations.set(key + ':' + name, {key, name, callPath});
        const field = left.name.text;
        const reads = new Set();
        try {
          const fnPlan = api.analyze(editor.page, name, left.getText());
          const missing = fnPlan.dependencies.find(d => !derived.has(d));
          if (missing) throw new Error('Depends on an unresolved field: ' + missing);
          for (const step of fnPlan.steps) rowReads(step.return ?? step.value, row, reads);
        } catch (err) {
          omitted.push({ each: key, owner: name, field, reason: err.message, evidence: api.evidence(statement) });
          continue;
        }
        if (claimed.has(field)) throw new Error('Conflicting calculations for ' + key + '.' + field);
        rules.push({ each: bodyParam.text + '.' + key, source: editor.page.relative + '#' + name, assignment: left.getText() });
        traces.push({field, collection, array: key, reads: [...reads],
          inputs: [...reads].filter(key => !claimed.has(key)),
          derivedInputs: [...reads].filter(key => claimed.has(key)), payload: api.evidence(binding.prop),
          callPath: callPath.map(api.evidence), formula: api.evidence(statement)});
        claimed.add(field);
        derived.add(left.getText());
      }
    }
  }
  if (!calculations.size) throw new Error('No row calculations linked to the saved payload');
  return { rules, omitted, traces, calculations: [...calculations.values()] };
}

function headerDefaults(editor, payload, api) {
  const defaults = [];
  for (const [section, binding] of payload) {
    const expr = binding.expression;
    const obj = ts.isCallExpression(expr) ? expr.arguments[0] : expr;
    const source = obj && ts.isObjectLiteralExpression(obj)
      ? obj.properties.find(ts.isSpreadAssignment)?.expression
      : obj;
    if (!source || !ts.isIdentifier(source)) continue;
    const add = (object, clearsNumbers = false) => {
      if (!object || !ts.isObjectLiteralExpression(object)) return;
      for (const p of object.properties) {
        if (ts.isPropertyAssignment(p) && (p.initializer.kind === ts.SyntaxKind.NullKeyword ||
            (clearsNumbers && ts.isNumericLiteral(p.initializer)))) {
          defaults.push({ section, field: p.name.text, evidence: api.evidence(p) });
        }
      }
    };
    for (const s of editor.page.ast.statements.filter(ts.isVariableStatement)) {
      const init = s.declarationList.declarations.find(d => d.name.getText() === source.text)?.initializer;
      if (!init) continue;
      const object = ts.isCallExpression(init) ? init.arguments[0] : init;
      add(object);
    }
    walk(editor.page.ast, n => {
      if (!ts.isCallExpression(n) || n.expression.getText() !== 'Object.assign' ||
          n.arguments[0]?.getText() !== source.text) return;
      const arg = n.arguments[1];
      if (!arg) return;
      if (ts.isObjectLiteralExpression(arg)) add(arg);
      if (!ts.isCallExpression(arg)) return;
      const link = editor.page.imports.get(arg.expression.getText());
      if (!link) return;
      const cleaner = api.localModule(link[0], editor.page.real).defs.get(link[1])?.getText() ?? '';
      add(arg.arguments[0], /typeof\s+\w+\s*===\s*['"]number['"]/.test(cleaner) && /return null\b/.test(cleaner));
    });
  }
  return defaults;
}

// A cleanup rule may write null only to the object passed to that call.
function nullableRelations(page, payload, api) {
  const fields = [], active = new Set();
  function scan(n, path) {
    if (!n) return;
    if (ts.isBinaryExpression(n) && n.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
        ts.isPropertyAccessExpression(n.left)) {
      scan(n.right, [...path, n.left.name.text]);
      return;
    }
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression)) {
      const name = n.expression.text, rules = n.arguments[1], link = page.imports.get(name);
      if (link && rules && ts.isArrayLiteralExpression(rules)) {
        const helper = api.localModule(link[0], page.real).defs.get(link[1]);
        if (helper && /\[idField\]\s*=\s*null\b/.test(helper.getText())) {
          for (const rule of rules.elements.filter(ts.isObjectLiteralExpression)) {
            const key = rule.properties.find(p => p.name?.getText() === 'idField');
            if (key?.initializer && ts.isStringLiteralLike(key.initializer)) {
              fields.push({path: [...path, key.initializer.text], evidence: api.evidence(key)});
            }
          }
        }
      }
      const fn = page.defs.get(name);
      if (fn && !active.has(fn)) {
        active.add(fn);
        scan(fn.body,path);
        active.delete(fn);
      }
    }
    ts.forEachChild(n, child => { scan(child, path); });
  }
  for (const [key, binding] of payload) scan(binding.expression, [key]);
  return fields;
}

function editorProgram(editor, payload, calculations, api, contract) {
  const bindings = new Map();
  for (const [key, {expression}] of payload) {
    const value = ts.isCallExpression(expression) ? expression.arguments[0] : expression;
    const spread = value && ts.isObjectLiteralExpression(value) && value.properties.find(ts.isSpreadAssignment);
    const name = spread ? spread.expression.getText() : rowSource(expression);
    if (name && /^\w+(?:\.value)?$/.test(name)) {
      bindings.set(name.split('.')[0], {key, ref: name.endsWith('.value')});
    }
  }
  const hooks = new Map();
  for (const {callPath} of calculations) {
    if (callPath.length > 1) {
      const call = callPath[0];
      if (!ts.isIdentifier(call.expression) || call.arguments.length) throw new Error('Pre-save callback needs external parameters');
      hooks.set(call.expression.text,call.pos);
    }
  }
  const raw = resolve(editor.save.node.arguments[0]);
  const body = ts.isCallExpression(raw) ? {name: raw.expression.getText()} : {expression: raw};
  const argument = editor.save.adapter.req.arguments[1].getText();
  const guards = [], remaining = [], after = [];
  let configured, headerGuard;
  const fn = editor.fn.body.statements.find(ts.isTryStatement)?.tryBlock ?? editor.fn.body;
  for (const stmt of fn.statements) {
    if (stmt.end >= editor.save.node.pos) break;
    if (ts.isIfStatement(stmt) || ts.isForStatement(stmt)) {
      const predicate = ts.isIfStatement(stmt) && stmt.expression;
      const call = predicate && ts.isPrefixUnaryExpression(predicate) && predicate.operand;
      const localValidator = call && ts.isCallExpression(call) && editor.page.defs.has(call.expression.getText()) &&
        editor.page.defs.get(call.expression.getText()).getText().includes('message.error(');
      const dependsOnLocal = predicate && [...fn.statements].some(s => ts.isVariableStatement(s) &&
        s.end <= stmt.pos && s.declarationList.declarations.some(d => predicate.getText().includes(d.name.getText())));
      const prior = predicate && ts.isBinaryExpression(predicate) &&
        ts.isPropertyAccessExpression(predicate.left) && fn.statements.find(s => ts.isVariableStatement(s) && s.end <= stmt.pos &&
        s.declarationList.declarations.length === 1 && s.declarationList.declarations.some(d =>
          ts.isIdentifier(d.name) &&
          predicate.left.expression.getText() === d.name.text && predicate.left.name.text === 'length' &&
          d.initializer && ts.isCallExpression(d.initializer) &&
          d.initializer.arguments.some(a => ts.isStringLiteralLike(a) && a.text === 'save')));
      if (prior && stmt.getText().includes('message.error(')) {
        const collector = editor.page.defs.get(prior.declarationList.declarations[0].initializer.expression.getText());
        const header = [...bindings].find(([, slot]) => !slot.ref)?.[0];
        configured = collector && configuration(editor.page, api, contract, collector, headerGuard && header);
        if (configured) guards.push(prior, stmt);
        else remaining.push(api.evidence(stmt));
      } else if (!dependsOnLocal && (localValidator || /message\.(error|warning)\(/.test(stmt.getText()))) guards.push(stmt);
      else if (stmt.getText().includes('return')) remaining.push(api.evidence(stmt));
    } else if (ts.isExpressionStatement(stmt) && ts.isAwaitExpression(stmt.expression)) {
      const call = stmt.expression.expression;
      if (ts.isCallExpression(call) && !call.arguments.length && editor.page.defs.has(call.expression.getText()) &&
          [...hooks.values()].some(pos => pos < stmt.pos)) {
        after.push(call.expression.getText());
      } else if (ts.isCallExpression(call) && ts.isPropertyAccessExpression(call.expression) &&
          call.expression.name.text === 'validate' && !call.arguments.length) {
        headerGuard = stmt;
      } else remaining.push(api.evidence(stmt));
    }
  }
  if (headerGuard) {
    if (configured?.header) configured.header.index = guards.filter(g => g.end <= headerGuard.pos).length;
    else remaining.push(api.evidence(headerGuard));
  }
  let program;
  try {
    program = linkEditor(editor.page, api, {
      bindings,
      calculators: calculations.map(({key, name}) => ({key, name})),
      hooks: [...hooks.keys()], guards, body, argument, after, configured,
    });
  } catch (error) {
    if (configured) error.configurationRequired = true;
    throw error;
  }
  program.remaining = remaining;
  program.nullableRelations = nullableRelations(editor.page, payload, api);
  program.lookups = referenceLookups(editor.page, api, bindings, program.state, contract.reads);
  Object.assign(program.lookups, configured?.lookups);
  return program;
}

export function inferWorkflow(root, contract, api) {
  const files = [...sources(root)];
  const choices = adapters(files, contract, api);
  const candidates = editors(files, choices, api);
  const bound = candidates.filter(c => c.buttons.length);
  const editor = unique(bound.length ? bound : candidates, 'Vue create → detail flow');
  const payload = payloadOf(editor);
  const { rules, omitted, traces, calculations } = inferredRules(editor, contract, payload, api);
  let program, programError;
  try { program = editorProgram(editor, payload, calculations, api, contract); }
  catch (error) {
    if (error.configurationRequired) throw error;
    programError = error.message;
  }
  if (!program && !rules.length) throw new Error(programError || 'No executable row calculations');
  function adapter(role) {
    const { module, name, client } = editor[role].adapter;
    return { entry: module.relative + '#' + name, httpClient: client };
  }
  return {
    save: { ...adapter('save'), derive: rules }, detail: adapter('detail'),
    resultProperty: editor.link.property,
    defaults: headerDefaults(editor, payload, api),
    program, programError,
    report: { handler: editor.page.relative + '#' + editor.name, triggers: editor.buttons,
      createCall: api.evidence(editor.save.node), readCall: api.evidence(editor.detail.node),
      readArgument: api.evidence(resolve(editor.detail.node.arguments[0])),
      fields: traces, omitted },
  };
}
