//! library —— 用户素材库 (`platform_data/library/user_<id>/`) + `assets` 表。
//!
//! 对应 Python: `rpg/platform_app/library.py`。
//!
//! 提供:
//! - 目录浏览:`list_dir`
//! - 新建目录:`mkdir`
//! - 上传文件:`upload` (base64 解码,写盘,登记 `assets`)
//! - 删除:`delete`
//! - 下载:`download_path`
//!
//! 安全:`safe_path` 防越权,文件名过滤,单次上传上限。

use std::path::{Path, PathBuf};

use base64::{engine::general_purpose, Engine};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

pub const MAX_UPLOAD_BYTES: usize = 64 * 1024 * 1024;
pub const MAX_FILES_PER_REQUEST: usize = 12;

/// 单条目录项。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LibraryEntry {
    pub name: String,
    pub path: String,
    /// `"directory"` 或 `"file"`。
    pub r#type: &'static str,
    pub size: u64,
    pub mime: String,
    pub modified: i64,
}

/// `list_dir` / `mkdir` / `delete` / `upload` 的统一返回。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LibraryListing {
    pub engine: &'static str,
    pub path: String,
    pub entries: Vec<LibraryEntry>,
    pub page: PageMeta,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PageMeta {
    pub limit: usize,
    pub next_cursor: Option<String>,
    pub has_more: bool,
}

/// 一份上传 payload。`base64`/`data_url` 至少给一个。
#[derive(Debug, Clone, Deserialize)]
pub struct UploadItem {
    #[serde(default)]
    pub name: String,
    #[serde(default, rename = "type")]
    pub mime_type: String,
    #[serde(default)]
    pub base64: String,
    #[serde(default, alias = "dataUrl")]
    pub data_url: String,
}

/// 用户根目录;首次调用会自动 mkdir。
pub fn user_root(user_id: i64) -> PathBuf {
    let base = library_root();
    let dir = base.join(format!("user_{}", user_id));
    let _ = std::fs::create_dir_all(&dir);
    dir
}

fn library_root() -> PathBuf {
    if let Ok(dir) = std::env::var("RPG_LIBRARY_DIR") {
        return PathBuf::from(dir);
    }
    PathBuf::from("platform_data/library")
}

/// Python `safe_path` —— 拼接后必须仍在 root 下。
pub fn safe_path(root: &Path, rel_path: &str) -> PlatformResult<PathBuf> {
    let root_abs = root
        .canonicalize()
        .unwrap_or_else(|_| root.to_path_buf());
    let target = root_abs.join(rel_path.trim_start_matches('/'));
    // 不要求 target 已存在;normalize 用 components 处理 `..`。
    let normalized = normalize(&target);
    if normalized != root_abs && !normalized.starts_with(&root_abs) {
        return Err(PlatformError::validation("非法路径"));
    }
    Ok(normalized)
}

