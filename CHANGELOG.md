# Changelog

## 1.3.0

- Toggling a device's internet switch no longer discards its schedule or
  name: the write changes only the rule type on the existing row, and the
  post-write confirmation now checks name and schedule as well as type.
- Removing a rule no longer unloads and reloads the whole switch platform.
  The affected switch removes itself and its registry entry while the writer
  still holds the rule lock; a rule deleted from the router's own UI shows
  the switch as unavailable instead of stale.
- MAC addresses are validated and canonicalised on every path, so
  `AA-BB-…`, `aabb…` and `aa:bb:…` address the same rule instead of creating
  duplicates, and a device tracker carrying a malformed MAC is reported
  instead of written to the router. Device names are limited to 32
  characters and reject delimiter and control characters; names from device
  trackers are sanitised.
- The periodic rule poll now waits for an in-flight rule write, so a poll
  that started before a removal can no longer recreate the removed switch.
- Confirmation compares names after HTML-unescaping, matching how
  schedules were already compared, so a router that echoes `&` as `&amp;`
  no longer fails a successful write. Confirmation also checks that every
  other row in the written table came back with its type, so a router that
  reports success but drops an unrelated rule no longer passes.
- Device names are decoded (`&#x3e;`, `&gt;`, …) before the delimiter check,
  and `&` itself is rejected in direct names and stripped from tracker
  names, since the router uses it to escape delimiters on readback.
- The options credentials form no longer sends the stored password back to
  the browser as a default.
- A service call spanning several routers now finishes every router and
  reports which succeeded and which failed instead of stopping at the first.
- Wrong credentials, a locked-out login, "another admin is logged in" and a
  bad status are now told apart. Only real credential failures start
  re-authentication; the rest retry setup as before. The options flow no
  longer saves rejected credentials, and changing credentials reloads the
  entry. Added a re-authentication flow.
- Entity and button write failures raise a Home Assistant error instead of
  being logged and swallowed.
- Diagnostics redact RADIUS keys, MAC addresses, client names (tracked
  devices, tracker attributes and entity names), the router host, serial
  number and configuration URL, IP addresses, gateways, DNS servers,
  hostnames, VPN logins and client lists, static-lease lists, MAC filter
  lists, and the WAN IP state for every WAN IP sensor.
- All twelve translation files carry the full key set; untranslated keys
  fall back to English. Fixed the `cannot_resolve` key mismatch.
- The library version check runs off the event loop.
- Removed unused constants and a stale duplicate of the result codes.
- The six write services (`device_internet_access`, `set_static_dhcp_lease`,
  `remove_static_dhcp_lease`, `reserve_current_ip`,
  `refresh_static_dhcp_leases`, `remove_trackers`) are now admin-only.
  Calls from automations and scripts, which carry no user, still run.
- "Hide passwords" now defaults to on and also hides RADIUS keys and the
  WireGuard server pre-shared key, so Wi-Fi and VPN secrets are no longer
  published as entity attributes unless you turn the option off. Hiding is
  applied per entry instead of to the shared entity descriptions, so one
  entry's setting no longer leaks into another and a reload restores
  attributes when the option is turned off.
- An unchanged option stored as off no longer forces a reload on every
  options-flow save.
- The initial setup form no longer echoes a rejected password back as a
  form default.
- New option "Verify SSL certificate", default off (ASUS routers ship a
  self-signed certificate). When on, both Home Assistant's session and the
  library verify the router's certificate. Existing entries are unchanged.
- New option "Ignore devices with randomised MAC addresses", default off.
  When on, new clients whose MAC has the locally-administered bit set are
  not tracked; clients already known keep updating.
- Removed dead code: legacy `cert_path` / `confirm` / `enable_control`
  options, unused sensor description fields, and the guards for a library
  without static DHCP support (the version check makes them unreachable).
- Requires asusrouter 2.0.0+jgassens.3 (version bumped so existing installs
  reinstall the library).

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
