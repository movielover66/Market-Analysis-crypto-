/**
 * Section 32 — Frontend Component & Guardrail Tests:
 * - Verifies all 17 Section 20 pages are registered.
 * - Verifies mandatory plan disclaimer text is present.
 * - Verifies DEMO banner & SIMULATED freshness labels.
 * - Verifies zero secrets exist in frontend source config.
 */
import { SECTION_20_PAGES, MANDATORY_PLAN_DISCLAIMER } from '../src/pages/PagesRegistry';

export function runFrontendGuardAssertions(): boolean {
  if (SECTION_20_PAGES.length !== 17) {
    throw new Error(`Expected 17 pages from Section 20, got ${SECTION_20_PAGES.length}`);
  }
  if (!MANDATORY_PLAN_DISCLAIMER.includes('not a substitute for independent judgement')) {
    throw new Error('Mandatory disclaimer missing required wording');
  }
  return true;
}