fn normalize(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for c in p.components() {
        match c {
            std::path::Component::ParentDir => {
                out.pop();
            }
            std::path::Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn parent_rel(root: &Path, target: &Path) -> String {
    let parent = target.parent().unwrap_or(root);
    if parent == root {
        String::new()
    } else {
        parent
            .strip_prefix(root)
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default()
    }
}

/// Python `safe_filename`。
pub fn safe_filename(name: &str) -> String {
    let stem = Path::new(name)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("file.bin");
    let mut buf = String::with_capacity(stem.len());
    for ch in stem.chars() {
        if ch.is_alphanumeric()
            || matches!(ch, '.' | '_' | '-' | ' ')
            || ('\u{4e00}'..='\u{9fff}').contains(&ch)
        {
            buf.push(ch);
        } else {
            buf.push('_');
        }
    }
    let trimmed: String = buf.trim_matches(|c: char| c == ' ' || c == '.' || c == '_').to_string();
    if trimmed.is_empty() {
        "file.bin".to_string()
    } else {
        trimmed
    }
}

fn unique_path(path: &Path) -> PlatformResult<PathBuf> {
    if !path.exists() {
        return Ok(path.to_path_buf());
    }
    let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("file");
    let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("");
    let parent = path.parent().unwrap_or(Path::new("."));
    for i in 2..1000 {
        let name = if ext.is_empty() {
            format!("{}-{}", stem, i)
        } else {
            format!("{}-{}.{}", stem, i, ext)
        };
        let candidate = parent.join(name);
        if !candidate.exists() {
            return Ok(candidate);
        }
    }
    Err(PlatformError::validation("无法分配文件名"))
}

fn kind_for(mime: &str, suffix: &str) -> &'static str {
    let s = suffix.to_lowercase();
    if mime.starts_with("image/") {
        return "image";
    }
    if mime.starts_with("video/") {
        return "video";
    }
    if matches!(s.as_str(), ".zip" | ".rar" | ".7z" | ".tar" | ".gz") {
        return "archive";
    }
    if matches!(
        s.as_str(),
        ".md" | ".txt" | ".pdf" | ".doc" | ".docx" | ".csv" | ".json"
    ) {
        return "document";
    }
    "file"
}

fn guess_mime(name: &str) -> String {
    let s = name.to_lowercase();
    let ext = Path::new(&s).extension().and_then(|e| e.to_str()).unwrap_or("");
    match ext {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "svg" => "image/svg+xml",
        "mp4" => "video/mp4",
        "webm" => "video/webm",
        "mp3" => "audio/mpeg",
        "wav" => "audio/wav",
        "pdf" => "application/pdf",
        "json" => "application/json",
        "txt" | "md" => "text/plain",
        "csv" => "text/csv",
        "zip" => "application/zip",
        _ => "",
    }
    .to_string()
}

/// Python `list_dir`。
pub async fn list_dir(
    pool: &PgPool,
    user_id: i64,
    rel_path: &str,
    limit: Option<usize>,
    cursor: Option<&str>,
) -> PlatformResult<LibraryListing> {
    let _ = pool; // 仅文件系统列表,不查 DB。保留 pool 参数与其它接口一致。
    let root = user_root(user_id);
    let current = safe_path(&root, rel_path)?;
    std::fs::create_dir_all(&current)?;
    let mut entries = Vec::new();
    if let Ok(rd) = std::fs::read_dir(&current) {
        let mut items: Vec<_> = rd.flatten().collect();
        items.sort_by(|a, b| {
            let a_dir = a.path().is_dir();
            let b_dir = b.path().is_dir();
            b_dir
                .cmp(&a_dir)
                .then(a.file_name().to_string_lossy().to_lowercase().cmp(
                    &b.file_name().to_string_lossy().to_lowercase(),
                ))
        });
        for it in items {
            let p = it.path();
            let md = match it.metadata() {
                Ok(m) => m,
                Err(_) => continue,
            };
            let rel = p
                .strip_prefix(&root)
                .map(|r| r.to_string_lossy().into_owned())
                .unwrap_or_default();
            let mime = guess_mime(p.file_name().and_then(|s| s.to_str()).unwrap_or(""));
            let modified = md
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs() as i64)
                .unwrap_or(0);
            entries.push(LibraryEntry {
                name: p.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default(),
                path: rel,
                r#type: if md.is_dir() { "directory" } else { "file" },
                size: md.len(),
                mime,
                modified,
            });
        }
    }
    if let Some(c) = cursor {
        entries.retain(|e| e.path.as_str() > c);
    }
    let page_limit = limit.unwrap_or(50).clamp(1, 200);
    let has_more = entries.len() > page_limit;
    let visible: Vec<_> = entries.into_iter().take(page_limit).collect();
    let path_field = if current == root {
        String::new()
    } else {
        current
            .strip_prefix(&root)
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default()
    };
    let next_cursor = if has_more {
        visible.last().map(|e| e.path.clone())
    } else {
        None
    };
    Ok(LibraryListing {
        engine: "fsspec-local",
        path: path_field,
        entries: visible,
        page: PageMeta {
            limit: page_limit,
            next_cursor,
            has_more,
        },
    })
}

/// Python `mkdir`。
pub async fn mkdir(pool: &PgPool, user_id: i64, rel_path: &str) -> PlatformResult<LibraryListing> {
    let root = user_root(user_id);
    let target = safe_path(&root, rel_path)?;
    std::fs::create_dir_all(&target)?;
    let rel = parent_rel(&root, &target);
    list_dir(pool, user_id, &rel, None, None).await
}

/// Python `delete`。
pub async fn delete(pool: &PgPool, user_id: i64, rel_path: &str) -> PlatformResult<LibraryListing> {
    let root = user_root(user_id);
    let target = safe_path(&root, rel_path)?;
    if target == root {
        return Err(PlatformError::validation("不能删除库根目录"));
    }
    if !target.exists() {
        return Err(PlatformError::not_found(format!("文件不存在: {}", rel_path)));
    }
    if target.is_dir() {
        std::fs::remove_dir_all(&target)?;
    } else {
        std::fs::remove_file(&target)?;
    }
    let store_rel = rel_path.replace('\\', "/");
    sqlx::query("delete from assets where user_id = $1 and rel_path = $2")
        .bind(user_id)
        .bind(&store_rel)
        .execute(pool)
        .await?;
    let parent = parent_rel(&root, &target);
    list_dir(pool, user_id, &parent, None, None).await
}

fn decode_upload(item: &UploadItem) -> PlatformResult<Vec<u8>> {
    let mut encoded = item.base64.trim().to_string();
    if encoded.is_empty() && !item.data_url.is_empty() {
        if let Some((_, rest)) = item.data_url.split_once(',') {
            encoded = rest.trim().to_string();
        }
    }
    if encoded.is_empty() {
        return Err(PlatformError::validation("上传内容为空"));
    }
    general_purpose::STANDARD
        .decode(encoded.as_bytes())
        .map_err(|_| PlatformError::validation("上传内容不是有效 base64"))
}

/// Python `upload`。
pub async fn upload(
    pool: &PgPool,
    user_id: i64,
    rel_dir: &str,
    files: Vec<UploadItem>,
) -> PlatformResult<LibraryListing> {
    if files.is_empty() {
        return Err(PlatformError::validation("files 必须是非空列表"));
    }
    if files.len() > MAX_FILES_PER_REQUEST {
        return Err(PlatformError::validation(format!(
            "单次最多上传 {} 个文件,本次提交 {}",
            MAX_FILES_PER_REQUEST,
            files.len()
        )));
    }
    let root = user_root(user_id);
    let target_dir = safe_path(&root, rel_dir)?;
    std::fs::create_dir_all(&target_dir)?;
    for item in &files {
        let raw_name = if item.name.is_empty() {
            "upload.bin".to_string()
        } else {
            item.name.clone()
        };
        let name = safe_filename(&raw_name);
        let data = decode_upload(item)?;
        if data.len() > MAX_UPLOAD_BYTES {
            return Err(PlatformError::validation(format!("文件过大:{}", name)));
        }
        let target = unique_path(&target_dir.join(&name))?;
        std::fs::write(&target, &data)?;
        let final_name = target
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or(&name)
            .to_string();
        let suffix = Path::new(&final_name)
            .extension()
            .and_then(|s| s.to_str())
            .map(|s| format!(".{}", s))
            .unwrap_or_default();
        let mime = if !item.mime_type.is_empty() {
            item.mime_type.clone()
        } else {
            let g = guess_mime(&final_name);
            if g.is_empty() {
                "application/octet-stream".to_string()
            } else {
                g
            }
        };
        let kind = kind_for(&mime, &suffix);
        let rel = target
            .strip_prefix(&root)
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default();
        sqlx::query(
            "insert into assets(user_id, name, rel_path, mime, kind, size) \
             values ($1, $2, $3, $4, $5, $6)",
        )
        .bind(user_id)
        .bind(&final_name)
        .bind(&rel)
        .bind(&mime)
        .bind(kind)
        .bind(data.len() as i64)
        .execute(pool)
        .await?;
    }
    list_dir(pool, user_id, rel_dir, None, None).await
}

/// Python `download_path` —— 返回绝对路径,供 axum 用 `StreamBody` / `tower-http` 输出。
pub fn download_path(user_id: i64, rel_path: &str) -> PlatformResult<PathBuf> {
    let target = safe_path(&user_root(user_id), rel_path)?;
    if !target.exists() || !target.is_file() {
        return Err(PlatformError::not_found("文件不存在"));
    }
    Ok(target)
}

// ─── Script (剧本库) CRUD ──────────────────────────────────────────────
//
// Python 的 library.py 只管 `assets`;剧本(scripts 表)CRUD 在 Python 散落在
// api/scripts 与 import_pipeline。这里把基础 list/get/create/update/delete
// 集中起来,供 routes 复用。

/// `scripts` 行。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Script {
    pub id: i64,
    pub owner_id: i64,
    pub title: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub source_path: String,
    #[serde(default)]
    pub metadata: Value,
}

