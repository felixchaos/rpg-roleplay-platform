//! upload —— 大文件分片上传三件套。
//!
//! 对应 Python `init_upload` / `put_chunk` / `finish_upload` / `cancel_upload`。
//! 用磁盘目录 `<UPLOAD_ROOT>/user_<id>/<upload_id>/` 存 meta.json + chunk_XXXX.bin。
//!
//! 与 Python 端的一致性约定:
//! - upload_id 形如 `up_<user_id>_<16hex>`,严格校验前缀防越权
//! - meta.json 字段:upload_id, user_id, filename, total_bytes, total_chunks,
//!   received_bytes, received_chunks, created_at
//! - put_chunk 幂等:同 chunk_index 重传会覆盖且修正 received_bytes
//! - finish_upload 拼接出完整 bytes,返回给调用方做后续 decode/import

use std::path::{Path, PathBuf};

use rand::Rng;
use serde::{Deserialize, Serialize};

use crate::error::{PlatformError, PlatformResult};
use crate::library::safe_filename;

/// 单 chunk 最大 8 MiB(Python `MAX_UPLOAD_CHUNK_BYTES`)。
pub const MAX_UPLOAD_CHUNK_BYTES: usize = 8 * 1024 * 1024;
/// 单次上传(总)最大 256 MiB —— 与 Python 的 script_upload_max_bytes 默认一致。
/// 实际 Python 是 read from core.config;Rust 端先固定,后续接 config crate 时收口 (TODO[P3-CFG])。
pub const MAX_SCRIPT_UPLOAD_BYTES: usize = 256 * 1024 * 1024;
/// 单次上传最多 4096 分片(Python 同值)。
pub const MAX_CHUNKS: usize = 4096;

/// upload 磁盘根目录;由 `RPG_UPLOAD_CHUNK_DIR` 覆盖,否则用 `platform_data/upload_chunks`。
pub fn upload_chunk_root() -> PathBuf {
    if let Ok(p) = std::env::var("RPG_UPLOAD_CHUNK_DIR") {
        return PathBuf::from(p);
    }
    PathBuf::from("platform_data/upload_chunks")
}

/// meta.json schema。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UploadMeta {
    pub upload_id: String,
    pub user_id: i64,
    pub filename: String,
    pub total_bytes: usize,
    pub total_chunks: usize,
    #[serde(default)]
    pub received_chunks: usize,
    #[serde(default)]
    pub received_bytes: usize,
    pub created_at: f64,
}

fn now_secs() -> f64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn random_token_hex(byte_len: usize) -> String {
    let mut rng = rand::thread_rng();
    let mut buf = vec![0u8; byte_len];
    for b in buf.iter_mut() {
        *b = rng.gen();
    }
    buf.iter().map(|b| format!("{:02x}", b)).collect()
}

/// upload_id 前缀校验 + 拼出磁盘目录;同时不让 `..` 越界。
pub fn upload_dir(user_id: i64, upload_id: &str) -> PlatformResult<PathBuf> {
    let prefix = format!("up_{}_", user_id);
    if !upload_id.starts_with(&prefix) {
        return Err(PlatformError::forbidden("无权访问该 upload_id"));
    }
    // 拒绝任何路径分隔/相对路径片段
    if upload_id.contains('/') || upload_id.contains('\\') || upload_id.contains("..") {
        return Err(PlatformError::validation("upload_id 含非法字符"));
    }
    Ok(upload_chunk_root()
        .join(format!("user_{}", user_id))
        .join(upload_id))
}

fn read_meta(dir: &Path) -> PlatformResult<UploadMeta> {
    let meta_path = dir.join("meta.json");
    if !meta_path.exists() {
        return Err(PlatformError::not_found("upload_id 不存在或已过期"));
    }
    let txt = std::fs::read_to_string(&meta_path)?;
    let meta: UploadMeta = serde_json::from_str(&txt)?;
    Ok(meta)
}

fn write_meta(dir: &Path, meta: &UploadMeta) -> PlatformResult<()> {
    let s = serde_json::to_string(meta)?;
    std::fs::write(dir.join("meta.json"), s)?;
    Ok(())
}

