# Oxygen Studio components

The studio uses a monochrome technical interface with dark graphite and light neutral themes, fine borders, quiet selected states and restrained glass depth. The design applies to the functioning CAD editor, verification tools, history and source view.

## Shared tokens

`frontend/src/tokens.css` defines the semantic colors, materials, radii, spacing and motion values. `style.css` imports these tokens for the application. The Three.js viewport reads the `--viewport-*` tokens at initialization and on theme changes, so real geometry, edges, selections and grids share the active palette. Switching keeps the existing renderer, camera, selection and unsaved draft.

- Canvas: near-black in dark mode and soft neutral grey in light mode, with a quiet grid and neutral model lighting.
- Panels: 16px glass blur, rounded corners, a fine border and minimal reflection.
- Controls: inset reading surfaces, rounded toolbars, spatial breadcrumbs and a dark graphite primary action.
- State: mint for current/success, amber for stale/review/selection, rose for errors. Existing numerical result colors and legends retain their engineering meaning.
- Type: locally served Geist Sans, with Geist Mono for dimensions, source and measured results.
- Access: quiet 1px field focus borders without an outer glow, visible keyboard focus on actions, native themed form controls, named icon actions, exposed toggle state, reduced-motion support and solid-surface fallbacks.

Place prefixed backdrop declarations before the standard declaration. The production CSS optimizer can otherwise discard the standard declaration when the shared blur value is a custom property. The browser test checks the computed blur in the built application.

## Themes

Use the sun/moon control in the header. Dark is the default; an explicit choice is stored locally under `oxygen-cad-theme` and applied before first paint on reload. If browser storage is unavailable the switch still works for the current session. Theme preferences are separate from the engineering document and never create a revision.

Both themes cover the entire studio, including menus, property fields, source editing, study controls, warnings and viewport overlays. Numerical stress/displacement color maps retain their meaning.

## Reusable controls

`frontend/src/components/ui.tsx` and `ui.css` define the shared Button, Input, SelectControl and Menu primitives. Buttons expose size and variant recipes; inputs share typography, spacing, hover, disabled and invalid states. The menu uses Radix Primitives for arrow-key navigation, focus return, Escape, outside dismissal and viewport-aware portal positioning. Every native studio action and field uses these primitives while keeping existing event handlers, refs and commands. Primary actions have a dark graphite fill in both their idle and hover states. Inputs use the sans-serif face for comfortable editing; vectors, source and measured results use the mono face. Corners, borders, icon strokes and spacing follow the same compact treatment.

The design follows the inspected OXYGEN component patterns: a 4px spacing grid, compact 28/32/36/40px controls, Geist, fine glass rims and unfiltered nested fields. This is an original neutral adaptation built on open-source Radix, rather than a copy of the private product catalog. Text fields use solid reading surfaces; selectors and toolbar chips use glass at control scale, without stacking blur inside a glass pane.

## Responsive studio

The model gets the main workspace. A compact breadcrumb header replaces branding, the project-title field and mode tabs. The Workspace crumb and left panel icon independently toggle the feature sidebar. The current-context crumb and right panel icon toggle the inspector. Selecting a feature or a face opens its properties. Closing the inspector preserves the active item, unsaved edits and study context.

Project settings, Study setup, Source and History are available from Project tools. Project naming remains editable in Project settings. Generic guidance and duplicated metadata are removed. Actual measurements, errors, stale-state evidence and study assumptions remain visible where they support an engineering decision.

On narrow screens, the inspector overlays the workspace; both panels remain controllable from the header. Save/export/download stay accessible as compact icon actions when space is limited.

## Behavior

The theme changes presentation and lighting. Real editing, saves, revision conflict protection, parameter locks, face selection, geometry checks, exports and studies continue through the existing application state and command service. The interface does not replace engineering results with illustrative states.

## Verification

Run `npm run build` in `frontend`, then `npm test` with the disposable fixture. The UI appearance test visits the model inspector, Study setup, History and Source at 320, 390, 768, 1024 and 1512 pixels, checks both themes, actual glass blur, readable input colors and page overflow, and confirms that browsing the interface does not change the engineering document. Theme coverage also verifies the actual WebGL clear color, persistent preferences, preserved renderer and unsaved input, quiet pointer/keyboard field focus, and menu keyboard operation. The functional flows additionally exercise editing/rebuild/check/export, revision conflicts and a real static study.

These checks run in Linux Chromium. Physical macOS/Safari and extended-session usability remain separate validation work.
