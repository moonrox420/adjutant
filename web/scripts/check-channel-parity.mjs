import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const channels = new Set([
  "meta",
  "google_ads",
  "youtube",
  "tiktok",
  "linkedin",
  "microsoft",
  "reddit",
  "pinterest",
  "snapchat",
  "amazon_ads",
]);

export function violations(source, filename) {
  const tree = ts.createSourceFile(
    filename,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const aliases = new Set();
  function collect(node) {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer &&
      ts.isStringLiteral(node.initializer) &&
      channels.has(node.initializer.text)
    )
      aliases.add(node.name.text);
    ts.forEachChild(node, collect);
  }
  collect(tree);
  function channelValue(node) {
    if (ts.isStringLiteral(node)) return channels.has(node.text);
    if (ts.isIdentifier(node)) return aliases.has(node.text);
    if (ts.isArrayLiteralExpression(node))
      return node.elements.some(channelValue);
    if (ts.isParenthesizedExpression(node))
      return channelValue(node.expression);
    return false;
  }
  const comparisons = new Set([
    ts.SyntaxKind.EqualsEqualsToken,
    ts.SyntaxKind.EqualsEqualsEqualsToken,
    ts.SyntaxKind.ExclamationEqualsToken,
    ts.SyntaxKind.ExclamationEqualsEqualsToken,
    ts.SyntaxKind.InKeyword,
  ]);
  const errors = [];
  function visit(node) {
    const comparison =
      ts.isBinaryExpression(node) &&
      comparisons.has(node.operatorToken.kind) &&
      (channelValue(node.left) || channelValue(node.right));
    const match = ts.isCaseClause(node) && channelValue(node.expression);
    const membership =
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === "includes" &&
      channelValue(node.expression.expression);
    if (comparison || match || membership) {
      const { line } = tree.getLineAndCharacterOfPosition(node.getStart());
      errors.push(
        `${filename}:${line + 1}: channel-specific branching belongs in adapters/`,
      );
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  return errors;
}

function files(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory()
      ? files(path)
      : /\.(tsx?|jsx?)$/.test(entry.name)
        ? [path]
        : [];
  });
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const errors = ["app", "components", "lib"]
    .flatMap((directory) => files(join(root, directory)))
    .flatMap((path) =>
      violations(readFileSync(path, "utf8"), relative(root, path)),
    );
  errors.forEach((error) => console.error(error));
  if (errors.length) process.exitCode = 1;
  else
    console.log(
      "Console channel parity: no channel-specific branches above adapters.",
    );
}
