// Tauri v2 构建脚本。tauri-build 负责：能力/权限校验、生成上下文、
// 以及（macOS）为内嵌 Info.plist 注入 bundle 元数据。
fn main() {
    tauri_build::build()
}
