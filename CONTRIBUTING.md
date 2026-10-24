# Contributing to Oxygen CAD

Start with a reproducible part or workflow. Include the project document, expected dimensions or behavior, actual measurements, and relevant worker logs. Share only geometry you have permission to publish.

For a modeling feature, extend the typed schema, compiler, property editor, documentation, and verification tests together. Every advertised native feature needs agent and manual editing coverage. Fail explicitly when geometric references disappear or become ambiguous.

For numerical work, include the physical assumptions, analytical/reference solution, units, mesh refinement, and force/moment balance. A solver finishing or a screenshot looking convincing is not sufficient evidence.

Before submitting:

```sh
docker build -t oxygen-cad .
docker run --rm oxygen-cad pytest -q
cd frontend
npm ci
npm run build
```

Run Playwright for UI changes against a disposable studio project. Regenerate public types with `scripts/generate_types.py` when changing the Pydantic schema. Keep dependency lockfiles updated. Do not commit private projects, caches, or credentials.

The project uses GitHub issues and pull requests for public collaboration. Contributions are provided under the repository's MIT license; dependencies keep their respective licenses.
