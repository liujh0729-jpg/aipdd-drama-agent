//! 启动架构自检：装错版本（如 Apple Silicon 上跑了 Intel 包）在门口拒绝，
//! 而不是丢进 Rosetta 再因用户目录包的架构不匹配崩一个玄学 ImportError。
//!
//! 判定口径（2.3.1 真机事故驱动）：arm 包里塞了 x86_64 的随包 Python——壳是
//! arm64、payload 是 x64，靠主执行件架构查不出来。所以这里以**随包 Python
//! 解释器**（真正会崩的那一环）的代表去读架构，和**真机芯片**比对：
//!   - 真机是 Apple Silicon（hw.optional.arm64==1）→ 随包必须含 arm64；
//!   - 真机是 Intel → 随包必须含 x86_64。
//! 缺对应架构就返回提示。arm64 包在 Intel 机上根本 exec 不了（系统直接拦），
//! 本模块覆盖的是可达的那一侧：x86_64 payload 落在 Apple Silicon 上（能走
//! Rosetta 静默跑，正是要拦的）。
use std::path::Path;

/// 真机硬件是否为 Apple Silicon。
///
/// 关键：x86_64 进程在 Rosetta 下 `uname -m` / `std::env::consts::ARCH` 都会
/// 返回被翻译后的 x86_64，无法区分「Intel 机」与「arm 机跑 Intel 包」。只有
/// `sysctl hw.optional.arm64` 反映真实硬件、不被 Rosetta 翻译，是唯一可信地气。
#[cfg(target_os = "macos")]
pub fn host_is_apple_silicon() -> Option<bool> {
    let out = std::process::Command::new("sysctl")
        .args(["-n", "hw.optional.arm64"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    match String::from_utf8_lossy(&out.stdout).trim() {
        "1" => Some(true),
        "0" => Some(false),
        _ => None,
    }
}

/// 非 macOS（Windows/Linux）不做这项判定，交给调用方跳过。
#[cfg(not(target_os = "macos"))]
pub fn host_is_apple_silicon() -> Option<bool> {
    None
}

/// cputype → 可读架构名。屏蔽 CPU_ARCH_ABI64 后按基型 + 64 位标志归类。
fn arch_of_cputype(cpu: u32) -> Option<&'static str> {
    const ABI64: u32 = 0x0100_0000;
    let base = cpu & !ABI64;
    let is64 = cpu & ABI64 != 0;
    match base {
        7 => Some(if is64 { "x86_64" } else { "i386" }), // CPU_TYPE_X86
        12 => Some(if is64 { "arm64" } else { "arm" }),  // CPU_TYPE_ARM
        _ => None,
    }
}

/// 解析 Mach-O（thin / fat）二进制内含的 CPU 架构列表。
///
/// 直接读文件字节，不依赖 `lipo`/`file`——终端用户机器通常没有 Xcode CLT，
/// 出包机才有。解析失败返回空表，调用方据此「读不出就不误伤」。
pub fn macho_archs(path: &Path) -> Vec<String> {
    let bytes = std::fs::read(path).unwrap_or_default();
    let mut out: Vec<String> = Vec::new();
    if bytes.len() < 12 {
        return out;
    }
    // 统一先按大端读前 4 字节，用魔数区分 thin/fat 与字节序。
    let be = u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
    let mut push = |cpu: u32| {
        if let Some(a) = arch_of_cputype(cpu) {
            if !out.iter().any(|s| s == a) {
                out.push(a.to_string());
            }
        }
    };
    match be {
        // fat_arch：魔数 0xcafebabe（大端存放）/ 0xbebafeca（小端存放，需 swap）。
        // nfat_arch 在偏移 8，条目自 12 起、每条 20 字节，字段皆大端。
        0xcafebabe | 0xbebafeca => {
            let swap = be == 0xbebafeca;
            let n = u32::from_be_bytes([bytes[8], bytes[9], bytes[10], bytes[11]]);
            let n = if swap { n.swap_bytes() } else { n } as usize;
            for i in 0..n {
                let o = 12 + i * 20;
                if o + 4 > bytes.len() {
                    break;
                }
                let cpu = u32::from_be_bytes([bytes[o], bytes[o + 1], bytes[o + 2], bytes[o + 3]]);
                push(if swap { cpu.swap_bytes() } else { cpu });
            }
        }
        // 64 位 thin：MH_MAGIC_64=0xfeedfacf 按小端存放时，大端读法得到 0xcffaedfe。
        // 故 be==0xcffaedfe → 本机小端（正常）；be==0xfeedfacf → 字节交换（大端宿主，罕见）。
        0xfeedfacf | 0xcffaedfe => {
            let native_le = be == 0xcffaedfe;
            push(rd_u32(&bytes, 4, native_le));
        }
        // 32 位 thin：MH_MAGIC=0xfeedface，同理 be==0xcefaedfe 为小端。
        0xfeedface | 0xcefaedfe => {
            let native_le = be == 0xcefaedfe;
            push(rd_u32(&bytes, 4, native_le));
        }
        _ => {}
    }
    out
}

fn rd_u32(b: &[u8], off: usize, little: bool) -> u32 {
    let a = [b[off], b[off + 1], b[off + 2], b[off + 3]];
    if little {
        u32::from_le_bytes(a)
    } else {
        u32::from_be_bytes(a)
    }
}

/// 判断随包 payload 能否在本机原生运行。
///
/// 返回 `None` 放行；`Some(文案)` 表示版本与本机芯片不符，调用方应拒绝起服务。
/// 探不到真机芯片 / 读不出 payload 架构时一律放行——绝不在信息不足时误伤正常包。
pub fn mismatch_message(python_bin: &Path) -> Option<String> {
    let arm_host = host_is_apple_silicon()?;
    let archs = macho_archs(python_bin);
    if archs.is_empty() {
        return None;
    }
    let (want, got_wrong, host_name, wrong_name) = if arm_host {
        ("arm64", "x86_64", "Apple Silicon（arm）版", "Intel x86_64 版")
    } else {
        ("x86_64", "arm64", "Intel（x64）版", "Apple Silicon arm64 版")
    };
    if archs.iter().any(|a| a == want) {
        return None;
    }
    if archs.iter().any(|a| a == got_wrong) {
        Some(format!(
            "检测到本机是{host_name}芯片，但当前安装的 App 是{wrong_name}（随包运行时架构：{}），不匹配、无法稳定运行。\n请卸载后下载对应版本的安装包。",
            archs.join("/")
        ))
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_exe(name: &str, bytes: &[u8]) -> std::path::PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!("dramaclaw-arch-{name}-{nanos}"));
        std::fs::write(&p, bytes).unwrap();
        p
    }

    #[test]
    fn parses_thin_64bit_little_endian_x86_64() {
        // MH_MAGIC_64(小端) + cputype=0x01000007(x86_64)
        let mut b = Vec::new();
        b.extend_from_slice(&0xfeedfacfu32.to_le_bytes());
        b.extend_from_slice(&0x0100_0007u32.to_le_bytes());
        b.extend_from_slice(&[0u8; 32]);
        let p = tmp_exe("x64thin", &b);
        assert_eq!(macho_archs(&p), vec!["x86_64".to_string()]);
        std::fs::remove_file(&p).ok();
    }

    #[test]
    fn parses_thin_64bit_arm64() {
        let mut b = Vec::new();
        b.extend_from_slice(&0xfeedfacfu32.to_le_bytes());
        b.extend_from_slice(&0x0100_000cu32.to_le_bytes());
        b.extend_from_slice(&[0u8; 32]);
        let p = tmp_exe("armthin", &b);
        assert_eq!(macho_archs(&p), vec!["arm64".to_string()]);
        std::fs::remove_file(&p).ok();
    }

    #[test]
    fn parses_fat_universal_with_both_arches() {
        // fat 布局（字段皆大端）：0..4 magic 0xcafebabe, 4..8 version,
        // 8..12 nfat_arch, 12.. 起 fat_arch[]（每条 20 字节，cputype 在前 4 字节）。
        let mut b = Vec::new();
        b.extend_from_slice(&0xcafebabeu32.to_be_bytes()); // magic
        b.extend_from_slice(&1u32.to_be_bytes()); // version
        b.extend_from_slice(&2u32.to_be_bytes()); // nfat_arch
        // fat_arch #0: cputype x86_64
        b.extend_from_slice(&0x0100_0007u32.to_be_bytes());
        b.extend_from_slice(&[0u8; 16]); // subtype/offset/size/align
        // fat_arch #1: cputype arm64
        b.extend_from_slice(&0x0100_000cu32.to_be_bytes());
        b.extend_from_slice(&[0u8; 16]);
        let p = tmp_exe("fat", &b);
        let a = macho_archs(&p);
        assert!(a.contains(&"x86_64".to_string()), "got {a:?}");
        assert!(a.contains(&"arm64".to_string()), "got {a:?}");
        std::fs::remove_file(&p).ok();
    }

    #[test]
    fn empty_or_non_macho_returns_empty() {
        let p = tmp_exe("junk", b"not a macho file at all, really long enough bytes");
        assert!(macho_archs(&p).is_empty());
        std::fs::remove_file(&p).ok();
    }

    #[test]
    fn mismatch_rejects_x64_payload_when_host_is_arm() {
        // 注入一台「虚拟」判定：直接构造一个 x86_64 thin 文件，
        // 手动跑与 host 无关的核心逻辑（want/got 匹配）。
        let mut b = Vec::new();
        b.extend_from_slice(&0xfeedfacfu32.to_le_bytes());
        b.extend_from_slice(&0x0100_0007u32.to_le_bytes());
        b.extend_from_slice(&[0u8; 32]);
        let p = tmp_exe("x64reject", &b);
        let archs = macho_archs(&p);
        // arm 主机期望 arm64：x86_64-only 应判为不匹配（复刻 mismatch_message 的判定）。
        let want = "arm64";
        assert!(archs.iter().all(|a| a != want));
        assert!(archs.iter().any(|a| a == "x86_64"));
        std::fs::remove_file(&p).ok();
    }
}
