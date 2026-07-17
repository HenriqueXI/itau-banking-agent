import parser from "@typescript-eslint/parser";

export default [
  { ignores: [".next/**", "node_modules/**", "coverage/**"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { parser, parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } } },
    rules: {},
  },
];
