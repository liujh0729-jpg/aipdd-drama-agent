"""Security primitives for sandboxed agent workers (Hermes etc.).

Public API:
    - SandboxSpec: per-user sandbox configuration
    - wrap_command: cross-platform sandbox command wrapper
    - build_macos_profile: helper for direct sandbox-exec testing (used by PoC)
"""

from novelvideo.security.sandbox_wrap import (
    SandboxSpec,
    build_macos_profile,
    wrap_command,
)

__all__ = ["SandboxSpec", "build_macos_profile", "wrap_command"]
