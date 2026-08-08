# Changelog

## 1.1.0

- Established AsusRouter Fixed IP as an independently maintained integration.
- Promoted the live-tested release line to the stable **main** branch.
- Preserved static DHCP write validation and router-side readback.
- Preserved hardened client internet-access actions and stale-entity cleanup.
- Removed inherited bots, funding, and update workflows that depended on the
  source project's ownership or credentials.
- Preserved Apache-2.0 licensing, attribution, and repository history.

## 1.0.0+jgassens.2

- Added Home Assistant static DHCP services and device controls.
- Added router-state confirmation for client internet-access changes.
- Fixed idempotent rule removal and unavailable switch cleanup.
