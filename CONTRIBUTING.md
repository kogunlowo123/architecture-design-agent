# Contributing

Thanks for helping improve this project. This guide covers the workflow and the quality bar.

## Development setup

```bash
git clone <repository-url>
cd architecture-design-agent
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Checks

Every change must pass the gates CI enforces:

```bash
make lint        # ruff check + ruff format --check
make typecheck   # mypy --strict
make cov         # pytest with a coverage gate of 80%
```

`make format` applies safe autofixes and formatting.

## Workflow

1. Open an issue for anything larger than a small fix so the design can be discussed first.
2. Branch from `main`: `feature/<short-name>` or `fix/<short-name>`.
3. Keep commits focused, with imperative subjects.
4. Add or update tests. Bug fixes need a regression test that fails without the fix.
5. Update `CHANGELOG.md` under **Unreleased** and any affected documentation.
6. Open a pull request describing the problem, the approach and how you verified it.

## Code standards

- Python 3.10+, fully type-annotated, `mypy --strict` clean.
- Docstrings explain behaviour, not restate names.
- Errors raised deliberately derive from `ArchagentError`.
- Agents are pure: the same inputs give the same outputs, with no network or clock access.
- Every number in a report comes from a named constant or catalogue field, and appears in the assumptions when
  it is not obvious.
- Anything rendered into Markdown or Mermaid goes through `md_cell` or `mermaid_label`.
- Never send requirement text, names or component identifiers to a language model.
- Tests are offline and deterministic (see `tests/conftest.py`).

## Adding a review rule

1. Write `def _my_rule(ctx: Context) -> list[Finding]` in `agents/review.py` and return `[]` when it does not apply.
2. Use the next free `ARCH-` number and a severity that reflects the context.
3. Add it to `RULES`, add tests for firing and for staying quiet, and add it to the table in the README.

## Adding a component kind or style

See the Extending section of [docs/architecture.md](docs/architecture.md).

## Reporting security issues

See [SECURITY.md](SECURITY.md). Do not file public issues for vulnerabilities.

## License

By contributing you agree that your contributions are licensed under the MIT License.
