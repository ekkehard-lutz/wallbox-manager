# Development rules

- Read applicable specifications in `docs/specifications/` first; respect explicit approval and precedence, especially WBM-ARCH-01–12.
- Analyze existing code before adding implementation. Remove superseded and redundant code rather than maintaining competing paths.
- Examine `v0.3.2-beta.18` for applicable proven behavior; do not restore its runtime wholesale.
- Cross-check relevant functionality against actual openWB source and record the revision and files inspected.
- Write Codex tasks and technical documentation in English.
- Commit and push completed, validated work promptly on the task's dedicated branch.
- Keep implementation, independent review, merge, release and deployment separate. A code task does not authorize hardware commands or deployment.
