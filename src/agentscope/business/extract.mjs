// Parse source text only. Client modules and their imports are never executed.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import ts from 'typescript';
import { parse as parseVue } from '@vue/compiler-sfc';
import { inferWorkflow } from './infer.mjs';
import { inferRowAction } from './row_action.mjs';

const [rootArg, entryFile, entryName, optionsJSON] = process.argv.slice(2);
const root = fs.realpathSync(rootArg);
const options = JSON.parse(optionsJSON ?? '{}');
const modules = new Map();
let functions;
const hashes = {};
let endpoint;

function fail(node, message) {
  const loc = evidence(node);
  throw new Error(`${loc.file}:${loc.start}: ${message}`);
}

function evidence(node) {
  const source = node.getSourceFile();
  const start = node.getStart(source);
  return {
    file: source.fileName,
    start: source.getLineAndCharacterOfPosition(start).line + 1,
    end: source.getLineAndCharacterOfPosition(node.end - 1).line + 1,
    code: source.text.slice(start, node.end),
  };
}

function localModule(from, importer) {
  let aliasRoot = path.dirname(importer);
  while (aliasRoot !== root && path.basename(aliasRoot) !== 'src') aliasRoot = path.dirname(aliasRoot);
  const base = from.startsWith('@/') ? path.join(aliasRoot, from.slice(2)) : path.resolve(path.dirname(importer), from);
  const found = [base, ...['.ts', '.js', '.mjs', '/index.ts', '/index.js'].map(ext => base + ext)]
    .find(candidate => fs.statSync(candidate, { throwIfNoEntry: false })?.isFile());
  if (!found) throw new Error('Missing local module ' + from);
  return load(found);
}

function transportEvidence(from, importer, node) {
  if (from === 'axios') return [evidence(node)];
  if (!(from.startsWith('@/') || from.startsWith('.'))) fail(node, 'Expected a local Axios wrapper');
  const wrapper = localModule(from, importer);
  const statements = wrapper.ast.statements;
  const exported = statements.find(s => ts.isExportAssignment(s) && !s.isExportEquals);
  const factory = statements.filter(s => ts.isVariableStatement(s) && s.declarationList.flags & ts.NodeFlags.Const)
    .flatMap(s => [...s.declarationList.declarations]).find(d => d.name.getText() === exported?.expression.getText());
  const axiosImport = statements.find(s => ts.isImportDeclaration(s) && s.moduleSpecifier.text === 'axios');
  const alias = axiosImport?.importClause?.name?.text;
  if (!alias || !factory?.initializer || !ts.isCallExpression(factory.initializer) ||
      factory.initializer.expression.getText() !== alias + '.create') fail(node, 'Expected a default-exported axios.create instance');
  return [node, axiosImport, factory, exported].map(evidence);
}

function load(file) {
  const real = fs.realpathSync(file);
  const relative = path.relative(root, real);
  if (relative.startsWith('..' + path.sep) || relative === '..' || path.isAbsolute(relative)) {
    throw new Error('Import outside the selected source directory');
  }
  if (modules.has(relative)) return modules.get(relative);
  const raw = fs.readFileSync(real, 'utf8');
  hashes[relative] = createHash('sha256').update(raw).digest('hex');
  let text = raw;
  let template;
  if (relative.endsWith('.vue')) {
    const { descriptor, errors } = parseVue(raw, { filename: relative });
    if (errors.length) throw new Error(`${relative}: ${errors[0]}`);
    const blocks = [descriptor.script, descriptor.scriptSetup].filter(Boolean);
    if (blocks.length !== 1 || blocks[0].src) throw new Error(`${relative}: expected one inline script block`);
    const block = blocks[0];
    template = descriptor.template?.ast;
    text = raw.slice(0, block.loc.start.offset).replace(/[^\r\n]/g, ' ') + block.content;
  }
  const ast = ts.createSourceFile(relative, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  if (ast.parseDiagnostics.length) throw new Error(`${relative}: invalid script syntax`);
  const defs = new Map(), imports = new Map(), clients = new Map(), constants = new Map();
  for (const statement of ast.statements) {
    if (ts.isFunctionDeclaration(statement) && statement.name && statement.body) {
      defs.set(statement.name.text, statement);
    } else if (ts.isVariableStatement(statement) && statement.declarationList.flags & ts.NodeFlags.Const) {
      for (const d of statement.declarationList.declarations) {
        if (!ts.isIdentifier(d.name) || !d.initializer) continue;
        if (ts.isArrowFunction(d.initializer)) defs.set(d.name.text, d.initializer);
        else if (ts.isObjectLiteralExpression(d.initializer)) {
          for (const prop of d.initializer.properties) {
            if (ts.isPropertyAssignment(prop) && ts.isIdentifier(prop.name) && ts.isArrowFunction(prop.initializer)) {
              defs.set(d.name.text + '.' + prop.name.text, prop.initializer);
            }
          }
        } else if (ts.isNumericLiteral(d.initializer) || ts.isStringLiteral(d.initializer)) constants.set(d.name.text, d);
      }
    } else if (ts.isImportDeclaration(statement)) {
      const from = statement.moduleSpecifier.text;
      const clause = statement.importClause;
      if ((from === 'axios' || from === options.httpClient) && clause?.name && !clause.isTypeOnly) clients.set(clause.name.text, statement);
      const named = clause?.namedBindings;
      if (!clause?.isTypeOnly && named && ts.isNamedImports(named) && (from.startsWith('.') || from.startsWith('@/'))) {
        for (const item of named.elements) {
          if (!item.isTypeOnly) imports.set(item.name.text, [from, (item.propertyName ?? item.name).text]);
        }
      }
    }
  }
  const module = { relative, real, ast, defs, imports, clients, constants, template };
  modules.set(relative, module);
  return module;
}

function lookup(module, name, node) {
  if (module.defs.has(name)) return [module, name];
  const link = module.imports.get(name);
  if (link) {
    const target = localModule(link[0], module.real);
    const def = target.defs.get(link[1]);
    const statement = def && (ts.isArrowFunction(def) ? def.parent.parent.parent : def);
    if (statement?.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword)) return [target, link[1]];
  }
  fail(node, 'Unresolved function ' + name);
}

