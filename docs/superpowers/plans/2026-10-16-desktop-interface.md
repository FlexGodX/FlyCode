# Desktop Interface Implementation Plan

**Goal:** Add a Codex-like desktop workspace to FlyCode while preserving the
existing interface and its behavior at `/`.

**Architecture:** `/desktop` renders the existing document with an isolated
stylesheet and enhancement script. Both interfaces use the same application
logic, dialogs, projects, preferences, run history and event stream. Desktop
enhancements move existing controls rather than implementing a second runtime.

**Tech Stack:** FastAPI, HTML, CSS, JavaScript, Playwright.

**Spec:** User request to analyze the installed Codex desktop interface and
implement a similar alternative without breaking the current UI. Preserve
`docs/Plan.md` §102 model/settings ordering and existing project/chat behavior.

## Constraints and verification

- Keep `/` on its existing stylesheet and application script.
- Preserve projectless chats, native folder selection, model choices and Stop.
- Use only working FlyCode capabilities and original UI assets.
- Verify desktop, 390px and 320px layouts, keyboard controls and both interfaces.
- Run interface/API/browser checks only for this UI task.

### Task 1: Reference analysis and an isolated entry point

Files: `docs/specs/desktop-interface.md`, `src/flycode/web/app.py`,
`tests/unit/web/test_desktop.py`.

- [x] Record measured tokens, layout and component boundaries from the installed
  app archive; keep extracted reference material outside the repository.
- [x] Add failing HTTP checks for `/desktop`, distinct assets and unchanged `/`.
- [x] Render the shared document with desktop-specific CSS/JS and a body class.

### Task 2: Desktop shell and interactions

Files: `src/flycode/web/static/desktop.css`, `desktop.js`, `index.html`,
`tests/browser/desktop.cjs`.

- [x] Add a failing browser scenario for separate entry points, sidebar collapse,
  theme preference, model/settings order, real demo execution, history, changes,
  responsive Stop and keyboard/mobile behavior.
- [x] Add neutral light/dark surfaces, compact toolbar, project/chat navigation,
  a centered empty-state composer and an optional inspector.
- [x] Keep application logic shared; add reversible links between interfaces.
- [x] Run the new scenario and existing browser/API regression checks.
- [x] Inspect screenshots, record results and refresh the local server.

## Verification results

- UI API: 17 passed (desktop presentation plus projects, native chooser and
  projectless chat persistence).
- Desktop browser: real demo repair, changes, journal replay, Stop, keyboard
  navigation, light/dark preference and desktop/390/320 layouts passed.
- Existing classic browser acceptance passed unchanged in behavior.
- Project browser acceptance passed against `/desktop`, including chooser-result
  integration and mobile dialogs.
- Projectless chat browser acceptance passed against `/desktop` with a real
  isolated server and offline model catalogue fixture.
- Screenshots inspected in both themes; dark card/diff contrast corrected.
- Changed-file Ruff, mypy, JavaScript syntax and diff whitespace checks passed.
- No non-interface test suite was run for this UI task.
