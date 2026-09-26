# Security

The engine is an offline numerical library with no network access and no
runtime dependencies. The attack surface is the local FastAPI service in
`apps/api/` and the YAML config loader. The API is designed to run on
`localhost` and has no authentication. Do not expose it to a network you do not
trust.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/AeroPoint/AP-BlackJack/security/advisories/new)
rather than in a public issue. Include what you found, how to reproduce it, and
the commit you tested against.

This is a small project maintained in spare time, so there is no guaranteed
response window, but reports are read and acknowledged.
