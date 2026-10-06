import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const componentExports = {};
const source = readFileSync(new URL('../src/components/PositionSizer.tsx', import.meta.url), 'utf8');
runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
}).outputText, { exports: componentExports, require: createRequire(import.meta.url) });

test('position sizing starts with the stock-specific stop and correct lot calculation', () => {
    const markup = renderToStaticMarkup(React.createElement(componentExports.default, {
        stock: { 代码: '000001', 名称: '测试股票', 现价: 10 },
        onClose() {},
    }));
    assert.match(markup, /value="9\.5"/);
    assert.match(markup, />20000 <span/);
    assert.match(markup, /200,000/);
});