function schemaFor(node) {
  if (!node) return {};
  if (ts.isParenthesizedTypeNode(node)) return schemaFor(node.type);
  if (ts.isArrayTypeNode(node)) return { type: 'array', items: schemaFor(node.elementType) };
  if (ts.isTypeLiteralNode(node)) {
    const properties = {}, required = [];
    for (const field of node.members) {
      if (!ts.isPropertySignature(field) || !ts.isIdentifier(field.name)) fail(field, 'Unsupported input field');
      properties[field.name.text] = schemaFor(field.type);
      if (!field.questionToken) required.push(field.name.text);
    }
    return { type: 'object', properties, required, additionalProperties: false };
  }
  const type = node.getText();
  if (['string', 'number', 'boolean'].includes(type)) return { type };
  fail(node, 'Unsupported input type ' + type + ' (use an inline object type)');
}

const binary = new Set(['+', '-', '*', '/', '**', '%', '===', '!==', '<', '<=', '>', '>=', '&&', '||', '??']);
const math = new Set(['round', 'floor', 'ceil', 'abs', 'min', 'max']);
const forbidden = new Set(['__proto__', 'constructor', 'prototype']);

function compile(module, name, action, stack = [], projection = '') {
  const id = module.relative + '#' + name + (projection ? ':' + projection : '');
  if (stack.includes(id)) throw new Error('Recursive call: ' + id);
  if (functions.has(id)) {
    if (functions.get(id).action !== action) throw new Error('Inconsistent call type: ' + id);
    return id;
  }
  const fn = module.defs.get(name);
  if (!fn) throw new Error('Missing function: ' + id);
  const scope = new Set();
  const params = [];
  const steps = [];
  const locals = new Map();
  const usedConstants = new Map();
  const dependencies = new Set();
  const writes = new Map();

  function expr(n, tail = false) {
    if (ts.isParenthesizedExpression(n) || ts.isAwaitExpression(n) || ts.isAsExpression(n)) return expr(n.expression, tail);
    if (!tail && ts.isCallExpression(n) && n.arguments.length === 1 &&
        ['Number', 'Number.isFinite'].includes(n.expression.getText()) && !scope.has('Number')) {
      return { numeric: n.expression.getText(), value: expr(n.arguments[0]) };
    }
    if (!tail && n.getText() === 'Number.EPSILON' && !scope.has('Number')) return { literal: Number.EPSILON };
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && !scope.has(n.expression.text)) {
      if (tail && module.clients.has(n.expression.text)) return request(n);
      const [target, symbol] = lookup(module, n.expression.text, n);
      return { call: compile(target, symbol, tail, [...stack, id]), args: n.arguments.map(a => expr(a)) };
    }
    if (tail) return request(n);
    if (ts.isStringLiteralLike(n)) return { literal: n.text };
    if (ts.isNumericLiteral(n)) return { literal: Number(n.text) };
    if ([ts.SyntaxKind.TrueKeyword, ts.SyntaxKind.FalseKeyword, ts.SyntaxKind.NullKeyword].includes(n.kind)) {
      return { literal: n.kind === ts.SyntaxKind.NullKeyword ? null : n.kind === ts.SyntaxKind.TrueKeyword };
    }
    if (ts.isIdentifier(n)) {
      if (scope.has(n.text)) return { variable: n.text };
      if (locals.has(n.text)) {
        const d = locals.get(n.text);
        locals.delete(n.text);
        const value = expr(d.initializer);
        steps.push({ assign: n.text, value, evidence: evidence(d) });
        scope.add(n.text);
        return { variable: n.text };
      }
      const constant = module.constants.get(n.text);
      if (constant) {
        usedConstants.set(n.text, evidence(constant));
        return expr(constant.initializer);
      }
    }
    if (ts.isPropertyAccessExpression(n) && !n.questionDotToken && !forbidden.has(n.name.text)) {
      if (writes.has(n.getText()) && writes.get(n.getText()) < n.pos) dependencies.add(n.getText());
      return { property: n.name.text, of: expr(n.expression) };
    }
    if (ts.isBinaryExpression(n) && binary.has(n.operatorToken.getText())) {
      return { op: n.operatorToken.getText(), left: expr(n.left), right: expr(n.right) };
    }
    if (ts.isPrefixUnaryExpression(n) && ['!', '-', '+'].includes(ts.tokenToString(n.operator))) {
      return { unary: ts.tokenToString(n.operator), value: expr(n.operand) };
    }
    if (ts.isConditionalExpression(n)) return { when: expr(n.condition), yes: expr(n.whenTrue), no: expr(n.whenFalse) };
    if (ts.isArrayLiteralExpression(n)) return { array: n.elements.map(el => expr(el)) };
    if (ts.isObjectLiteralExpression(n)) {
      const fields = Object.create(null);
      for (const p of n.properties) {
        if (!(ts.isPropertyAssignment(p) || ts.isShorthandPropertyAssignment(p)) ||
            !(ts.isIdentifier(p.name) || ts.isStringLiteral(p.name)) ||
            forbidden.has(p.name.text) || Object.hasOwn(fields, p.name.text)) fail(p, 'Unsupported object field');
        fields[p.name.text] = expr(p.initializer ?? p.name);
      }
      return { object: fields };
    }
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression) &&
        n.expression.expression.getText() === 'Math' && !scope.has('Math') && math.has(n.expression.name.text)) {
      return { math: n.expression.name.text, args: n.arguments.map(a => expr(a)) };
    }
    fail(n, 'Unsupported expression: ' + n.getText());
  }

  function request(n) {
    const access = n.expression;
    if (!ts.isCallExpression(n)) fail(n, 'Expected an Axios request');
    const clientName = ts.isPropertyAccessExpression(access) ? access.expression.getText() : access.getText();
    if (!module.clients.has(clientName) || scope.has(clientName)) {
      fail(n, 'Expected a return of an axios request or a local API function');
    }
    let method, url, data, query;
    if (ts.isIdentifier(access)) {
      const [config] = n.arguments;
      if (n.arguments.length !== 1 || !ts.isObjectLiteralExpression(config)) {
        fail(n, 'Expected a literal Axios request config');
      }
      const fields = new Map();
      for (const property of config.properties) {
        if ((!ts.isPropertyAssignment(property) && !ts.isShorthandPropertyAssignment(property)) ||
            !ts.isIdentifier(property.name) || !['url', 'method', 'params'].includes(property.name.text) ||
            fields.has(property.name.text)) fail(property, 'Unsupported Axios request config field');
        fields.set(property.name.text, ts.isPropertyAssignment(property) ? property.initializer : property.name);
      }
      const verb = fields.get('method');
      if (!verb || !ts.isStringLiteralLike(verb) || verb.text.toUpperCase() !== 'GET') {
        fail(config, 'Expected a literal GET method');
      }
      method = 'GET';
      url = fields.get('url');
      query = fields.get('params');
    } else {
      method = access.name.text.toUpperCase();
      [url, data] = n.arguments;
    }
    if (!['GET', 'DELETE', 'POST', 'PUT', 'PATCH'].includes(method)) fail(n, 'Unsupported HTTP method');
    if (!url || n.arguments.length > 2) fail(n, 'Unsupported HTTP arguments');
    let template;
    const bindings = [];
    const encodedSegments = [];
    if (ts.isStringLiteralLike(url)) template = url.text;
    else if (ts.isTemplateExpression(url)) {
      template = url.head.text;
      for (const span of url.templateSpans) {
        const e = span.expression;
        const encoded = ts.isCallExpression(e) && e.expression.getText() === 'encodeURIComponent' &&
          !scope.has('encodeURIComponent') && e.arguments.length === 1;
        if (!encoded && !ts.isIdentifier(e)) fail(e, 'Unsupported path expression');
        template += '{' + bindings.length + '}' + span.literal.text;
        bindings.push(expr(encoded ? e.arguments[0] : e));
        encodedSegments.push(encoded);
      }
    } else fail(url, 'Expected a literal API route');
    if (!/^\/(?:[A-Za-z0-9_~-][\w.~-]*|\{\d+\})(?:\/(?:[A-Za-z0-9_~-][\w.~-]*|\{\d+\}))*$/.test(template)) {
      fail(url, 'Expected a relative route of literal or parameter segments');
    }
    if (endpoint) fail(n, 'Expected exactly one HTTP operation');
    const imported = module.clients.get(clientName);
    endpoint = { method, template, evidence: evidence(n),
      transport: transportEvidence(imported.moduleSpecifier.text, module.real, imported) };
    const result = { request: true, bindings, encodedSegments };
    if (query) result.query = expr(query);
    if (data && ['GET', 'DELETE'].includes(method)) {
      if (ts.isIdentifier(data)) { result.config = expr(data); return result; }
      if (!ts.isObjectLiteralExpression(data) || data.properties.length !== 1 ||
          data.properties[0].name?.getText() !== 'params' || !ts.isPropertyAssignment(data.properties[0])) fail(data, 'Expected { params: ... }');
      result.query = expr(data.properties[0].initializer);
    } else if (data) result.body = expr(data);
    return result;
  }

  for (const p of fn.parameters) {
    if (!ts.isIdentifier(p.name) || p.dotDotDotToken || forbidden.has(p.name.text)) fail(p, 'Expected a named parameter');
    const key = p.name.text;
    const param = { name: key, evidence: evidence(p) };
    if (!stack.length) param.schema = schemaFor(p.type);
    if (p.initializer) param.default = expr(p.initializer);
    param.optional = !!(p.questionToken || p.initializer);
    params.push(param);
    scope.add(key);
  }
  function result(n) { steps.push({ return: expr(n, action), evidence: evidence(n) }); }
  if (projection) {
    if (!ts.isBlock(fn.body) || params.length !== 1 || !projection.startsWith(params[0].name + '.')) {
      fail(fn, 'Select a field assignment on the only input record');
    }
    const assignments = fn.body.statements.filter(s => ts.isExpressionStatement(s) &&
      ts.isBinaryExpression(s.expression) && s.expression.left.getText() === projection &&
      s.expression.operatorToken.kind === ts.SyntaxKind.EqualsToken);
    if (assignments.length !== 1) fail(fn, 'Expected one top-level assignment to ' + projection);
    const target = assignments[0];
    for (const s of fn.body.statements) {
      if (s === target) break;
      if (ts.isExpressionStatement(s) && ts.isBinaryExpression(s.expression) &&
          s.expression.operatorToken.kind === ts.SyntaxKind.EqualsToken) {
        writes.set(s.expression.left.getText(), s.end);
      }
      if (ts.isVariableStatement(s) && s.declarationList.flags & ts.NodeFlags.Const) {
        for (const d of s.declarationList.declarations) {
          if (ts.isIdentifier(d.name) && d.initializer) locals.set(d.name.text, d);
        }
      }
    }
    const formula = expr(target.expression.right);
    steps.push({ return: formula, evidence: evidence(target) });
  } else if (!ts.isBlock(fn.body)) result(fn.body);
  else for (const s of fn.body.statements) {
    if (steps.at(-1)?.return) fail(s, 'Unreachable statement');
    if (ts.isVariableStatement(s) && s.declarationList.flags & ts.NodeFlags.Const) {
      for (const d of s.declarationList.declarations) {
        if (!ts.isIdentifier(d.name) || !d.initializer || scope.has(d.name.text) || forbidden.has(d.name.text)) fail(d, 'Expected a new local constant');
        steps.push({ assign: d.name.text, value: expr(d.initializer), evidence: evidence(d) });
        scope.add(d.name.text);
      }
    } else if (ts.isIfStatement(s) && !s.elseStatement) {
      const rejection = ts.isBlock(s.thenStatement) && s.thenStatement.statements.length === 1 ? s.thenStatement.statements[0] : s.thenStatement;
      const e = rejection.expression;
      if (!action && ts.isReturnStatement(rejection) && rejection.expression) {
        steps.push({ earlyIf: expr(s.expression), value: expr(rejection.expression), evidence: evidence(s) });
        continue;
      }
      if (!ts.isThrowStatement(rejection) || !ts.isNewExpression(e) || e.expression.getText() !== 'Error' ||
          scope.has('Error') || e.arguments?.length !== 1 || !ts.isStringLiteral(e.arguments[0])) fail(s, 'Expected a validation guard that throws Error');
      steps.push({ rejectIf: expr(s.expression), message: e.arguments[0].text, evidence: evidence(s) });
    } else if (ts.isReturnStatement(s) && s.expression) result(s.expression);
    else fail(s, 'Unsupported statement: ' + ts.SyntaxKind[s.kind]);
  }
  if (!steps.at(-1)?.return) fail(fn, 'Function must return a value');
  functions.set(id, { action, params, steps, constants: [...usedConstants.values()], dependencies: [...dependencies] });
  return id;
}

