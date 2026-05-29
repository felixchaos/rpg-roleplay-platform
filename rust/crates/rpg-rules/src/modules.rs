//! modules — 5E 模组 manifest 读取器(目录 → JSON 摘要)。
//!
//! 对应 Python: `rpg/modules/__init__.py`。
//!
//! 模组数据在 `rpg/modules/<id>/module.json` + 配套 `rooms.json` / `encounters.json` 等,
//! 翻译期 Rust 端只需要列出 manifest 与读 manifest+rooms 用于启动。
//!
//! 路径解析:`RPG_MODULES_DIR` 环境变量 > `./rpg/modules` 相对 cwd > 找不到时
//! 返回空列表(不报错,保证 GET /api/rules/modules 在测试 / 无数据集环境下也 200)。

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

/// 模组目录解析顺序:
/// 1. `RPG_MODULES_DIR` env(测试/部署可指定)
/// 2. 进程 cwd 下 `rpg/modules`(开发环境)
/// 3. 进程 cwd 下 `../rpg/modules`(在 rust/ 工作区里跑测试)
pub fn modules_dir() -> Option<PathBuf> {
    if let Ok(v) = std::env::var("RPG_MODULES_DIR") {
        let p = PathBuf::from(v);
        if p.is_dir() {
            return Some(p);
        }
    }
    let candidates = ["rpg/modules", "../rpg/modules", "../../rpg/modules"];
    for c in candidates {
        let p = PathBuf::from(c);
        if p.is_dir() {
            return Some(p);
        }
    }
    None
}

/// 列出模组目录下所有可用模组的摘要(对应 Python `list_modules()`)。
///
/// 每条记录抽取 `id` / `kind` / `name` / `name_cn` / `tagline` / `ruleset` /
/// `context_providers` / `level_range` / `estimated_minutes` / `path`。
/// 任何子目录缺 `module.json` 或 JSON 损坏 → 跳过(不抛错)。
pub fn list_modules() -> Vec<Value> {
    let Some(root) = modules_dir() else {
        return vec![];
    };
    let mut entries: Vec<PathBuf> = match fs::read_dir(&root) {
        Ok(rd) => rd
            .filter_map(|r| r.ok())
            .map(|d| d.path())
            .filter(|p| p.is_dir())
            .collect(),
        Err(_) => return vec![],
    };
    entries.sort();

    let mut out = Vec::with_capacity(entries.len());
    for sub in entries {
        let manifest_path = sub.join("module.json");
        if !manifest_path.exists() {
            continue;
        }
        let Ok(text) = fs::read_to_string(&manifest_path) else { continue };
        let Ok(data): Result<Value, _> = serde_json::from_str(&text) else { continue };

        // ruleset:兼容旧 dict 格式与新 string 格式
        let ruleset = match data.get("ruleset_meta").cloned() {
            Some(v) if !v.is_null() => v,
            _ => match data.get("ruleset").cloned() {
                Some(Value::String(s)) => json!({"id": s, "mode": s, "public_label": s}),
                Some(other) => other,
                None => Value::Null,
            },
        };

        let dir_name = sub
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string();

        out.push(json!({
            "id": data.get("id").and_then(|v| v.as_str()).unwrap_or(&dir_name),
            "kind": data.get("kind").and_then(|v| v.as_str()).unwrap_or("module_adventure"),
            "name": data.get("name").cloned().unwrap_or(Value::Null),
            "name_cn": data.get("name_cn").cloned().unwrap_or(Value::Null),
            "tagline": data.get("tagline").cloned().unwrap_or(Value::Null),
            "ruleset": ruleset,
            "context_providers": data.get("context_providers").cloned().unwrap_or(json!([])),
            "level_range": data.get("level_range").cloned().unwrap_or(Value::Null),
            "estimated_minutes": data.get("estimated_minutes").cloned().unwrap_or(Value::Null),
            "path": sub.to_string_lossy(),
        }));
    }
    out
}

/// 模组完整 bundle(对应 Python `load_module(module_id)`)。
#[derive(Debug, Clone, Default)]
pub struct ModuleBundle {
    pub id: String,
    pub manifest: Value,
    pub rooms: Value,
    pub encounters: Value,
    pub npcs: Value,
    pub loot: Value,
    pub worldbook: Value,
    pub opening: String,
}

