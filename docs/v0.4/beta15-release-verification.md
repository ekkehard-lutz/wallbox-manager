# beta.15 coordinated merge and release verification

This record covers repository/release preparation only. Supervised installation
and physical acceptance are separate; no production system is changed.

## Reviewed source and explicit merges

| Repository | Approved feature | Approved commit | Previous develop | Non-fast-forward merge |
| --- | --- | --- | --- | --- |
| WBM | `feature/live-netz-timing-and-status` | `1a766121060d77f38ef30749028b1f6d6ea510d1` | `b46832404a478c1f2588822b70acaf7af46a080e` | `8de0635e7fb7aede8c0bc068157271a85ccf6806` |
| Stationary | `feature/disabled-cp-keep-awake` | `c32e9c935601b8b9271200f1dc3f669dde43275e` | `0f97ebf6f4d83e0b56f33d3f4bb68f9642c92313` | **`279287c3c1d8863e803144eccf773ab3f10889a5`** |

Each previous develop was an ancestor of its approved feature. Both merge trees
are byte-for-byte identical to the approved feature trees, including normative
requirements. Original requirements/feature branches remain intact. WBM release
preparation changes only the manifest version and release documentation/README;
all other integration files match the reviewed implementation exactly.

The exact merged Stationary develop commit above is the controlled-installation
candidate. No Stationary version, tag or GitHub release is introduced.

## Pre-merge maintained checks

- WBM: **516 Python tests passed**, including the companion Stationary/WBM
  hardware-mocked OCPP contract; **58 frontend tests passed**; Ruff, formatting,
  JavaScript syntax and whitespace passed. Approved feature CI succeeded at
  [run 40133544672](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/40133544672).
- Stationary: **841 Python tests and 607 subtests passed**. All **103 tracked
  Python sources compile**. Ruff is exactly the established **141 findings**
  (same file/code/message multiset); formatting affects the same **77 paths**.
  No unrelated cleanup is included. No GitHub Actions workflow is configured.

## Release candidate and publication gates

The complete release-candidate run passed **516 Python tests** (including the
companion contract) and **58 frontend tests**. Ruff lint/format, JavaScript syntax
and whitespace passed. Exact-commit CI omits the absent companion checkout and
therefore expects that single combined contract to be skipped. Version is `0.4.0-beta.15`; the tag/release namespace was
verified absent before creation. Stable/latest was `v0.3.1`.

Packaging checks verify the complete **39-file integration**, unchanged HACS
metadata/entity identity sources, branding/notices, manifest dependencies, service
schemas and English/German translation leaf parity. The candidate ZIP and GitHub
source archive are checked against the exact release integration bytes, excluding
runtime files and all eight unrelated untracked files (whose hashes are preserved).

Publication requires successful CI on both the exact develop release commit and
its new annotated `v0.4.0-beta.15` tag. Failures stop publication without replacing
a tag. The completion report supplies immutable SHAs and both CI URLs. The GitHub
release is explicitly a prerelease with latest disabled; existing stable/latest
classification is preserved. No Stationary tag, main merge, installation, INI
edit, production service operation or hardware command is authorized here.

See [release notes](release-notes-beta.15.md) for behavior, coordinated compatibility,
saved-draft/deadline handling, frontend refresh and rollback considerations.
