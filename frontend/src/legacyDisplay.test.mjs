import assert from 'node:assert/strict';
import test from 'node:test';
import { displayInspection, displayStandard, legacyText } from './legacyDisplay.ts';

test('legacy result text is translated without changing numbers or operators', () => {
  assert.equal(legacyText('높이: 기준보다 0.10 mm 초과입니다.'), 'Height: The value is 0.10 mm above the limit.');
  assert.equal(legacyText('깊이 ≤ 0.30 mm AND 직경 < 0.80 mm'), 'Depth ≤ 0.30 mm AND Diameter < 0.80 mm');
  assert.equal(legacyText('높이: 경계값과 같지만 등호가 허용되지 않습니다.'), 'Height: The value equals the boundary, but equality is not permitted.');
  assert.equal(legacyText('높이: 값 누락 또는 단위 불일치'), 'Height: Missing value or unit mismatch');
  assert.equal(legacyText('해당 없음'), 'Not applicable');
  assert.equal(legacyText('결함이 관찰되지 않았습니다.'), 'No defect was observed.');
  assert.equal(legacyText('Unchanged text'), 'Unchanged text');
});

test('display projection preserves original records, identities, decisions and artifacts', () => {
  const standard = {
    id: 'demo-cover-v1', provenance: 'SYNTHETIC', product_name: '가상 커버',
    rules: [{ area: '외측면', item: '버 높이', source: '가상 기준 1항 / 회사 승인 기준 아님', conditions: [{ label: '높이', limit: '0.20' }] }],
  };
  const state = {
    standard, inputs: { inspector: '서현우' }, report: { approved: true }, events: [],
    item_results: [{ area: '외측면', item: '버 높이', criteria: '높이 < 0.20 mm', measured: '높이 0.30 mm', explanation: '높이: 기준보다 0.10 mm 초과입니다.', source: standard.rules[0].source, final_result: 'FAIL', review_reason: '확인함' }],
  };
  const original = structuredClone(state);
  const displayed = displayInspection(state);
  assert.equal(displayed.standard.product_name, 'Synthetic cover');
  assert.equal(displayed.item_results[0].item, 'Burr height');
  assert.equal(displayed.item_results[0].final_result, 'FAIL');
  assert.equal(displayed.item_results[0].review_reason, '확인함');
  assert.equal(displayed.inputs, state.inputs);
  assert.equal(displayed.report, state.report);
  assert.deepEqual(state, original);
  const custom = { ...standard, id: 'customer-standard' };
  assert.equal(displayStandard(custom), custom);
  const internal = { ...standard, provenance: 'INTERNAL' };
  assert.equal(displayStandard(internal), internal);
});

test('customer terms are shown in the reader language while evidence keeps the original', async () => {
  const { setGlossary, displayInspection } = await import('./legacyDisplay.ts');
  setGlossary({ '咬边': { en: 'Undercut', ko: '언더컷' }, '깊이': { en: 'Depth', zh: '深度' } });
  const state = { standard: { id: 'x', provenance: 'SYNTHETIC', product_name: 'P', rules: [] },
    item_results: [{ area: 'A', item: '咬边', criteria: '깊이 ≤ 0.3 mm', measured: '깊이 0.20 mm', explanation: '', source: 'HC-QC-07 / 咬边' }] };
  const shown = displayInspection(state, 'en');
  assert.equal(shown.item_results[0].item, 'Undercut');
  assert.equal(shown.item_results[0].criteria, 'Depth ≤ 0.3 mm');
  assert.equal(shown.item_results[0].source, 'HC-QC-07 / 咬边');
  assert.equal(displayInspection(state, 'zh').item_results[0].measured, '深度 0.20 mm');
  setGlossary({});
});
