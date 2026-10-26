# Security

Report a vulnerability privately to security@axius-sdc.com. Please do not open a public issue. The organization
policy is at https://github.com/SemanticDataCharter/.github/blob/main/SECURITY.md. Expect an acknowledgement within
three business days, and a fix released before anything is made public.

## Supported versions

| version | supported |
|---|---|
| 4.3.x | yes |
| earlier | no |

## What counts as a vulnerability here

The mapper's promise is its report: every value in a Governed Data Record is reported as lookup, declared, carried
or not representable, and every Exceptional Value as preserved, degraded, carried or lost. A value written to a
target without being reported, reported in the wrong class, or an Exceptional Value passed off as a value, is an
integrity defect and in scope. So is any way a record that fails validation reaches a target as if it were valid.

## The MCP server, as a threat model

`sdc-mapper` (alias `sdc-mapper-mcp`) speaks JSON-RPC over stdio. There is no authentication and no network
listener: whoever can write to the process's stdin is the caller, and controls every argument.

- **File paths are the caller's.** Records, mapping files and an output directory are opened as the process's user.
  The server writes mapped files and `coverage.json` only to the output directory the caller names, and appends
  each call to a hash-chained log in its log directory. Run it as an account that can read and write only what it
  should; it does not sandbox paths.
- **Nothing is fetched.** The mapper makes no network requests.

Out of scope: the authenticity of a record, model package or mapping file the caller supplies.
