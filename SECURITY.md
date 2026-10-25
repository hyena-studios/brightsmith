# Security Policy

## Supported versions

Brightsmith is currently alpha software. Security fixes are applied to the latest release and the default development branch; older versions may not receive patches.

## Reporting a vulnerability

Please do not disclose a suspected vulnerability in a public issue, discussion, or pull request. Use GitHub's **Report a vulnerability** flow in the repository's Security tab to send a private report.

Include, when possible:

- the affected version or commit;
- the attack scenario and required access;
- minimal reproduction steps;
- the potential impact; and
- any suggested mitigation.

You should receive an acknowledgment within seven days. A fix timeline will depend on severity and complexity. Please allow time for a patch before public disclosure.

## Scope and deployment assumptions

The MCP SQL surface is designed to be read-only and disables DuckDB external access before executing client-provided queries. Brightsmith does not yet provide row-level, tenant-level, or per-user authorization. Do not expose an MCP server to untrusted users or a public network without an independent authentication and authorization layer.

Domain configurations, DQ rule SQL, and governance artifacts are trusted-operator inputs. Treat write access to them as privileged access. Do not place credentials or sensitive source data in committed domain packs.
