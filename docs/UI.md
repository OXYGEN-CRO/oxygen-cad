# Oxygen Glass

The studio uses a dark glass interface: translucent charcoal panels, softly illuminated edges, rounded controls, silver text and restrained ice-blue actions. The design applies to the functioning CAD editor, verification tools, history and source view.

## Shared tokens

`frontend/src/tokens.css` defines the semantic colors, materials, radii, spacing and motion values. `style.css` imports these tokens for the application. The Three.js viewport reads the `--viewport-*` tokens when it initializes, so the real geometry view shares the same dark palette.

- Canvas: near-black, with a quiet grid and cool neutral model lighting.
- Panels: 24px glass blur, rounded corners, a fine border and soft upper reflection.
- Controls: inset dark inputs, rounded toolbars, segmented navigation and an illuminated primary action.
- State: mint for current/success, amber for stale/review/selection, rose for errors. Existing numerical result colors and legends retain their engineering meaning.
- Type: native system sans-serif, with IBM Plex Mono for dimensions, source and measured results.
- Access: visible focus rings, native dark form controls, named icon actions, exposed toggle state, reduced-motion support and solid-surface fallbacks.

Place prefixed backdrop declarations before the standard declaration. The production CSS optimizer can otherwise discard the standard declaration when the shared blur value is a custom property. The browser test checks the computed blur in the built application.

## Responsive studio

The model gets the main workspace. The inspector appears when a feature, reference, face or engineering view needs it. Design and Verify remain primary modes; source, history, parameters, imports and agent commands are available from Project tools. On narrow screens, the inspector overlays the workspace and can be dismissed with Close properties. All editing commands remain available without a fixed desktop minimum width.

## Behavior

The theme changes presentation and lighting. Real editing, saves, revision conflict protection, parameter locks, face selection, geometry checks, exports and studies continue through the existing application state and command service. The interface does not replace engineering results with illustrative states.

## Verification

Run `npm run build` in `frontend`, then `npm test` with the disposable fixture. The UI appearance test visits Design, Verify, History and Source at 320, 390, 768, 1024 and 1512 pixels, checks actual dark surfaces, glass blur, readable input colors and page overflow, and confirms that browsing the interface does not change the engineering document. The functional flows additionally exercise editing/rebuild/check/export, revision conflicts and a real static study.

These checks run in Linux Chromium. Physical macOS/Safari and extended-session usability remain separate validation work.
