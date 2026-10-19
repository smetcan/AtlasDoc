# Vendored front-end assets

The web UI does not load these from a CDN. They are served by the AtlasDoc process from `/static/vendor/`.

| File | Version | License | Upstream |
| --- | --- | --- | --- |
| `marked.umd.js` | marked 18.1.1 | MIT | https://github.com/markedjs/marked |
| `purify.min.js` | DOMPurify 3.4.17 | Apache-2.0 OR MPL-2.0 | https://github.com/cure53/DOMPurify |
| `mermaid.min.js` | mermaid 10.9.5 | MIT | https://github.com/mermaid-js/mermaid |
| `tailwind.css` | built with Tailwind CSS 3.4.19 and `@tailwindcss/typography` 0.5.19 | MIT | https://github.com/tailwindlabs/tailwindcss |
| `fonts.css` and `fonts/*.woff2` | IBM Plex Sans and IBM Plex Mono, latin and latin-ext, packaged by fontsource 5.3.0 | SIL OFL 1.1 | https://github.com/IBM/plex |

License texts are in `licenses/`.

`tailwind.css` is generated. Rebuild it from `frontend/` with `npm install && npm run build:css` after changing classes in `index.html`. The browser does not run Tailwind.

`mermaid.min.js` is the upstream browser build. It contains the dependencies that release bundles. Strings such as `http://www.w3.org/2000/svg` in that file are XML namespace names, not network requests.

IBM Plex is not sold by itself. Keep `licenses/ibm-plex-OFL.txt` with the font files.
