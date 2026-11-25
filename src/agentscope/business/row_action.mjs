// Trace a scoped Vue grid row into the path arguments of a host-bound GET.
// Only parse application source. Never import or execute the application.
import ts from 'typescript';

function walk(node, visit) {
  if (visit(node) !== false)
    ts.forEachChild(node, (child) => {
      walk(child, visit);
    });
}

function location(page, prop) {
  return {
    file: page.relative,
    start: prop.loc.start.line,
    end: prop.loc.end.line,
    code: prop.loc.source,
  };
}

function gridAction(page, handler) {
  const matches = [];
  function visit(node, grid, rows) {
    const props = node.props ?? [];
    const data = props.find(
      (p) =>
        p.name === 'bind' &&
        ['row-data', 'data-source', 'items', 'dataSource'].includes(p.arg?.content),
    );
    if (data) {
      grid = data;
      rows = [];
    }
    const slot = props.find((p) => p.name === 'slot');
    if (slot) {
      rows = [];
      const binding = slot.exp?.content.trim().match(/^\{([^{}]+)\}$/);
      for (const member of binding?.[1].split(',') ?? []) {
        const parts = member.trim().split(/\s*:\s*/);
        if (['row', 'item', 'record'].includes(parts[0])) rows.push(parts.at(-1));
      }
    }
    for (const p of props) {
      if (!grid || p.name !== 'on' || p.arg?.content !== 'click') continue;
      const call = p.exp?.content.trim().match(/^(\w+)\(\s*(\w+)\s*\)$/);
      if (call?.[1] === handler && rows.includes(call[2])) matches.push({ grid, click: p });
    }
    for (const child of node.children ?? []) visit(child, grid, rows);
  }
  if (page.template) visit(page.template, null, []);
  if (!matches.length || new Set(matches.map((m) => m.grid)).size !== 1)
    throw new Error('缺少唯一的列表行插槽与点击处理函数的源码关联');
  return matches[0];
}

function adapter(call, page, api) {
  const callee = call.expression;
  const name = ts.isPropertyAccessExpression(callee) ? callee.expression : callee;
  if (!ts.isIdentifier(name)) return;
  const link = page.imports.get(name.text);
  if (!link) return;
  const symbol = ts.isPropertyAccessExpression(callee) ? link[1] + '.' + callee.name.text : link[1];
  const target = api.localModule(link[0], page.real);
  const fn = target.defs.get(symbol);
  if (!fn) return;
  let definition = fn;
  while (!ts.isSourceFile(definition.parent)) definition = definition.parent;
  if (!definition.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)) return;
  const body = fn.body;
  const req = ts.isBlock(body)
    ? body.statements.length === 1 && ts.isReturnStatement(body.statements[0])
      ? body.statements[0].expression
      : undefined
    : body;
  if (
    !req ||
    !ts.isCallExpression(req) ||
    !ts.isPropertyAccessExpression(req.expression) ||
    req.expression.name.text !== 'get'
  )
    return;
  const client = req.expression.expression.getText();
  const imp = target.ast.statements.find(
    (s) => ts.isImportDeclaration(s) && s.importClause?.name?.text === client,
  );
  if (!imp) return;
  const url = req.arguments[0];
  if (!url || !(ts.isStringLiteralLike(url) || ts.isTemplateExpression(url))) return;
  const spans = url.templateSpans ?? [];
  const shape = spans.length
    ? url.head.text + spans.map((s) => '{}' + s.literal.text).join('')
    : url.text;
  return {
    shape,
    req,
    fn,
    spans,
    transport: () => api.transportEvidence(imp.moduleSpecifier.text, target.real, imp),
  };
}

