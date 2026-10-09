"""[AIPDD] VectCut 服务入口（独立启动点）。

用法::

    python services/vectcut/aipdd_server.py            # 默认 127.0.0.1:9001
    VECTCUT_PORT=9101 python services/vectcut/aipdd_server.py

与 ``uvicorn``/``python capcut_server.py`` 等上游启动方式相比，本入口额外提供：

- 非 Windows 平台的 ``desktop_companion`` 兼容 stub（上游 import 即崩的修复）；
- ``config.json`` 缺失时按环境变量自举；
- ``/aipdd/health``、``/aipdd/resolve_media``、``/aipdd/save_draft`` 附加路由。

实现全部位于 ``aipdd/server.py``；升级 VectCut 上游时本文件无需改动。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from aipdd.server import main

if __name__ == "__main__":
    main()