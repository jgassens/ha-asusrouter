# AsusRouter Fixed IP

An independently maintained Home Assistant integration for ASUSWRT routers,
with fixed-IP management and verified client internet-access controls.

## Included Controls

- Read current static DHCP reservations.
- Reserve a tracked device's current IP.
- Create, update, and remove reservations.
- Refresh reservation state after router-side changes.
- Block, allow, or remove client internet-access rules.

Fixed-IP writes use the router's authenticated HTTP(S) WebUI API and are read
back after apply. SSH and Merlin-only commands are not required.

## Install

Add **https://github.com/jgassens/ha-asusrouter** to HACS as a custom
**Integration**, download the latest release, and restart Home Assistant.

Do not install this integration alongside another integration using the
**asusrouter** domain.

See the [README](https://github.com/jgassens/ha-asusrouter#readme) for actions,
compatibility, and safety guidance. Report problems in the
[issue tracker](https://github.com/jgassens/ha-asusrouter/issues).

This independently maintained project preserves the Apache-2.0 license,
NOTICE, original authorship, and Git history of its source project.
