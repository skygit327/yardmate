import type { Inspection, Standard } from './types';
import { translate } from './translations.ts';
import type { Language } from './translations.ts';

const legacyLabels: Record<string, string> = {
  "가상 커버": "Synthetic cover",
  "외측면": "Outer surface",
  "버 높이": "Burr height",
  "표면 기공": "Surface porosity",
  "전체": "Overall",
  "크랙": "Crack",
  "체결부": "Fastening area",
  "표면 거칠기": "Surface roughness",
  "가상 기준 1항 / 회사 승인 기준 아님": "Synthetic criterion 1 / Not company-approved",
  "가상 기준 2항 / 모든 치수 조건 충족 필요": "Synthetic criterion 2 / All dimensional conditions must be met",
  "가상 기준 3항 / 발생 불허": "Synthetic criterion 3 / Defects prohibited",
  "가상 기준 4항 / 해당 없음": "Synthetic criterion 4 / Not applicable",
  "해당 없음": "Not applicable",
  "해석 미확정": "Unresolved interpretation",
  "결함 발생 불허": "Defects are prohibited",
  "미입력": "Not entered",
  "있음": "Present",
  "없음": "Absent",
  "적용하지 않는 항목입니다. 합격으로 집계하지 않습니다.": "This item does not apply and is excluded from pass counts.",
  "기준 해석을 먼저 확정해야 합니다. 이 버전으로 승인할 수 없습니다.": "Resolve the interpretation of the criteria first. This revision cannot be approved.",
  "결함 발생 여부를 입력해 주세요.": "Specify whether the defect is present.",
  "결함이 확인되어 허용 불가입니다.": "A defect was observed and is not permitted.",
  "결함이 관찰되지 않았습니다.": "No defect was observed.",
  "입력값이 모든 조건을 만족합니다.": "The input satisfies all conditions."
};
const dimensions: Record<string, string> = { '높이': 'Height', '깊이': 'Depth', '직경': 'Diameter' };

export function legacyText(text: string): string {
  if (legacyLabels[text]) return legacyLabels[text];
  return text.split(/(?<=\.) /).map(part => {
    const violation = part.match(/^(높이|깊이|직경): 기준보다 ([0-9.]+) (mm|µm) (초과|미달)입니다\.$/);
    if (violation) {
      const [, label, difference, unit, direction] = violation;
      return `${dimensions[label]}: The value is ${difference} ${unit} ${direction === '초과' ? 'above' : 'below'} the limit.`;
    }
    return part
      .replace(/높이|깊이|직경/g, label => dimensions[label])
      .replaceAll('값 누락 또는 단위 불일치', 'Missing value or unit mismatch')
      .replaceAll('미확인', 'Not verified')
      .replaceAll('경계값과 같지만 등호가 허용되지 않습니다.', 'The value equals the boundary, but equality is not permitted.');
  }).join(' ');
}

function isDemo(standard: Standard): boolean {
  const ids = ['demo-cover-v1', 'demo-cover-en-v1'];
  return (ids.includes(standard.id) || ids.includes(standard.family_id ?? '')) && standard.provenance === 'SYNTHETIC';
}

// Customer terms (Korean, English or Chinese originals) shown in the reader's language.
// Evidence lines (source) always keep the customer's own wording.
let glossary: Record<string, Partial<Record<Language, string>>> = {};
const dimensionWords = ['높이', '깊이', '직경', '길이', '폭', '두께', '거칠기'];

export function setGlossary(value: Record<string, Partial<Record<Language, string>>>) { glossary = value; }

export function term(value: string, language: Language): string {
  return glossary[value]?.[language] ?? value;
}

function dimensionText(value: string, language: Language): string {
  return dimensionWords.reduce((text, word) => text.replaceAll(word, glossary[word]?.[language] ?? word), value);
}

function glossaryStandard(standard: Standard, language: Language): Standard {
  return {
    ...standard,
    product_name: term(standard.product_name, language),
    rules: standard.rules.map(rule => ({
      ...rule,
      area: term(rule.area, language),
      item: term(rule.item, language),
      conditions: rule.conditions.map(condition => ({ ...condition, label: term(condition.label, language) })),
    })),
  };
}

export function displayStandard(standard: Standard, language: Language = 'en'): Standard {
  if (!isDemo(standard)) return Object.keys(glossary).length ? glossaryStandard(standard, language) : standard;
  const text = (value: string) => translate(legacyText(value), language);
  return {
    ...standard,
    product_name: text(standard.product_name),
    rules: standard.rules.map(rule => ({
      ...rule,
      area: text(rule.area),
      item: text(rule.item),
      source: text(rule.source),
      conditions: rule.conditions.map(condition => ({ ...condition, label: text(condition.label) })),
    })),
  };
}

export function displayInspection(state: Inspection, language: Language = 'en'): Inspection {
  if (!isDemo(state.standard) && !Object.keys(glossary).length) return state;
  if (!isDemo(state.standard)) {
    const text = (value: string) => translate(dimensionText(value, language), language);
    return {
      ...state,
      standard: glossaryStandard(state.standard, language),
      item_results: state.item_results.map(result => ({
        ...result,
        area: term(result.area, language),
        item: term(result.item, language),
        criteria: text(result.criteria),
        measured: text(result.measured),
        explanation: text(result.explanation),
      })),
    };
  }
  const text = (value: string) => translate(legacyText(value), language);
  return {
    ...state,
    standard: displayStandard(state.standard, language),
    item_results: state.item_results.map(result => ({
      ...result,
      area: text(result.area),
      item: text(result.item),
      criteria: text(result.criteria),
      measured: text(result.measured),
      explanation: text(result.explanation),
      source: text(result.source),
    })),
  };
}