fn script_from_row(row: &sqlx::postgres::PgRow) -> sqlx::Result<Script> {
    Ok(Script {
        id: row.try_get("id")?,
        owner_id: row.try_get("owner_id")?,
        title: row.try_get("title")?,
        description: row.try_get::<String, _>("description").unwrap_or_default(),
        source_path: row.try_get::<String, _>("source_path").unwrap_or_default(),
        metadata: row
            .try_get::<Value, _>("metadata")
            .unwrap_or(Value::Object(Default::default())),
    })
}

/// 列出 user 所有剧本。
pub async fn list_scripts(pool: &PgPool, owner_id: i64) -> PlatformResult<Vec<Script>> {
    let rows = sqlx::query(
        "select id, owner_id, title, description, source_path, \
         '{}'::jsonb as metadata \
         from scripts where owner_id = $1 order by id desc",
    )
    .bind(owner_id)
    .fetch_all(pool)
    .await?;
    rows.iter()
        .map(script_from_row)
        .collect::<Result<Vec<_>, _>>()
        .map_err(Into::into)
}

/// 取单条剧本,鉴权用 owner_id。未找到返回 `None`。
pub async fn get_script(
    pool: &PgPool,
    owner_id: i64,
    script_id: i64,
) -> PlatformResult<Option<Script>> {
    let row = sqlx::query(
        "select id, owner_id, title, description, source_path, \
         '{}'::jsonb as metadata \
         from scripts where id = $1 and owner_id = $2",
    )
    .bind(script_id)
    .bind(owner_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(|r| script_from_row(&r)).transpose()?)
}

