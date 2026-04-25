# Vendored browser assets

These files are checked in as prebuilt browser assets so leanblueprint users do
not need Node.js to build a blueprint.

- `d3.min.js`: `d3@7.9.0`, copied from `dist/d3.min.js`.
- `d3-graphviz.js`: `d3-graphviz@5.6.0`, copied from `build/` and
  locally patched with a camera transform resolver hook for blueprint
  navigation.
- `hpcc.min.js`: `@hpcc-js/wasm@2.33.2`, copied from
  `dist/graphviz.umd.js`, with a small compatibility shim appended so
  d3-graphviz's worker can read `globalThis["@hpcc-js/wasm"].Graphviz`.
