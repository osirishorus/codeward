# Codeward PR Report Workflow

Copy this workflow into `.github/workflows/codeward-pr.yml` in a repository that wants Codeward PR reports.

```yaml
name: Codeward PR report

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write
  issues: write

jobs:
  codeward-pr-report:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run Codeward PR report
        uses: osirishorus/codeward@main
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          security: "true"
          comment: "true"
          python-version: "3.12"
```

Inputs: `base` (default: `""`; uses the pull request base, then repository default branch), `security` (default: `"true"`), `comment` (default: `"true"`), `python-version` (default: `"3.12"`), `from-source` (default: `"false"`).
