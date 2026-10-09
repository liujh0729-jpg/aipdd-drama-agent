"""[AIPDD] VectCut 服务适配层。

本目录与 ``aipdd_server.py`` 是 AIPDD 集成 VectCut 时新增的文件，升级
VectCut 上游版本时应当保留（详见 ``README-AIPDD.md`` 的升级步骤）。
"""

from __future__ import annotations

__all__ = ["media_resolver", "server"]