/// Python `init_upload`。
pub fn init_upload(
    user_id: i64,
    filename: &str,
    total_bytes: usize,
    total_chunks: usize,
) -> PlatformResult<UploadMeta> {
    if user_id <= 0 {
        return Err(PlatformError::validation("分片上传需要登录用户"));
    }
    if total_bytes == 0 || total_bytes > MAX_SCRIPT_UPLOAD_BYTES {
        return Err(PlatformError::validation(format!(
            "total_bytes 越界(最大 {})",
            MAX_SCRIPT_UPLOAD_BYTES
        )));
    }
    if total_chunks == 0 || total_chunks > MAX_CHUNKS {
        return Err(PlatformError::validation(format!(
            "total_chunks 越界(最大 {})",
            MAX_CHUNKS
        )));
    }
    let upload_id = format!("up_{}_{}", user_id, random_token_hex(8));
    let dir = upload_dir(user_id, &upload_id)?;
    std::fs::create_dir_all(&dir)?;
    let meta = UploadMeta {
        upload_id: upload_id.clone(),
        user_id,
        filename: safe_filename(if filename.is_empty() { "upload.bin" } else { filename }),
        total_bytes,
        total_chunks,
        received_bytes: 0,
        received_chunks: 0,
        created_at: now_secs(),
    };
    write_meta(&dir, &meta)?;
    Ok(meta)
}

/// Python `put_chunk` —— 把一块写到磁盘,刷新 meta。
pub fn put_chunk(
    user_id: i64,
    upload_id: &str,
    chunk_index: usize,
    blob: &[u8],
) -> PlatformResult<UploadMeta> {
    let dir = upload_dir(user_id, upload_id)?;
    let mut meta = read_meta(&dir)?;
    if chunk_index >= meta.total_chunks {
        return Err(PlatformError::validation("chunk_index 越界"));
    }
    if blob.len() > MAX_UPLOAD_CHUNK_BYTES {
        return Err(PlatformError::validation(format!(
            "chunk 超过 {} 字节",
            MAX_UPLOAD_CHUNK_BYTES
        )));
    }
    let chunk_path = dir.join(format!("chunk_{:04}.bin", chunk_index));
    // 幂等重传:扣回之前那块的字节数
    if chunk_path.exists() {
        let prev_size = std::fs::metadata(&chunk_path).map(|m| m.len() as usize).unwrap_or(0);
        meta.received_bytes = meta.received_bytes.saturating_sub(prev_size);
    } else if meta.received_bytes + blob.len() > meta.total_bytes {
        return Err(PlatformError::validation("累计字节超过 total_bytes 声明"));
    }
    // 重算 received_bytes 之后再 +
    if meta.received_bytes + blob.len() > meta.total_bytes {
        return Err(PlatformError::validation("累计字节超过 total_bytes 声明"));
    }
    std::fs::write(&chunk_path, blob)?;
    meta.received_bytes += blob.len();
    meta.received_chunks = count_chunks(&dir)?;
    write_meta(&dir, &meta)?;
    Ok(meta)
}

fn count_chunks(dir: &Path) -> PlatformResult<usize> {
    let mut n = 0;
    for entry in std::fs::read_dir(dir)? {
        let entry = entry?;
        if let Some(name) = entry.file_name().to_str() {
            if name.starts_with("chunk_") && name.ends_with(".bin") {
                n += 1;
            }
        }
    }
    Ok(n)
}

/// Python `finish_upload` —— 把所有 chunk 拼成完整 bytes,然后清理目录。
///
/// 与 Python 不一样的地方:Python 还会 base64 编码返回 file_item,这里直接给 Vec<u8>。
/// 调用方(import_script)拿到 bytes 后自己走 decode_bytes / clean_text。
pub fn finish_upload(user_id: i64, upload_id: &str) -> PlatformResult<(UploadMeta, Vec<u8>)> {
    let dir = upload_dir(user_id, upload_id)?;
    let meta = read_meta(&dir)?;
    if meta.received_chunks != meta.total_chunks {
        return Err(PlatformError::validation(format!(
            "分片未齐:{}/{}",
            meta.received_chunks, meta.total_chunks
        )));
    }
    if meta.received_bytes != meta.total_bytes {
        return Err(PlatformError::validation(format!(
            "字节不匹配:收到 {} ≠ 声明 {}",
            meta.received_bytes, meta.total_bytes
        )));
    }
    let mut out: Vec<u8> = Vec::with_capacity(meta.total_bytes);
    for i in 0..meta.total_chunks {
        let chunk_path = dir.join(format!("chunk_{:04}.bin", i));
        if !chunk_path.exists() {
            return Err(PlatformError::validation(format!("缺失 chunk {}", i)));
        }
        out.extend_from_slice(&std::fs::read(&chunk_path)?);
    }
    // 拼完不清理 —— 让 _consume_upload_chunks(peek=true) 还能再读一次(preview)。
    // 但通常调用方完成后会显式 cancel_upload。
    Ok((meta, out))
}

