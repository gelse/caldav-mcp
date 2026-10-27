import js from '@eslint/js';
import globals from 'globals';
import svelte from 'eslint-plugin-svelte';
import tseslint from 'typescript-eslint';

const NO_LOCALSTORAGE =
  'The admin key must never touch localStorage — sessionStorage only (src/lib/auth.ts owns all storage).';
const NO_INLINE_STYLE =
  'Inline styles are banned (CSP-by-construction for UI-M3) — use CSS classes and custom properties.';
/**
 * Hygiene rules applied to every source file:
 * 1. localStorage banned as a bare identifier (no-restricted-globals),
 * 2. localStorage banned as a member of window/globalThis (no-restricted-
 *    properties — closes the `window.localStorage` bypass),
 * 3. inline styles banned in all three authored forms (no-restricted-syntax):
 *    - Svelte `style:` directives (compile into JS chunks no index.html
 *      grep can observe),
 *    - `style="…"` attributes in .svelte markup,
 *    - `style=` occurrences inside string/template literals in .ts.
 *    The post-build grep gate covers index.html itself.
 */
const hygieneRules = {
  'no-restricted-globals': ['error', { name: 'localStorage', message: NO_LOCALSTORAGE }],
  'no-restricted-properties': [
    'error',
    { object: 'window', property: 'localStorage', message: NO_LOCALSTORAGE },
    { object: 'globalThis', property: 'localStorage', message: NO_LOCALSTORAGE },
  ],
  'no-restricted-syntax': [
    'error',
    { selector: 'SvelteStyleDirective', message: NO_INLINE_STYLE },
    { selector: 'SvelteAttribute[key.name="style"]', message: NO_INLINE_STYLE },
    { selector: 'Literal[value=/\\bstyle=/]', message: NO_INLINE_STYLE },
  ],
  // No console output in shipped code — bodies may carry key material.
  'no-console': 'error',
};

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...svelte.configs['flat/recommended'],
  {
    files: ['**/*.ts'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      ...hygieneRules,
      // Convention: a leading underscore marks a deliberately unused binding
      // (e.g. typed mock parameters in tests).
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
    },
  },
  {
    files: ['**/*.svelte'],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: {
        parser: tseslint.parser,
      },
    },
    rules: hygieneRules,
  },
  {
    files: ['vite.config.ts', 'eslint.config.js', 'scripts/**/*.mjs'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
  {
    files: ['**/*.test.ts', 'src/test-setup.ts', 'src/test-helpers.ts'],
    rules: {
      // Tests stub globals and may inspect failures deliberately.
      'no-console': 'off',
    },
  },
);
