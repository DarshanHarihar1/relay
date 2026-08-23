import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["**/.next/**", "**/node_modules/**", "**/.venv/**"]
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
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