function buildPlan(module, symbol) {
  endpoint = undefined;
  functions = new Map();
  const entry = compile(module, symbol, true);
  if (!endpoint) throw new Error('No HTTP operation found');
  const derived = new Set();
  const derivations = (options.derive ?? []).map(rule => {
    if (!/^\w+(\.\w+)*$/.test(rule.each) || rule.each.split('.').some(p => forbidden.has(p)) ||
        !/^\w+\.\w+$/.test(rule.assignment) || forbidden.has(rule.assignment.split('.')[1])) throw new Error('Invalid row or assignment path');
    const [file, name] = rule.source.split('#');
    const symbol = compile(load(path.resolve(root, file)), name, false, [], rule.assignment);
    const prefix = JSON.stringify([rule.each, rule.source]);
    for (const required of functions.get(symbol).dependencies) {
      if (!derived.has(prefix + required)) throw new Error(rule.assignment + ' requires deriving ' + required + ' first');
    }
    derived.add(prefix + rule.assignment);
    return { each: rule.each, field: rule.assignment.split('.').at(-1), symbol };
  });
  const used = new Set([...functions.keys()].map(id => id.slice(0, id.lastIndexOf('#'))));
  for (const ref of endpoint.transport) used.add(ref.file);
  return { entry, functions: Object.fromEntries(functions), endpoint, derivations,
    hashes: Object.fromEntries([...used].map(file => [file, hashes[file]])) };
}

