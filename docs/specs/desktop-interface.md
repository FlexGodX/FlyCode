# FlyCode desktop interface reference

The installed desktop app identifies itself as `com.openai.codex`, package
version `26.1007.71326`. Its local renderer is packaged in
`/Applications/ChatGPT.app/Contents/Resources/app.asar` under `webview/`.
This analysis reads packaged renderer resources; it does not establish that
every available component is enabled in the current account or configuration.

## Observed structure

| Area | Local evidence | FlyCode implementation |
|---|---|---|
| Navigation rail | `--spacing-token-sidebar`: preferred 275px, minimum 240px; project/sidebar modules | Project and chat navigation in a collapsible 275px rail |
| Toolbar | `--height-toolbar:46px`; thread shell chrome separated from page content | Compact toolbar with chat title and optional inspector |
| Spacing | Shared `--spacing:.25rem` | Four-pixel spacing steps, compact controls |
| Typography | Platform sans-serif stack; separate UI text and heading tokens | System font, clear hierarchy, restrained heading weights |
| Surfaces | Neutral gray scale with light/dark token substitutions | Separate light/dark shell palettes |
| Composer | Dedicated editor, footer/action controls and rounded surface tokens | Existing task composer, model selection and execution settings |
| Content | Thread chrome has Chat/page/tab concepts; page and shell components separate | Existing conversation, changes and log views |
| Panels | Chrome and document panels are separate renderer components | Existing agent inspector shown on demand |

Primary resources inspected: `app-shared-6ed15bfa827c.css`,
`app-initial-99e8b4b455fa.css`, `app-primary-ca4a3fdeb6cc.css`,
`thread-app-shell-chrome-283504a55587.js`,
`composer-controls-7b97df99f6a2.js` and sidebar modules. Extracted files remain
under `/tmp/flycode-codex-reference`; no packaged application code, fonts, logos
or assets are included in FlyCode.

## Implementation boundary

FlyCode provides `/desktop` as a separate presentation of its existing runtime.
The classic interface remains at `/`. Both share server state and model choices.
The new shell exposes actual project/chat, run, history, model, settings, Stop,
changes and log capabilities. It does not present unimplemented agent teams,
cloud jobs, Git operations or publication controls as working features.

Desktop-only preferences cover theme and navigation visibility. All CSS is
scoped to the desktop document. Dynamic enhancement runs after the existing
application script and retains the original elements and their event handlers.
