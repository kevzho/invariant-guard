# Security policy

InvariantGuard runs repository code only through `--execute` with an explicit run profile, normally inside a Docker container with `--network none`. Static commands are designed never to import or execute the analyzed repository.

Please report privately, through GitHub's "Report a vulnerability" (Security tab), any of these:

- a way for static commands (`check`, `verify` without `--execute`, `contracts list|show`, the MCP read tools) to import, execute or modify analyzed code;
- a probe-runner or replay-harness escape from its profile (network, filesystem or capability limits);
- a way for an MCP client to reach paths outside its configured roots, approve contracts or behavior decisions, or select a non-preauthorized execution profile;
- leakage of an API key, or of context packets to an unconfigured destination.

This is an alpha. There are no supported release lines yet; fixes land on `main`.

Note that the Docker capability checks describe what a profile claims as observed from inside the container. They are not a claim of containment against container or kernel exploits.