/// 加载一个模组的所有 JSON/markdown 数据。
/// 找不到模组目录或 `module.json` → `Err(描述)`。
pub fn load_module(module_id: &str) -> Result<ModuleBundle, String> {
    let root = modules_dir().ok_or_else(|| "未配置模组目录(RPG_MODULES_DIR)".to_string())?;
    let sub = root.join(module_id);
    if !sub.exists() || !sub.is_dir() {
        return Err(format!("未知模组：{}", module_id));
    }
    let manifest = read_json(&sub, "module.json").unwrap_or(json!({}));
    if manifest.is_null() {
        return Err(format!("模组 {} 缺少 module.json", module_id));
    }
    Ok(ModuleBundle {
        id: module_id.to_string(),
        manifest,
        rooms: read_json(&sub, "rooms.json").unwrap_or(json!([])),
        encounters: read_json(&sub, "encounters.json").unwrap_or(json!([])),
        npcs: read_json(&sub, "npcs.json").unwrap_or(json!([])),
        loot: read_json(&sub, "loot.json").unwrap_or(json!([])),
        worldbook: read_json(&sub, "worldbook.json").unwrap_or(json!({})),
        opening: read_text(&sub, "opening.md"),
    })
}

fn read_json(dir: &Path, name: &str) -> Option<Value> {
    let p = dir.join(name);
    if !p.exists() {
        return None;
    }
    let txt = fs::read_to_string(&p).ok()?;
    serde_json::from_str(&txt).ok()
}

fn read_text(dir: &Path, name: &str) -> String {
    let p = dir.join(name);
    fs::read_to_string(&p).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    fn setup_modules_dir() -> TempDir {
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();

        // 完整 module
        let m1 = root.join("ash_mine");
        fs::create_dir_all(&m1).unwrap();
        fs::write(
            m1.join("module.json"),
            r#"{
                "id": "ash_mine",
                "kind": "module_adventure",
                "name": "Ash Mine",
                "name_cn": "灰烬矿坑",
                "tagline": "废弃矿道",
                "ruleset_meta": {"id": "dnd5e", "mode": "5e_compatible", "public_label": "5E"},
                "context_providers": ["module_scene"],
                "level_range": [1, 3],
                "estimated_minutes": 90
            }"#,
        )
        .unwrap();
        fs::write(m1.join("rooms.json"), r#"[{"id": "entrance", "name": "入口"}]"#).unwrap();
        fs::write(m1.join("opening.md"), "你站在矿洞前。").unwrap();

        // 只有 ruleset string 格式的 module
        let m2 = root.join("simple");
        fs::create_dir_all(&m2).unwrap();
        fs::write(
            m2.join("module.json"),
            r#"{"id": "simple", "name": "Simple", "ruleset": "5e_compatible"}"#,
        )
        .unwrap();

        // 损坏的 manifest → 应被跳过
        let m3 = root.join("broken");
        fs::create_dir_all(&m3).unwrap();
        fs::write(m3.join("module.json"), "{ this is not json").unwrap();

        // 没有 manifest 的目录 → 跳过
        let m4 = root.join("no_manifest");
        fs::create_dir_all(&m4).unwrap();

        std::env::set_var("RPG_MODULES_DIR", root);
        tmp
    }

    #[test]
    fn list_modules_returns_only_valid_entries() {
        let _g = setup_modules_dir();
        let mods = list_modules();
        // ash_mine + simple，broken/no_manifest 被跳过
        assert_eq!(mods.len(), 2, "应只列 2 个有效模组，实际：{mods:?}");
        let ids: Vec<&str> = mods.iter().map(|m| m["id"].as_str().unwrap_or("")).collect();
        assert!(ids.contains(&"ash_mine"));
        assert!(ids.contains(&"simple"));
    }

    #[test]
    fn list_modules_normalizes_string_ruleset() {
        let _g = setup_modules_dir();
        let mods = list_modules();
        let simple = mods
            .iter()
            .find(|m| m["id"] == "simple")
            .expect("simple 模组应存在");
        assert_eq!(simple["ruleset"]["id"], "5e_compatible");
        assert_eq!(simple["ruleset"]["mode"], "5e_compatible");
    }

    #[test]
    fn list_modules_keeps_dict_ruleset() {
        let _g = setup_modules_dir();
        let mods = list_modules();
        let ash = mods
            .iter()
            .find(|m| m["id"] == "ash_mine")
            .expect("ash_mine 模组应存在");
        assert_eq!(ash["ruleset"]["public_label"], "5E");
        assert_eq!(ash["name_cn"], "灰烬矿坑");
    }

    #[test]
    fn load_module_returns_bundle() {
        let _g = setup_modules_dir();
        let bundle = load_module("ash_mine").expect("ash_mine 应加载成功");
        assert_eq!(bundle.id, "ash_mine");
        assert_eq!(bundle.manifest["name_cn"], "灰烬矿坑");
        assert!(bundle.rooms.is_array());
        assert_eq!(bundle.opening, "你站在矿洞前。");
    }

    #[test]
    fn load_module_unknown_id_errors() {
        let _g = setup_modules_dir();
        let err = load_module("not_exist").unwrap_err();
        assert!(err.contains("未知模组"), "err={err}");
    }
}
