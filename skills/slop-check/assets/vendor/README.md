# Dashboard vendor assets

`highlight.min.js` bundles Highlight.js 11.12.0 core and the Python, Rust, and
JavaScript grammars. Its BSD 3-Clause license is in `highlight.LICENSE`.
Upstream: https://github.com/highlightjs/highlight.js/tree/11.12.0

Built with esbuild 0.25.12 using `--bundle --minify --format=iife
--legal-comments=inline`. The entry imports `highlight.js/lib/core`, registers
`highlight.js/lib/languages/python`, `rust`, and `javascript` with
`registerLanguage`, then assigns the core instance to `window.hljs`.

`code.svg` is the solid code icon from Font Awesome Free 7.3.1. It is licensed
under CC BY 4.0; attribution is retained in the SVG and the package license is
in `fontawesome.LICENSE`. Upstream: https://fontawesome.com/icons/code?f=classic&s=solid
