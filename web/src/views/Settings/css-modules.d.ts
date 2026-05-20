// Ambient module declaration for CSS Modules. Vite handles the runtime
// import; TypeScript needs this declaration to typecheck `import css from
// "./Foo.module.css"`. Safe to coexist with an identical declaration
// elsewhere — ambient module declarations are merged across files.
declare module "*.module.css" {
  const classes: Record<string, string>;
  export default classes;
}
