# Publishing BuildDiet

The repository is [GianlucaRoma/builddiet](https://github.com/GianlucaRoma/builddiet). Its GitHub Pages site is [gianlucaroma.github.io/builddiet](https://gianlucaroma.github.io/builddiet/), published from `main` → `/docs`.

## Update the code or website

From a checkout of this repository:

```bash
git status
git add -A
git diff --cached --check
git commit -m "Describe the change"
git push origin main
```

Review the staged files before committing. Changes to `docs/index.html` trigger a Pages rebuild; the result may take a few minutes to appear. Keep the repository homepage set to `https://gianlucaroma.github.io/builddiet/` so GitHub's website link opens the site.

## Before a release

Run the test suite and check the [GitHub Actions matrix](https://github.com/GianlucaRoma/builddiet/actions) for Windows, macOS and Linux. Review the source and Git history for personal information and credentials. Avoid committing local configuration, `.env` files or generated data. Use a GitHub `noreply` address for commit authorship if you want to keep your personal email out of new commits.

Publishing a GitHub Release activates `.github/workflows/publish.yml`, which attempts to publish to PyPI. A normal push to `main` does not publish to PyPI.

## GitHub organization

The existing personal repository is sufficient. An organization is useful only if you later need a team identity, several projects or shared access management.
