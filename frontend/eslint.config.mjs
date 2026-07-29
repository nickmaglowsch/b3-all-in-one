import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Same cyclomatic ceiling the Python side gets from ruff's C901
  // (scripts/quality_gates.py). Module size is checked there too, for .ts/.tsx
  // as well as .py, so there is no max-lines rule here.
  {
    files: ["**/*.{ts,tsx}"],
    // "warn", not "error": `npm run lint` gates correctness, and the complexity
    // ceiling is a ratchet over 18 pre-existing offenders rather than a wall. The
    // ratchet (fail only when one worsens or a new one appears) is enforced by
    // scripts/quality_gates.py, which reads these same warnings.
    rules: { complexity: ["warn", 10] },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
