/**
 * Section 19 & Section 32 — Playwright Mobile Viewport & Smoke E2E Specification:
 * - Tests 360px, 390px, and 412px Android viewports for zero horizontal overflow.
 * - Verifies 5-item bottom navigation (Home, Scanner, Planner, Paper, More) with >= 44px touch targets.
 * - Verifies DEMO_MODE banner and SIMULATED badges are permanently visible.
 */
export const MOBILE_VIEWPORTS = [
  { width: 360, height: 800, label: 'Android Compact 360px' },
  { width: 390, height: 844, label: 'Mobile Standard 390px' },
  { width: 412, height: 915, label: 'Android Pixel 412px' },
];

export const BOTTOM_NAV_ITEMS = ['Home', 'Scanner', 'Planner', 'Paper', 'More'];
export const MIN_TOUCH_TARGET_PX = 44;