/// 新建剧本。title 为空抛 validation。
pub async fn create_script(
    pool: &PgPool,
    owner_id: i64,
    title: &str,
    description: &str,
    source_path: &str,
) -> PlatformResult<Script> {
    let title = title.trim();
    if title.is_empty() {
        return Err(PlatformError::validation("title 不能为空"));
    }
    let row = sqlx::query(
        "insert into scripts(owner_id, title, description, source_path) \
         values ($1, $2, $3, $4) \
         returning id, owner_id, title, description, source_path, \
                   '{}'::jsonb as metadata",
    )
    .bind(owner_id)
    .bind(title)
    .bind(description)
    .bind(source_path)
    .fetch_one(pool)
    .await?;
    Ok(script_from_row(&row)?)
}

/// 更新剧本(只动 title/description)。
pub async fn update_script(
    pool: &PgPool,
    owner_id: i64,
    script_id: i64,
    title: Option<&str>,
    description: Option<&str>,
) -> PlatformResult<Script> {
    let existing = get_script(pool, owner_id, script_id)
        .await?
        .ok_or_else(|| PlatformError::not_found("剧本不存在"))?;
    let new_title = title.unwrap_or(&existing.title);
    let new_desc = description.unwrap_or(&existing.description);
    sqlx::query(
        "update scripts set title = $1, description = $2, updated_at = now() \
         where id = $3 and owner_id = $4",
    )
    .bind(new_title)
    .bind(new_desc)
    .bind(script_id)
    .bind(owner_id)
    .execute(pool)
    .await?;
    get_script(pool, owner_id, script_id)
        .await?
        .ok_or_else(|| PlatformError::not_found("剧本不存在"))
}

/// 删除剧本(级联会带走 saves/commits)。
pub async fn delete_script(
    pool: &PgPool,
    owner_id: i64,
    script_id: i64,
) -> PlatformResult<bool> {
    let res = sqlx::query("delete from scripts where id = $1 and owner_id = $2")
        .bind(script_id)
        .bind(owner_id)
        .execute(pool)
        .await?;
    Ok(res.rows_affected() > 0)
}

// ─── archive 自动解压 ──────────────────────────────────────────────────────
//
// Python library.py 里 `kind_for` 把 .zip/.gz 等标记为 "archive";
// 前端上传时若 kind="archive" 则调用下面的 extract_archive 把内容展开到同目录。
// 对应 Python `upload` 后的 archive 处理逻辑(Python 端用 fsspec 没做展开,
// 这是 Rust 端扩展功能)。

/// 把已上传的 zip 归档展开到同目录下的同名子文件夹。
///
/// 例: `images/photos.zip` → `images/photos/` 下各文件。
/// 展开后原 zip 保留(与 Python 行为一致:不删原文件)。
///
/// 返回展开出的文件数。若不是有效 zip,返回 `Err`。
pub fn extract_zip_archive(archive_path: &Path, dest_dir: &Path) -> PlatformResult<usize> {
    std::fs::create_dir_all(dest_dir)?;
    let file = std::fs::File::open(archive_path)?;
    let mut zip = zip::ZipArchive::new(file)
        .map_err(|e| PlatformError::validation(format!("无效 zip 归档: {e}")))?;
    let total = zip.len();
    for i in 0..total {
        let mut entry = zip
            .by_index(i)
            .map_err(|e| PlatformError::validation(format!("zip 读取错误: {e}")))?;
        // 安全:规范化 entry 名称,防 zip-slip 路径穿越。
        let raw_name = entry.name().replace('\\', "/");
        let safe: PathBuf = raw_name
            .split('/')
            .filter(|c| !c.is_empty() && *c != ".." && *c != ".")
            .collect();
        if safe.as_os_str().is_empty() {
            continue;
        }
        let out_path = dest_dir.join(&safe);
        if entry.is_dir() {
            std::fs::create_dir_all(&out_path)?;
        } else {
            if let Some(parent) = out_path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            let mut out_file = std::fs::File::create(&out_path)?;
            std::io::copy(&mut entry, &mut out_file)?;
        }
    }
    Ok(total)
}

