import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { korean, translate } from './translations.ts';
import { displayInspection, displayStandard } from './legacyDisplay.ts';

test('catalog supports both languages and preserves numeric evidence', () => {
  for (const [english, translated] of Object.entries(korean)) {
    assert.equal(translate(english, 'en'), english);
    assert.equal(translate(english, 'ko'), translated);
    assert.ok(translated.trim());
  }
  assert.equal(translate('Height: The value is 0.10 mm above the limit.', 'ko'), '높이: 기준보다 0.10 mm 초과입니다.');
  assert.equal(translate('Depth ≤ 0.30 mm AND Diameter < 0.80 mm', 'ko'), '깊이 ≤ 0.30 mm 그리고 직경 < 0.80 mm');
  assert.equal(translate('Depth: Missing value or unit mismatch', 'ko'), '깊이: 값 누락 또는 단위 불일치');
  assert.equal(translate('Height: The value equals the boundary, but equality is not permitted.', 'ko'), '높이: 경계값과 같지만 등호가 허용되지 않습니다.');
  assert.equal(translate('Unknown server detail', 'ko'), 'Unknown server detail');
});

test('switching demo projections preserves stored data and user-entered content', () => {
  const standard = {
    id: 'demo-cover-en-v1', provenance: 'SYNTHETIC', product_name: 'Synthetic cover',
    rules: [{ area: 'Outer surface', item: 'Burr height', source: 'Synthetic criterion 1 / Not company-approved', conditions: [{ label: 'Height', limit: '0.20' }] }],
  };
  const state = {
    standard, inputs: { inspector: 'Jane Kim', lot: 'LOT-003', observations: { burr: { measurements: { height: '0.30' } } } },
    report: { approved: true }, events: [{ actor: 'Jane Kim', reason: 'Original reason' }],
    item_results: [{ area: 'Outer surface', item: 'Burr height', criteria: 'Height < 0.20 mm', measured: 'Height 0.30 mm', explanation: 'Height: The value is 0.10 mm above the limit.', source: standard.rules[0].source, final_result: 'FAIL', review_reason: 'Original reason' }],
  };
  const original = structuredClone(state);
  const localized = displayInspection(state, 'ko');
  assert.equal(localized.standard.product_name, '가상 커버');
  assert.equal(localized.item_results[0].measured, '높이 0.30 mm');
  assert.equal(localized.item_results[0].final_result, 'FAIL');
  assert.equal(localized.item_results[0].review_reason, 'Original reason');
  assert.equal(localized.inputs, state.inputs);
  assert.equal(localized.report, state.report);
  assert.equal(localized.events, state.events);
  assert.deepEqual(displayInspection(state, 'en'), original);
  assert.deepEqual(state, original);
  const custom = { ...standard, id: 'custom-standard' };
  assert.equal(displayStandard(custom, 'ko'), custom);
});

test('workspace renders in the saved language with an accessible toggle', async () => {
  const server = await createServer({ root: new URL('..', import.meta.url).pathname, server: { middlewareMode: true }, logLevel: 'silent' });
  const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  try {
    const { LanguageProvider } = await server.ssrLoadModule('/src/language.tsx');
    const { default: App } = await server.ssrLoadModule('/src/App.tsx');
    const { default: AuthGate } = await server.ssrLoadModule('/src/AuthGate.tsx');
    const render = component => renderToStaticMarkup(createElement(LanguageProvider, null, component));
    const app = createElement(App, { user: { id: 'demo', username: 'demo', display_name: 'Jane Kim' }, onLogout: async () => {}, loggingOut: false });
    for (const language of ['en', 'ko']) {
      Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => language } });
      const html = render(app);
      assert.ok(html.includes(language === 'ko' ? '기준서 읽기' : 'Read specs'));
      assert.ok(html.includes(language === 'ko' ? '영어로 전환' : 'Switch to Korean'));
      assert.ok(html.includes('Jane Kim'));
      assert.ok(render(createElement(AuthGate)).includes(language === 'ko' ? '로그인 상태 확인 중...' : 'Checking your session...'));
    }
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('Storage unavailable'); } });
    assert.ok(render(app).includes('Read specs'));
  } finally {
    if (storage) Object.defineProperty(globalThis, 'localStorage', storage);
    else delete globalThis.localStorage;
    await server.close();
  }
});

test('chinese catalog covers every visible screen and dynamic messages', async () => {
  const { chinese } = await import('./chinese.ts');
  const { readFileSync } = await import('node:fs');
  const screens = ['App.tsx', 'Home.tsx', 'Tasks.tsx', 'FormIntake.tsx', 'StandardWorkspace.tsx', 'Programs.tsx', 'AISettings.tsx', 'AuthGate.tsx', 'docLanguage.tsx']
    .map(name => readFileSync(new URL(name, import.meta.url), 'utf8')).join('\n');
  for (const key of Object.keys(korean)) {
    if (!screens.includes(`"${key}"`) && !screens.includes(`'${key}'`)) continue;
    assert.ok(Object.hasOwn(chinese, key), key);
    assert.equal(translate(key, 'zh'), chinese[key]);
  }
  assert.equal(translate('Height: The value is 0.10 mm above the limit.', 'zh'), '高度：超出限值 0.10 mm。');
  assert.equal(translate('Depth ≤ 0.30 mm AND Diameter < 0.80 mm', 'zh'), '深度 ≤ 0.30 mm 且 直径 < 0.80 mm');
  assert.equal(translate('No matching item: "x"', 'zh'), '没有匹配的项目："x"');
});
