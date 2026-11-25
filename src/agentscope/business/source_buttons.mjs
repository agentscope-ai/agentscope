// Parse only the UTF-8 snapshot supplied by the host. Never load project modules.
import path from 'node:path';
import ts from 'typescript';
import { parse } from '@vue/compiler-sfc';

let input = '';
for await (const chunk of process.stdin.setEncoding('utf8')) input += chunk;
const files = JSON.parse(input);
const result = [];
const scripts = new Map();

function location(file, symbol, loc) {
  return { file, symbol, start: loc.start.line, end: loc.end.line, text: loc.source };
}

function directRequest(page, block, handler) {
  if (!block || block.src) return null;
  const script = ts.createSourceFile(
    page,
    block.content,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  if (script.parseDiagnostics.length) return null;
  const bindings = script.statements.filter(ts.isImportDeclaration).flatMap((statement) => {
    const names = statement.importClause?.namedBindings;
    if (
      !names ||
      !ts.isNamedImports(names) ||
      !ts.isStringLiteral(statement.moduleSpecifier)
    )
      return [];
    return names.elements
      .filter((item) => item.name.text === handler)
      .map((item) => [
        statement.moduleSpecifier.text,
        (item.propertyName || item.name).text,
      ]);
  });
  if (bindings.length !== 1 || !bindings[0][0].startsWith('.')) return null;
  const [source, symbol] = bindings[0];
  const base = path.posix.join(path.posix.dirname(page), source);
  const matches = [base, base + '.ts', base + '.js'].filter((file) =>
    Object.hasOwn(files, file),
  );
  if (matches.length !== 1) return null;
  const file = matches[0];
  if (!scripts.has(file))
    scripts.set(
      file,
      ts.createSourceFile(
        file,
        files[file],
        ts.ScriptTarget.Latest,
        true,
        ts.ScriptKind.TS,
      ),
    );
  const ast = scripts.get(file);
  const defs = ast.statements.filter(
    (node) =>
      ts.isFunctionDeclaration(node) &&
      node.name?.text === symbol &&
      node.modifiers?.some((mod) => mod.kind === ts.SyntaxKind.ExportKeyword),
  );
  if (ast.parseDiagnostics.length || defs.length !== 1) return null;
  const body = defs[0].body?.statements;
  if (body?.length !== 1 || !ts.isReturnStatement(body[0])) return null;
  const call = body[0].expression;
  if (!call || !ts.isCallExpression(call) || call.expression.getText(ast) !== 'fetch')
    return null;
  const [url, options] = call.arguments;
  if (
    !url ||
    !ts.isStringLiteral(url) ||
    !url.text.startsWith('/') ||
    !options ||
    !ts.isObjectLiteralExpression(options)
  )
    return null;
  if (
    !options.properties.every(
      (p) => ts.isPropertyAssignment(p) && !ts.isComputedPropertyName(p.name),
    )
  )
    return null;
  const methods = options.properties.filter((p) => p.name.text === 'method');
  if (methods.length !== 1 || !ts.isStringLiteral(methods[0].initializer)) return null;
  return {
    method: methods[0].initializer.text.toUpperCase(),
    route: url.text,
    evidence: {
      file,
      symbol,
      start: ast.getLineAndCharacterOfPosition(call.getStart()).line + 1,
      end: ast.getLineAndCharacterOfPosition(call.end - 1).line + 1,
      text: call.getText(ast),
    },
  };
}

for (const [file, raw] of Object.entries(files)) {
  if (!file.endsWith('.vue')) continue;
  const { descriptor, errors } = parse(raw, { filename: file });
  if (errors.length) throw new Error('Invalid Vue source in the authorized snapshot');
  function visit(node, conditions) {
    const props = node.props || [];
    const guards = [
      ...conditions,
      ...props.filter((p) => ['if', 'show', 'else-if', 'else'].includes(p.name)),
    ];
    if (node.tag === 'button') {
      const event = props.find((p) => p.name === 'on' && p.arg?.content === 'click');
      const label = node.children.every((n) => n.type === 2)
        ? node.children
            .map((n) => n.content)
            .join('')
            .trim()
        : '';
      if (event && label) {
        const handler = event.exp?.content.trim() || '';
        result.push({
          name: label,
          page: file,
          handler,
          binding: location(file, handler, node.loc),
          conditions: guards.map((g) => ({
            expression: g.loc.source,
            evidence: location(file, handler, g.loc),
          })),
          request: directRequest(file, descriptor.scriptSetup, handler),
        });
      }
    }
    for (const child of node.children || []) visit(child, guards);
  }
  if (descriptor.template?.ast) visit(descriptor.template.ast, []);
}
process.stdout.write(JSON.stringify({ buttons: result }));