// Follow the value assigned to the grid backwards. Loading metadata in the
// same function does not make that GET the source of the selected rows.
function gridReads(ast, grid, declaration) {
  const calls = new Map();
  const seen = new Set();
  function collect(node, binding) {
    if (!node || seen.has(node)) return;
    seen.add(node);
    if (ts.isIdentifier(node)) {
      const d = declaration(node);
      if (
        d &&
        ts.isVariableDeclaration(d) &&
        d.parent.flags & ts.NodeFlags.Const &&
        d.end <= node.pos
      )
        collect(d.initializer, binding);
    } else if (ts.isCallExpression(node)) {
      calls.set(node, binding);
      node.arguments.forEach((arg) => collect(arg, binding));
    } else if (
      ts.isPropertyAccessExpression(node) ||
      ts.isParenthesizedExpression(node) ||
      ts.isAwaitExpression(node)
    ) {
      collect(node.expression, binding);
    } else if (ts.isBinaryExpression(node)) {
      collect(node.left, binding);
      collect(node.right, binding);
    } else if (ts.isObjectLiteralExpression(node)) {
      node.properties.forEach((p) => collect(p.initializer, binding));
    }
  }
  walk(ast, (n) => {
    if (
      ts.isBinaryExpression(n) &&
      n.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
      [grid, grid + '.value'].includes(n.left.getText())
    ) {
      collect(n.right, n);
    }
    if (
      !ts.isMethodDeclaration(n) ||
      n.name.getText() !== 'loadPage' ||
      !ts.isObjectLiteralExpression(n.parent)
    )
      return;
    const bound = n.parent.properties.some(
      (p) =>
        (ts.isShorthandPropertyAssignment(p) && p.name.text === grid) ||
        (ts.isPropertyAssignment(p) && p.initializer.getText() === grid),
    );
    const returns = n.body?.statements.filter(ts.isReturnStatement);
    const record = returns?.length === 1 && returns[0].expression;
    if (bound && record && ts.isObjectLiteralExpression(record)) {
      const rows = record.properties.find(
        (p) => ts.isPropertyAssignment(p) && p.name.getText() === 'records',
      );
      if (rows) collect(rows.initializer, rows);
    }
  });
  return calls;
}