/// peek=true 不删原文件;import 流程用 peek=false。
pub fn consume_upload_chunks(
    user_id: i64,
    upload_id: &str,
    peek: bool,
) -> PlatformResult<Vec<u8>> {
    let dir = upload_dir(user_id, upload_id)?;
    let meta = read_meta(&dir)?;
    if meta.received_chunks != meta.total_chunks {
        return Err(PlatformError::validation("分片未齐,无法消费"));
    }
    let mut out: Vec<u8> = Vec::with_capacity(meta.total_bytes);
    for i in 0..meta.total_chunks {
        let chunk_path = dir.join(format!("chunk_{:04}.bin", i));
        out.extend_from_slice(&std::fs::read(&chunk_path)?);
    }
    if !peek {
        let _ = std::fs::remove_dir_all(&dir);
    }
    Ok(out)
}

/// Python `cancel_upload`。
pub fn cancel_upload(user_id: i64, upload_id: &str) -> PlatformResult<()> {
    let dir = upload_dir(user_id, upload_id)?;
    if dir.exists() {
        std::fs::remove_dir_all(&dir)?;
    }
    Ok(())
}

/// Python `cleanup_stale_upload_chunks` —— 删超过 ttl 小时未动过的目录。
pub fn cleanup_stale_upload_chunks(ttl_hours: u64) -> PlatformResult<usize> {
    let base = upload_chunk_root();
    if !base.exists() {
        return Ok(0);
    }
    let cutoff = now_secs() - (ttl_hours as f64) * 3600.0;
    let mut cleaned = 0;
    let user_dirs = match std::fs::read_dir(&base) {
        Ok(d) => d,
        Err(_) => return Ok(0),
    };
    for user_dir in user_dirs.flatten() {
        if !user_dir.path().is_dir() {
            continue;
        }
        if let Ok(up_dirs) = std::fs::read_dir(user_dir.path()) {
            for up in up_dirs.flatten() {
                let p = up.path();
                if !p.is_dir() {
                    continue;
                }
                let mtime = p
                    .metadata()
                    .and_then(|m| m.modified())
                    .ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_secs_f64())
                    .unwrap_or(0.0);
                if mtime < cutoff {
                    if std::fs::remove_dir_all(&p).is_ok() {
                        cleaned += 1;
                    }
                }
            }
        }
    }
    Ok(cleaned)
}

