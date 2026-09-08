# Changelog

## 1.2.0

- Parental-control (device internet access) writes can no longer wipe other
  rules: one router-owned writer under a lock reads the current table fresh,
  refuses to write if it cannot, applies every change to that snapshot, and
  writes once. The UI switch and the service share that path.
- Confirmation re-reads the router (never the cache), waits as long as the
  router says `restart_firewall` needs (capped at 10 s), and reports honestly
  when the state cannot be confirmed.
- Writes that would exceed the router's advertised rule or schedule-window
  limits are rejected before being sent, with the exact counts and limit.
- The `Block internet` switch only shows the new state after the router
  confirmed it, and surfaces failures instead of logging and moving on.
- Existing rules with empty names or schedules are written back unchanged
  instead of as the literal `None`.
- Debug logs no longer include router payloads, device names or MACs.
- Setup fails with a clear message if the installed asusrouter library is
  older than the pinned one.
- Requires asusrouter 2.0.0+jgassens.2.

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
