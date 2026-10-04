import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import globals from "globals";

export default defineConfig([
  { ignores: ["dist/"] },
  js.configs.recommended,
  {
    languageOptions: { globals: globals.browser },
    rules: {
      eqeqeq: ["error", "always"],
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "prefer-const": "error",
    },
  },
  { files: ["*.config.js", "scripts/**"], languageOptions: { globals: globals.node } },
]);
