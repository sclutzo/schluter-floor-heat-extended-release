# Security policy — Schluter Floor Heat Extended

This is an **unofficial beta integration** using a third-party cloud interface. No warranty or vendor endorsement is implied.

## Reporting a security concern

Please **do not post credentials, tokens, session cookies, raw HAR files, Home Assistant backup archives, device serials, internal IP addresses, or unredacted provider responses** in a public issue.

If GitHub private vulnerability reporting is available for this repository, use it. Otherwise, first ask the maintainer for a private reporting channel without disclosing exploit details or secrets in an issue.

## Supported versions

The `1.1.0b1` beta is the only planned initial public version, pending approval. The project currently provides no guaranteed security-update SLA. Use current supported Home Assistant releases and keep a separate backup of persistent Recorder/energy history.

## Operational safeguards

The integration uses Home Assistant config entries for account credentials, bounded network timeouts, sanitized provider errors, per-device timezone verification before hourly Recorder import, and schedule-write readback verification. It does **not** require installing a privileged background daemon or exposing Home Assistant to the internet.

Never upload your `/config/.storage/` directory, authentication cache, or Home Assistant database to a public issue.