export function inferRowAction(page, spec, api) {
  const { grid, click } = gridAction(page, spec.handler);
  const checkerOptions = { noLib: true, noResolve: true };
  const host = ts.createCompilerHost(checkerOptions);
  const file = 'row-page.ts';
  const ast = ts.createSourceFile(file, page.ast.text, ts.ScriptTarget.Latest, true);
  host.getSourceFile = (name) => (name === file ? ast : undefined);
  const checker = ts.createProgram([file], checkerOptions, host).getTypeChecker();
  const declaration = (n) => checker.getSymbolAtLocation(n)?.declarations?.[0];
  const cite = (n) => ({ ...api.evidence(n), file: page.relative });
  const handler = ast.statements
    .flatMap((s) => (ts.isVariableStatement(s) ? [...s.declarationList.declarations] : [s]))
    .find((s) => s.name?.getText() === spec.handler);
  const entry = handler && (ts.isFunctionDeclaration(handler) ? handler : handler.initializer);
  if (!entry?.parameters?.length) throw new Error('缺少行操作处理函数');
  function linked(call) {
    const target = call.expression;
    const local = ts.isPropertyAccessExpression(target) ? target.expression : target;
    return ts.isIdentifier(local) && ts.isImportSpecifier(declaration(local) ?? {})
      ? adapter(call, page, api)
      : undefined;
  }
  const gridInputs = gridReads(ast, grid.exp.content, declaration);
  const loads = [...gridInputs.keys()].filter((call) => linked(call)?.shape === spec.listRoute);
  if (loads.length !== 1) throw new Error('缺少从已绑定列表 GET 到当前表格的唯一加载入口');
  const list = linked(loads[0]);
  const results = [];
  const expected = spec.detailRoute.replace(/\{[^{}]+\}/g, '{}');
  const names = [...spec.detailRoute.matchAll(/\{([^{}]+)\}/g)].map((m) => m[1]);

  // Values in this environment describe lineage, not the contents of any row.
  function source(n, env) {
    if (!n) return;
    if (ts.isParenthesizedExpression(n) || ts.isAsExpression(n)) return source(n.expression, env);
    if (ts.isIdentifier(n)) {
      const def = declaration(n);
      if (env.has(def)) return env.get(def);
      if (
        def &&
        ts.isVariableDeclaration(def) &&
        def.parent.flags & ts.NodeFlags.Const &&
        def.end <= n.pos
      )
        return source(def.initializer, env);
    } else if (ts.isPropertyAccessExpression(n)) {
      const base = source(n.expression, env);
      if (base && !base.cast)
        return { fields: [...base.fields, n.name.text], evidence: [...base.evidence, cite(n)] };
    } else if (
      ts.isCallExpression(n) &&
      ['String', 'Number'].includes(n.expression.getText()) &&
      !declaration(n.expression) &&
      n.arguments.length === 1
    ) {
      const base = source(n.arguments[0], env);
      if (base && !base.cast)
        return { ...base, cast: n.expression.getText(), evidence: [...base.evidence, cite(n)] };
    }
  }

  function trace(fn, env, trail, chain) {
    if (chain.includes(fn)) return;
    walk(fn.body, (n) => {
      if (ts.isFunctionLike(n)) return false; // Enter closures only from an observed call.
      if (
        ts.isBinaryExpression(n) &&
        n.operatorToken.kind >= ts.SyntaxKind.FirstAssignment &&
        n.operatorToken.kind <= ts.SyntaxKind.LastAssignment &&
        source(n.left, env)
      )
        throw new Error('所选行在处理函数中被改写，缺少稳定的字段映射');
      if (!ts.isCallExpression(n)) return;
      const detail = linked(n);
      if (detail?.shape === expected) {
        // A supplied request config may change the query, tenant, or headers.
        // Only omit an optional forwarded config when the client omits it too.
        const config = detail.req.arguments[1];
        if (config) {
          const pos = detail.fn.parameters.findIndex((p) => p.name.getText() === config.getText());
          const fallback = detail.fn.parameters[pos]?.initializer;
          if (
            pos < 0 ||
            n.arguments.length > pos ||
            !fallback ||
            !ts.isObjectLiteralExpression(fallback) ||
            fallback.properties.length
          )
            throw new Error('详情依赖额外的请求配置或查询字段，缺少完整参数映射');
        }
        const fields = {};
        const evidence = [];
        for (const [i, span] of detail.spans.entries()) {
          const pos = detail.fn.parameters.findIndex(
            (p) => p.name.getText() === span.expression.getText(),
          );
          const origin = source(n.arguments[pos], env);
          if (!origin?.fields.length) throw new Error('缺少从所选行到详情路径参数的字段证据');
          fields[names[i]] = { path: origin.fields, cast: origin.cast ?? null };
          evidence.push(...origin.evidence);
        }
        results.push({
          fields,
          evidence: [
            ...trail,
            ...evidence,
            cite(n),
            api.evidence(detail.req),
            ...detail.transport(),
          ],
        });
        return;
      }
      if (!ts.isIdentifier(n.expression)) return;
      const def = declaration(n.expression);
      const child = def && (ts.isFunctionDeclaration(def) ? def : def.initializer);
      if (!child?.body || !child.parameters) return;
      const args = n.arguments.map((arg) => source(arg, env));
      const closure = child.pos >= fn.pos && child.end <= fn.end;
      if (!closure && !args.some(Boolean)) return;
      const next = new Map(env);
      child.parameters.forEach((p, i) => next.set(p, args[i]));
      trace(child, next, [...trail, cite(n)], [...chain, fn]);
    });
  }
  trace(entry, new Map([[entry.parameters[0], { fields: [], evidence: [] }]]), [], []);
  if (!results.length || new Set(results.map((r) => JSON.stringify(r.fields))).size !== 1)
    throw new Error('缺少唯一的列表行到详情 GET 的字段映射');
  const evidence = [
    api.evidence(list.req),
    ...list.transport(),
    cite(loads[0]),
    cite(gridInputs.get(loads[0])),
    location(page, grid),
    location(page, click),
    ...results[0].evidence,
  ];
  return {
    fields: results[0].fields,
    evidence: [...new Map(evidence.map((e) => [JSON.stringify(e), e])).values()],
  };
}