// ─────────────────────────── tests ───────────────────────────
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEST_DIR_SEQ: AtomicU64 = AtomicU64::new(0);

    /// 给每个测试一个独立的临时上传根,避免并行测试互相打架。
    fn with_isolated_dir<F: FnOnce()>(f: F) {
        let n = TEST_DIR_SEQ.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!(
            "rpg_upload_test_{}_{}",
            std::process::id(),
            n
        ));
        let prev = std::env::var("RPG_UPLOAD_CHUNK_DIR").ok();
        std::env::set_var("RPG_UPLOAD_CHUNK_DIR", &dir);
        f();
        // cleanup
        let _ = std::fs::remove_dir_all(&dir);
        match prev {
            Some(p) => std::env::set_var("RPG_UPLOAD_CHUNK_DIR", p),
            None => std::env::remove_var("RPG_UPLOAD_CHUNK_DIR"),
        }
    }

    #[test]
    fn test_init_upload_creates_meta() {
        with_isolated_dir(|| {
            let m = init_upload(7, "novel.txt", 1024, 2).unwrap();
            assert!(m.upload_id.starts_with("up_7_"));
            assert_eq!(m.user_id, 7);
            assert_eq!(m.total_bytes, 1024);
            assert_eq!(m.total_chunks, 2);
            assert_eq!(m.received_bytes, 0);
            let dir = upload_dir(7, &m.upload_id).unwrap();
            assert!(dir.join("meta.json").exists());
        });
    }

    #[test]
    fn test_init_upload_rejects_zero_user() {
        with_isolated_dir(|| {
            let err = init_upload(0, "x.txt", 100, 1).unwrap_err();
            assert!(matches!(err, PlatformError::Validation(_)));
        });
    }

    #[test]
    fn test_init_upload_rejects_too_many_chunks() {
        with_isolated_dir(|| {
            let err = init_upload(1, "x.txt", 100, MAX_CHUNKS + 1).unwrap_err();
            assert!(matches!(err, PlatformError::Validation(_)));
        });
    }

    #[test]
    fn test_put_chunk_then_finish() {
        with_isolated_dir(|| {
            let m = init_upload(3, "data.bin", 10, 2).unwrap();
            let part_a = b"hello";
            let part_b = b"world";
            let m1 = put_chunk(3, &m.upload_id, 0, part_a).unwrap();
            assert_eq!(m1.received_chunks, 1);
            assert_eq!(m1.received_bytes, 5);
            let m2 = put_chunk(3, &m.upload_id, 1, part_b).unwrap();
            assert_eq!(m2.received_chunks, 2);
            assert_eq!(m2.received_bytes, 10);
            let (final_meta, bytes) = finish_upload(3, &m.upload_id).unwrap();
            assert_eq!(final_meta.received_bytes, 10);
            assert_eq!(bytes, b"helloworld");
        });
    }

    #[test]
    fn test_put_chunk_idempotent_overwrite() {
        with_isolated_dir(|| {
            let m = init_upload(4, "x.bin", 6, 1).unwrap();
            put_chunk(4, &m.upload_id, 0, b"abcdef").unwrap();
            // 再次写同 index,received_bytes 应该是 6(不重复累加)
            let m2 = put_chunk(4, &m.upload_id, 0, b"xyz123").unwrap();
            assert_eq!(m2.received_bytes, 6);
            let (_, bytes) = finish_upload(4, &m.upload_id).unwrap();
            assert_eq!(bytes, b"xyz123");
        });
    }

    #[test]
    fn test_put_chunk_rejects_oversized() {
        with_isolated_dir(|| {
            let m = init_upload(5, "x.bin", MAX_UPLOAD_CHUNK_BYTES + 1, 1).unwrap();
            let big = vec![0u8; MAX_UPLOAD_CHUNK_BYTES + 1];
            let err = put_chunk(5, &m.upload_id, 0, &big).unwrap_err();
            assert!(matches!(err, PlatformError::Validation(_)));
        });
    }

    #[test]
    fn test_put_chunk_rejects_byte_overflow() {
        with_isolated_dir(|| {
            let m = init_upload(6, "x.bin", 5, 2).unwrap();
            put_chunk(6, &m.upload_id, 0, b"hello").unwrap();
            // 再写 chunk 1 = 1 byte,累加 6 > 5 声明
            let err = put_chunk(6, &m.upload_id, 1, b"x").unwrap_err();
            assert!(matches!(err, PlatformError::Validation(_)));
        });
    }

    #[test]
    fn test_finish_upload_rejects_incomplete() {
        with_isolated_dir(|| {
            let m = init_upload(8, "x.bin", 10, 2).unwrap();
            put_chunk(8, &m.upload_id, 0, b"hello").unwrap();
            // 缺 chunk 1
            let err = finish_upload(8, &m.upload_id).unwrap_err();
            assert!(matches!(err, PlatformError::Validation(_)));
        });
    }

    #[test]
    fn test_cancel_upload_removes_dir() {
        with_isolated_dir(|| {
            let m = init_upload(9, "x.bin", 5, 1).unwrap();
            put_chunk(9, &m.upload_id, 0, b"hello").unwrap();
            let dir = upload_dir(9, &m.upload_id).unwrap();
            assert!(dir.exists());
            cancel_upload(9, &m.upload_id).unwrap();
            assert!(!dir.exists());
        });
    }

    #[test]
    fn test_upload_dir_rejects_wrong_user_prefix() {
        with_isolated_dir(|| {
            // user 9 想用 user 1 的 upload_id
            let err = upload_dir(9, "up_1_abc").unwrap_err();
            assert!(matches!(err, PlatformError::Forbidden(_)));
        });
    }

    #[test]
    fn test_upload_dir_rejects_path_traversal() {
        with_isolated_dir(|| {
            let err = upload_dir(1, "up_1_..").unwrap_err();
            assert!(matches!(err, PlatformError::Validation(_)));
        });
    }

    #[test]
    fn test_consume_upload_chunks_peek_keeps_dir() {
        with_isolated_dir(|| {
            let m = init_upload(11, "x.bin", 5, 1).unwrap();
            put_chunk(11, &m.upload_id, 0, b"hello").unwrap();
            let dir = upload_dir(11, &m.upload_id).unwrap();
            let bytes = consume_upload_chunks(11, &m.upload_id, true).unwrap();
            assert_eq!(bytes, b"hello");
            assert!(dir.exists()); // peek 不删
            let bytes2 = consume_upload_chunks(11, &m.upload_id, false).unwrap();
            assert_eq!(bytes2, b"hello");
            assert!(!dir.exists()); // 非 peek 删
        });
    }
}
