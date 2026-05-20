/// <reference types="vite/client" />

declare module "*.module.css" {
  // Each class is always present at runtime when authored in the matching
  // CSS file. Typing them as a string-record lets dotted access stay
  // string-typed under `noUncheckedIndexedAccess`. Mis-typed names fail
  // visibly in the browser (no styling applied), which is the same
  // feedback loop as plain CSS-in-JS.
  // biome-ignore lint/suspicious/noExplicitAny: see comment above
  const classes: any;
  export default classes;
}
