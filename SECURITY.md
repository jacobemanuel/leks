# Security

## Reporting a problem

Please report a security problem privately through GitHub: the Security tab of this repository, then "Report a vulnerability". Please do not open a public issue for it. I read every report and answer there.

## Supported versions

Fixes go into the latest 3.x release. Older releases stay as they are.

## Where your data goes

LEKS reads your files, builds an index on your machine and answers an agent over MCP. Step by step:

1. **Reading.** `leks build` reads only the folders you list as roots in the config. It skips secret names (`.env` files, key files such as `.pem` or `id_rsa`, and the other patterns in [configuration.md](docs/configuration.md#roots)), files whose text holds a key, a token or a password, symbolic links unless you turn on `follow_links`, and the other skip rules listed there.
2. **Index.** Everything LEKS writes goes under its home folder, `~/.leks` unless you set another: the word index, the vectors, the models, locks and logs. The update log rotates at 5 MiB.
3. **Models.** `leks models fetch` downloads two public models from Hugging Face at pinned commits and writes the size and SHA-256 of every file into a manifest. It needs no account or token. Apart from installing the package, this download is the only network access LEKS makes.
4. **Model helper.** The models run in a separate helper process that only listens on this machine: on macOS and Linux on a Unix socket only your user can open, on Windows on a TCP port on 127.0.0.1 that answers only requests carrying a random token. The helper writes that token into a file in the LEKS home folder, whose access rules on Windows admit your user, the system and the administrators when the folder is in your user profile ([cli.md](docs/cli.md#leks-modeld)). On macOS it runs under `sandbox-exec` with no network, no writes outside its own run, cache and temp folders, and no new programs. Where `sandbox-exec` does not exist it runs without that sandbox; set `daemon.sandbox` to `on` in the config to refuse that ([configuration.md](docs/configuration.md#daemon)).
5. **Your agent host.** The MCP server talks to the host (Claude Code, GitHub Copilot or Codex) over stdio. The host sends what its agent reads, including the passages LEKS returns, to the host's model provider under that provider's terms. LEKS cannot see or limit that step, so keep files you must not share out of the roots in the config.
6. **Hooks.** The template's hooks for Claude Code, Copilot and Codex run a short brief and an index update when a session starts, and `leks check` when a turn ends. The check reports findings and edits nothing. Codex runs a project's hooks only after you have reviewed and trusted them with `/hooks`, and asks again when a hook changes.

## What is tested

The test suite covers the skip rules, the secret patterns, the helper's request limits and, on macOS, that the sandbox profile closes what it should. CI also scans every commit for secrets with gitleaks and runs CodeQL, bandit and pip-audit ([docs/testing.md](docs/testing.md#security)), where every workflow and what it proves is listed. LEKS has not had an external security audit.
