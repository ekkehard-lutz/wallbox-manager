# Development and stable releases

Branch from `develop` and use a dedicated branch for each change. Read the
[requirements](specifications/README.md) and follow
[repository development rules](../AGENTS.md). Keep source changes, independent
review, release preparation and operator deployment in distinct steps.

## Validation

Set up Python 3.14 with [development dependencies](../requirements-dev.txt),
Node.js 24 and Chrome. Run the checks used by
[GitHub Actions](../.github/workflows/tests.yml).

```sh
python -m pytest -q
ruff check .
ruff format --check .
node --check custom_components/wallbox_manager/www/wallbox-manager-card.js
node tests/test_wallbox_card.cjs
node tests/test_wallbox_card_layout.cjs
git diff --check
```

The suite runs simulated Home Assistant, OCPP peers on loopback and the card
in Node and headless Chrome. Set `WB_SOURCE_ROOT` to a separate source checkout
of the compatible Stationary revision to include the mocked Stationary
contract fixture. Standard CI skips that fixture when the source is absent.
Check the manifest and HACS package layout, translation key parity, service
schemas and Markdown links and anchors. Use the
[stable validation record](v0.4/release-validation-v0.4.0.md) for provenance.

## Stable release procedure

1. Confirm the authorized release scope and refresh the remote branch and
   tag refs. Prepare the manifest version and release notes on a release
   branch. Record the accepted runtime and resulting tree.
2. Complete review and validate the candidate. Merge through pull requests
   while preserving ancestry. Confirm that the intended tree is on both
   `main` and `develop` and that the exact commits have passed GitHub CI.
3. Create an annotated version tag on the validated `main` commit. Wait for
   tag CI and compare the GitHub source ZIP with the tagged tree. Verify its
   manifest and HACS metadata.
4. Publish the GitHub release as stable and latest. Verify the released tag,
   commit, package and HACS release discovery. Keep published tags immutable.
   Installation and field verification are operator-controlled.

Retired development documents remain reachable through the published
[v0.4.0 source commit](https://github.com/ekkehard-lutz/wallbox-manager/tree/56883b1974c7a2606bf74f0567d413b678eb7024/docs).
For operational evidence, use the maintained
[field acceptance report](v0.4/field-acceptance.md). The immutable v0.4.0
source archive includes original prerelease links that depend on historical
GitHub releases and tags.
