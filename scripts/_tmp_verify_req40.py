"""[aipdd fork delta req40] 线上只读核验：目录条目 / 死名不复活 / 槽位未绑定 → 前置拒绝。

只读：不改状态库、不提交任何生成任务。
"""

import json

from novelvideo import model_gateway_settings as mgs
from novelvideo import newapi_provisioner as prov

DEAD_NAMES = ("ap-doubao-seed-2.1-turbo", "ap-minimax-m3")

providers = sorted(mgs.get_custom_newapi_providers().keys()) if hasattr(
    mgs, "get_custom_newapi_providers") else []
print("provider_keys:", providers)

for provider in providers:
    catalog = mgs.get_upstream_catalog(provider)
    meta = mgs.get_upstream_catalog_meta(provider)
    names = mgs.catalog_names_for_provider(provider)
    kinds = {}
    for entry in catalog:
        key = str(entry.get("kind") or "")
        kinds[key] = kinds.get(key, 0) + 1
    inferred = sum(1 for e in catalog if e.get("inferred"))
    print(f"--- provider={provider} catalog_n={len(catalog)} inferred={inferred} "
          f"fetchedAt={meta.get('fetchedAt')}")
    print("    kind:", kinds)
    for dead in DEAD_NAMES:
        print(f"    dead_in_catalog {dead}: {dead in names}")

print()
print("=== bindings (must not point at dead names) ===")
alias = mgs.get_newapi_model_alias_mappings()
bound = {k: v.get("target") for k, v in alias.items()
         if str(v.get("target") or "").strip()}
print(f"alias_total={len(alias)} alias_bound={len(bound)}")
for key, target in sorted(bound.items()):
    hit = [d for d in DEAD_NAMES if d in str(target)]
    print(f"    {key} -> {target}" + (f"   <<< DEAD {hit}" if hit else ""))
print("media_mappings:", len(mgs.get_newapi_media_model_mappings()))
print("embedding_cfg:", mgs.get_newapi_embedding_model_config())

print()
print("=== compiled registrations per provider ===")
for provider in providers:
    fn = getattr(prov, "build_channel_registrations", None)
    regs = fn(provider) if fn else None
    text = json.dumps(regs, ensure_ascii=False, default=str) if regs is not None else ""
    print(f"provider={provider} n={len(regs) if regs is not None else 'n/a'}")
    for dead in DEAD_NAMES:
        print(f"    dead_in_registrations {dead}: {dead in text}")
