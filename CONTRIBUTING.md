# Contributing to AsusRouter Fixed IP

Issues and pull requests are welcome in
[jgassens/ha-asusrouter](https://github.com/jgassens/ha-asusrouter).

## Set Up

```sh
git clone https://github.com/jgassens/ha-asusrouter.git
cd ha-asusrouter
uv sync --all-groups
uv run prek install
```

Development targets the **main** branch. Create a short-lived branch for each
change.

## Validate Changes

```sh
uv run pytest
uv run prek run --all-files
```

Changes to fixed-IP or internet-access behavior should include focused tests.
Router writes must validate their input and confirm the resulting router state.

## Pull Requests

- Explain the user-visible problem and the behavior after the change.
- Include tests for new behavior and regressions.
- Keep compatibility claims limited to firmware or hardware that has evidence.
- Preserve Apache-2.0 attribution and existing Git history.

Questions, bugs, and feature requests belong in the
[project issue tracker](https://github.com/jgassens/ha-asusrouter/issues).