/// 上传后若 kind=="archive" 且 suffix==".zip",自动展开到同名子目录。
///
/// 展开失败只记 warning,不影响上传结果(对应 Python 宽松处理原则)。
pub async fn maybe_extract_archive(
    root: &Path,
    target_path: &Path,
    kind: &str,
    suffix: &str,
) {
    if kind != "archive" {
        return;
    }
    let s = suffix.to_lowercase();
    if s != ".zip" {
        // tar/gz 留 TODO;目前只展开 zip。
        return;
    }
    let stem = target_path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("archive");
    let parent = target_path.parent().unwrap_or(root);
    let dest = parent.join(stem);
    if let Err(e) = extract_zip_archive(target_path, &dest) {
        tracing::warn!("archive 自动展开失败 {:?}: {e}", target_path);
    }
}

// ─── metadata patch ────────────────────────────────────────────────────────
//
// 对应 Python 端 assets 表 metadata 字段的局部更新(Python 里直接 UPDATE)。
// 这里提供单条 patch:把 `patch` 对象的字段合并到 assets.metadata (jsonb merge)。

/// 把 `patch` 里的字段合并写入 `assets.metadata`。
///
/// SQL: `metadata = metadata || $patch`  ── PostgreSQL jsonb concat 操作符。
/// 若该 asset 不存在(user_id+rel_path 不匹配)返回 `false`。
pub async fn patch_asset_metadata(
    pool: &PgPool,
    user_id: i64,
    rel_path: &str,
    patch: &serde_json::Value,
) -> PlatformResult<bool> {
    let res = sqlx::query(
        "update assets set metadata = coalesce(metadata, '{}'::jsonb) || $1::jsonb \
         where user_id = $2 and rel_path = $3",
    )
    .bind(patch)
    .bind(user_id)
    .bind(rel_path)
    .execute(pool)
    .await?;
    Ok(res.rows_affected() > 0)
}

// ─── tests ─────────────────────────────────────────────────────────────────
#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    /// 构造一个最小 zip 文件并验证 extract_zip_archive 能展开。
    #[test]
    fn extract_zip_creates_files() {
        let tmp = std::env::temp_dir().join(format!("rpg_lib_test_{}", std::process::id()));
        std::fs::create_dir_all(&tmp).unwrap();
        let zip_path = tmp.join("test.zip");
        // 写一个含单个文件的 zip
        {
            let file = std::fs::File::create(&zip_path).unwrap();
            let mut zw = zip::ZipWriter::new(file);
            zw.start_file("hello.txt", zip::write::SimpleFileOptions::default()).unwrap();
            zw.write_all(b"hello zip").unwrap();
            zw.finish().unwrap();
        }
        let dest = tmp.join("test");
        let count = extract_zip_archive(&zip_path, &dest).unwrap();
        assert!(count >= 1, "应展开至少 1 个条目");
        assert!(dest.join("hello.txt").exists(), "hello.txt 应被展开");
        let content = std::fs::read_to_string(dest.join("hello.txt")).unwrap();
        assert_eq!(content, "hello zip");
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// zip-slip 路径穿越应被过滤。
    #[test]
    fn extract_zip_blocks_zip_slip() {
        let tmp = std::env::temp_dir().join(format!("rpg_lib_slip_{}", std::process::id()));
        std::fs::create_dir_all(&tmp).unwrap();
        let zip_path = tmp.join("slip.zip");
        {
            let file = std::fs::File::create(&zip_path).unwrap();
            let mut zw = zip::ZipWriter::new(file);
            // 包含 `..` 的条目
            zw.start_file("../../evil.txt", zip::write::SimpleFileOptions::default()).unwrap();
            zw.write_all(b"evil").unwrap();
            zw.finish().unwrap();
        }
        let dest = tmp.join("safe");
        std::fs::create_dir_all(&dest).unwrap();
        let _ = extract_zip_archive(&zip_path, &dest);
        // evil.txt 不应出现在 dest 以外
        assert!(!tmp.parent().unwrap_or(&tmp).join("evil.txt").exists());
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn safe_filename_handles_special() {
        assert_eq!(safe_filename("hello world.png"), "hello world.png");
        assert_eq!(safe_filename("../evil"), "evil");
        assert_eq!(safe_filename(""), "file.bin");
    }
}
