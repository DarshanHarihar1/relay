import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["**/.next/**", "**/node_modules/**", "**/.venv/**"]
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["packages/contracts/scripts/**/*.mjs"],
    languageOptions: {
      globals: {
        process: "readonly"
      }
    }
  },
  {
    files: ["**/*.test.{ts,tsx}"],
    languageOptions: {
      globals: {
        expect: "readonly",
        it: "readonly"
      }
    }
  }
);
