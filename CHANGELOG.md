# Changelog

## v0.3.0

### Features

- Freeze ten execution contracts with real authority boundary. ([fe61812])

### Fixes

- Repair SW-1510 contract findings — wheel packaging, envelope policy, catalog provenance. ([f55cd67])
- Constrain candidate to approved contract scope. ([526fc17])
- Reconcile candidate to authorized scope (restore catalog). ([0600690])
- Provision ops-engine v3.4.2 before dependency check. ([9d9799e])
- Replace old engine artifact with ABI signature check. ([f5518be])
- Provision pinned ops-engine per job before its imports. ([e081ec1])

### CI & Configuration

- Bind mirrors to SkillWeave-AI/skillweave-sdk. ([e30d0b7])
- Add preflight dependency guard — verify ops-engine v3.4.2 importable before push. ([4efd590])

### Tests

- Stage installed-wheel build in a pristine copy. ([3727acb])

### Contracts

- SDK owns the neutral adapter contract set (SW-ADAPT-001). ([a663a94])

### Packaging

- Ship the contract authority inside the SDK wheel (SW-1510-SDK-CI). ([bc86910])
- Ship the ownership inventory as installed package data (SW-1510-SDK-CI). ([7688f66])

### Miscellaneous

- Compose mirror-config: merge candidate onto private integration. ([a0c02a5])

[fe61812]: https://git.langevc.com/skillweave/skillweave-sdk/commit/fe6181217eff6044b8f831af3a303a10c6efbb0d
[f55cd67]: https://git.langevc.com/skillweave/skillweave-sdk/commit/f55cd67d8a5b0767da1dcc6cc958cc743341d2df
[3727acb]: https://git.langevc.com/skillweave/skillweave-sdk/commit/3727acb43aef1b473c6a8f0c0ed4e97fa89391b6
[526fc17]: https://git.langevc.com/skillweave/skillweave-sdk/commit/526fc17a3c6268189859ffd28e4b08986384c814
[0600690]: https://git.langevc.com/skillweave/skillweave-sdk/commit/06006901b506779801f8bc8759c569af1723d732
[e30d0b7]: https://git.langevc.com/skillweave/skillweave-sdk/commit/e30d0b7a607a6b71f08049dd0ea80a4f3bd5c6c3
[4efd590]: https://git.langevc.com/skillweave/skillweave-sdk/commit/4efd59023d4fb79ebe95ee526782310239faef3c
[9d9799e]: https://git.langevc.com/skillweave/skillweave-sdk/commit/9d9799e0b047ac2896994b1a4e1dcc65848ff543
[f5518be]: https://git.langevc.com/skillweave/skillweave-sdk/commit/f5518be1444602488becea6e3a40bc1f28102779
[e081ec1]: https://git.langevc.com/skillweave/skillweave-sdk/commit/e081ec1231c835fff27b73f5f4de91b8c7364b53
[a663a94]: https://git.langevc.com/skillweave/skillweave-sdk/commit/a663a9478493d694ee9ecaf4c9cd5411bb7a5ea8
[bc86910]: https://git.langevc.com/skillweave/skillweave-sdk/commit/bc86910c5a7c182ede598ebba750ca6cba0bfde6
[7688f66]: https://git.langevc.com/skillweave/skillweave-sdk/commit/7688f665669fc1be35ae40c70ecf3870518a9d23
[a0c02a5]: https://git.langevc.com/skillweave/skillweave-sdk/commit/a0c02a59057969b399dfd27a8483e9812d783d79
