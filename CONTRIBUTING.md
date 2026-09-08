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

## Updating the asusrouter library pin

The integration pins its companion library by git commit in `manifest.json`
and `pyproject.toml`. pip decides whether to reinstall by comparing package
**versions**, not commits, so moving the pin alone leaves existing Home
Assistant installs on the old library. When you move the pin:

1. bump `version` in the library's `pyproject.toml` and tag it;
2. update both pin files and run `uv lock`;
3. update `MIN_LIBRARY_VERSION` in `custom_components/asusrouter/const.py`;
4. run `uv run --group test pytest` - `test_installed_test_library_satisfies_guard`
   fails if the test environment's library is behind the declared minimum.
