# GitHub Pages

This repository can publish the MkDocs site from `docs/` with GitHub Pages.

## Local Preview

From the repository root:

```bash
python -m mkdocs serve
```

Open the printed local URL, usually:

```text
http://127.0.0.1:8000
```

If the port is already in use:

```bash
python -m mkdocs serve -a 127.0.0.1:8001
```

## Build Check

Before pushing documentation changes:

```bash
python -m mkdocs build --strict
```

## Publish

The repository includes `.github/workflows/docs.yml`, which publishes the docs
site when changes are pushed to `main`.

In GitHub, configure:

1. Repository **Settings**.
2. **Pages**.
3. Source: **Deploy from a branch**.
4. Branch: `gh-pages`.

After the workflow runs successfully, GitHub Pages serves the generated site
from the `gh-pages` branch.