function discover(dir, actions, diagnostics) {
  const skip = new Set(['node_modules', 'dist', 'build', 'coverage']);
  for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
    if (item.name.startsWith('.') || skip.has(item.name) || item.isSymbolicLink()) continue;
    const file = path.join(dir, item.name);
    if (item.isDirectory()) { discover(file, actions, diagnostics); continue; }
    if (!/\.(vue|js|ts|mjs)$/.test(file) || file.endsWith('.d.ts')) continue;
    let module;
    try { module = load(file); } catch (err) { diagnostics.push(err.message); continue; }
    for (const name of module.defs.keys()) {
      try {
        const plan = buildPlan(module, name);
        actions.push({ entry: plan.entry, endpoint: plan.endpoint, calls: Object.keys(plan.functions).filter(f => f !== plan.entry) });
      } catch (err) {
        diagnostics.push(module.relative + '#' + name + ': ' + err.message);
      }
    }
  }
}

try {
  if (entryFile === '--row-action') {
    const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
    const result = inferRowAction(load(path.resolve(root, spec.page)), spec, {localModule, evidence, transportEvidence});
    result.hashes = Object.fromEntries(result.evidence.map(e => [e.file, hashes[e.file]]));
    console.log(JSON.stringify(result));
  } else if (entryFile === '--infer') {
    const contract = JSON.parse(fs.readFileSync(0, 'utf8'));
    const analyze = (module, name, field) => {
      functions = new Map();
      const id = compile(module, name, false, [], field);
      return functions.get(id);
    };
    const inference = inferWorkflow(root, contract, {load, localModule, evidence, transportEvidence, analyze});
    inference.pageHash = hashes[inference.report.createCall.file];
    if (inference.program) {
      const files = new Set(inference.program.sources.map(s => s.file));
      for (const lookup of Object.values(inference.program.lookups)) files.add(lookup.adapterFile);
      inference.program.hashes = Object.fromEntries([...files].map(file => [file, hashes[file]]));
    }
    console.log(JSON.stringify(inference));
  } else if (entryFile === '--discover') {
    const actions = [], diagnostics = [];
    discover(root, actions, diagnostics);
    console.log(JSON.stringify({ actions, diagnostics }));
  } else {
    console.log(JSON.stringify(buildPlan(load(path.resolve(root, entryFile)), entryName)));
  }
} catch (err) {
  console.error(err.message);
  process.exitCode = 1;
}
