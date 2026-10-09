<!-- lang-switch -->
**English** · [简体中文](../zh/license.md)

# License

DramaClaw Community Edition (CE) is licensed under the [Elastic License 2.0 (ELv2)](../../LICENSES/Elastic-2.0.txt) and is **source available**. This document explains in plain terms what you can and cannot do.

> **This fork is multi-licensed.** The application source is Elastic-2.0, but third-party components vendored under [`third_party/`](../../third_party/README.md) keep their own licenses and are aggregated at arm's length (each path is mapped to its SPDX id in [`REUSE.toml`](../../REUSE.toml) and listed in [`license-inventory.csv`](../../license-inventory.csv)):
>
> - **`third_party/dramaclaw-gateway`** — **AGPL-3.0-only**. It runs as a separate service reached over HTTP (mere aggregation); if you modify it and offer it over a network, the AGPL's copyleft/Source-available obligations apply to that component.
> - **`third_party/DA-2`** — **Apache-2.0**.
> - **`third_party/ml-sharp`** — Apple sample code (**LicenseRef-Apple-Sample-Code**) plus the **Apple ML Research Model** weights under a **NON-COMMERCIAL** license (**LicenseRef-Apple-ML-Research-Model**). The model weights may **not** be used for commercial purposes; the `world` (3DGS / SHARP) features that depend on them are optional and not enabled by default.
>
> Your use of the whole distribution must respect the **most restrictive** applicable license for each part. The full bill of materials is in [`NOTICE`](../../NOTICE); the license texts are in [`LICENSES/`](../../LICENSES/).

## Is this open source? <!-- banned-word-allow -->

It is **source available**, not "open source" as defined by the OSI. The source code is public, readable, modifiable, and self-hostable, but it carries one commercial restriction (see below). We always refer to it as "source available" and never call it "open source".<!-- banned-word-allow -->

## What you can do for free

- Run it locally / self-hosted, for personal use or internal company use
- Modify the source and build on top of it
- Build your own applications on DramaClaw and deliver work to your clients
- Integrate it into your own products via the API / CLI

## What is restricted (requires a commercial license)

ELv2 has just one core restriction:

- **You may not provide DramaClaw to third parties as a hosted / managed service** (i.e., turning CE directly into a SaaS and reselling it externally).

In addition, you may not remove the license / copyright notices, and you may not circumvent the license key mechanism (if any).

If you want to offer a hosted / multi-tenant commercial service to others, please obtain a commercial license (see contact details below).

## How does this relate to the Enterprise Edition (EE)

EE is the proprietary commercial edition (multi-tenancy, team collaboration, billing, managed operations, etc.) and is not in this repository. CE covers the full set of individual creator capabilities.

## Who owns the code I contribute

Contributions are governed by the [Contributor Agreement](../../CONTRIBUTING.md#贡献者协议): your contributions may be used for commercial purposes (including the official cloud / hosted offering), and the maintainers may adjust the license as needed. See CONTRIBUTING for details.

## Commercial licensing / partnerships

Email <business@dramaclaw.ai>.

> This document is a plain-language explanation and does not constitute legal advice; the [LICENSE](../../LICENSES/Elastic-2.0.txt) text governs in all cases.
