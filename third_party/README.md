# Vendored third-party components

This repository **aggregates several independently-licensed works**. The application
code under `src/`, `frontend/`, etc. is **Elastic License 2.0 (ELv2)**. Everything in
this `third_party/` directory is **not** ours and keeps its **original license** — the
full text of each is in [`/LICENSES`](../LICENSES) and is declared per-path in
[`/REUSE.toml`](../REUSE.toml).

Because these components remain **separate programs / libraries** (they are not linked
into, or imported as part of, the ELv2 application binary — the gateway is a standalone
service reached over an OpenAI-compatible HTTP API), they are distributed here as a
**mere aggregation**: each keeps its own license and obligations. Do **not** apply the
application's ELv2 terms to anything in this folder.

| Component | Upstream | Pinned commit | License | Notes |
|---|---|---|---|---|
| `dramaclaw-gateway/` | https://github.com/dramaclaw/dramaclaw-gateway | `main` @ `6f990d84` | **AGPL-3.0-only** | OpenAI-compatible inference gateway (LocalAPI), a fork of [QuantumNous/new-api](https://github.com/QuantumNous/new-api). Built as its **own Docker service** — never imported by the app. Carries `LICENSE` + `NOTICE`. |
| `ml-sharp/` | https://github.com/apple/ml-sharp | `1eaa0468` | **LicenseRef-Apple-Sample-Code** | SHARP depth/3D code (the optional `world` extra). |
| `ml-sharp/LICENSE_MODEL` | (same) | — | **LicenseRef-Apple-ML-Research-Model** | ⚠️ The released **model weights** are research / non-commercial only. No weights are vendored here (they are fetched separately at runtime); review that license before commercial use. |
| `DA-2/` | https://github.com/EnVision-Research/DA-2 | `d6598385` | **Apache-2.0** | DA-2 depth model (the optional `world` extra). |

## Your obligations per license

- **AGPL-3.0 (gateway):** if you modify the gateway or run it and let users interact
  over a network, you must make the gateway's **source (incl. your changes) available
  under AGPL-3.0**, and you may **not** add further restrictions to it. Keeping it as
  a separate service that the app calls over HTTP avoids contaminating the ELv2 app.
- **Apache-2.0 (DA-2):** retain `LICENSE`, NOTICE and copyright headers.
- **Apple licenses (ml-sharp):** retain the full notice/disclaimer verbatim on any
  redistribution; do not use Apple's name/marketing to endorse derivatives; the model
  weights carry the non-commercial research terms above.

## How this folder was produced / how to refresh

Source checkouts live as siblings outside this repo; the vendored tree was copied
**without** `.git`/`node_modules`/`.venv`/`__pycache__`. To update a component:

```sh
git clone https://github.com/dramaclaw/dramaclaw-gateway.git /tmp/gw
git -C /tmp/gw checkout <new-sha>
rsync -a --delete --exclude '.git' --exclude node_modules --exclude .venv \
      --exclude __pycache__ /tmp/gw/ third_party/dramaclaw-gateway/
# then update the table above and the [tool.uv.sources] pins if applicable
```
