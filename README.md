# AsusRouter Fixed IP

AsusRouter Fixed IP is an independently maintained Home Assistant integration
for ASUSWRT routers. It keeps the broad monitoring and control surface of
AsusRouter while adding first-class fixed-IP management and safer client
internet-access actions.

Current release: **v1.1.0**.

## What It Solves

ASUS routers support manual DHCP reservations, but those reservations are not
normally manageable from Home Assistant. Assigning or changing a fixed IP
therefore requires opening the router WebUI.

This integration exposes the router's native HTTP(S) WebUI operations in Home
Assistant. Router SSH access is not required.

## Fixed-IP Controls

The integration provides:

- A **Static DHCP reservations** sensor containing the reservation count and
  lease list.
- A **Reserve current IP** button on eligible tracked client devices.
- **asusrouter.set_static_dhcp_lease** to create or replace a reservation.
- **asusrouter.remove_static_dhcp_lease** to remove a reservation.
- **asusrouter.reserve_current_ip** to reserve selected clients at their
  currently reported addresses.
- **asusrouter.refresh_static_dhcp_leases** to refresh Home Assistant's cached
  reservation state.

The implementation reads and writes ASUSWRT's **dhcp_staticlist** setting,
enables **dhcp_static_x**, and applies changes through the router's native
service action. Writes reject duplicate IP assignments and are read back from
the router after apply.

## Internet-Access Controls

The **asusrouter.device_internet_access** action accepts AsusRouter device
trackers in the Home Assistant action editor:

- **block** enables a parental-control block.
- **allow** disables the block while retaining its rule.
- **remove** deletes the parental-control rule.

The action validates targets, routes each target through its owning router,
refreshes the router after the write, and confirms the requested state.
Already-achieved states are accepted. Removing a rule also removes its dynamic
switch entity instead of leaving an unavailable entity in Home Assistant.

Direct API callers may provide **devices** containing **mac** and optional
**name**. A **config_entry_id** is required when more than one router-mode
AsusRouter entry is loaded.

## Install With HACS

AsusRouter Fixed IP and the original AsusRouter integration use the same Home
Assistant domain. Only one can be installed at a time.

1. Back up Home Assistant.
2. In HACS, uninstall the original AsusRouter package without deleting the
   integration's configuration or entities.
3. Add **https://github.com/jgassens/ha-asusrouter** under **Custom
   repositories** as an **Integration**.
4. Download the latest release and restart Home Assistant.

Existing AsusRouter config entries and entity IDs remain in place because the
integration domain remains **asusrouter**.

## Compatibility

- Home Assistant **2026.7.4** or newer.
- Stock ASUSWRT **3.0.0.4.x** and **3.0.0.6.x** are the primary target. Fixed-IP
  support uses the stock-compatible HTTP(S) WebUI path and does not require
  Merlin-only commands.
- AsusWRT-Merlin is expected to work where it exposes the same WebUI fields and
  apply actions.
- ASUS firmware **5.x.x** is not currently supported.

Router models and firmware vary. Back up the router configuration before the
first write and verify the resulting reservation in the ASUS WebUI.

## Maintenance

This repository has its own **main** branch, issue tracker, release history,
and compatibility decisions. Source-project updates may be reviewed and
ported, but they are not merged or rebased automatically.

The integration installs the companion
[jgassens/asusrouter](https://github.com/jgassens/asusrouter) library from an
exact tested commit. This avoids silently changing the router API underneath a
Home Assistant release.

Report integration, fixed-IP, or release problems in
[this repository's issue tracker](https://github.com/jgassens/ha-asusrouter/issues).

## Development

Run the checks before publishing:

```sh
uv sync --all-groups
uv run pytest
uv run ruff check custom_components/asusrouter tests
uv run ruff format --check custom_components/asusrouter tests
```

## Attribution

This project is derived from
[Vaskivskyi/ha-asusrouter](https://github.com/Vaskivskyi/ha-asusrouter) and is
maintained independently. It is not affiliated with ASUS or endorsed by the
source project's maintainers.

The Apache-2.0 license, NOTICE file, original authorship, and Git history are
preserved.
