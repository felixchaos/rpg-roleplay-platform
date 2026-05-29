//! path.rs — Python 风格 `a.b.c` / `a.b[0]` 路径访问器
//!
//! 对应 Python: `rpg/state/path_ops.py`
//! - `_clean_path` (中文别名 → 标准路径)
//! - `_set_path` / `_get_path` (dot-path 读写)
//!
//! Rust 侧实现:
//! - `PathSegment` 枚举:Key(String) | Index(usize)
//! - `parse_path` 解析 dot/bracket 混合语法
//! - `clean_path` 别名归一
//! - `get_path` / `set_path` / `delete_path` 用 `serde_json::Value` 直接读写
//!
//! serde_json::Value::pointer 只支持读 + RFC6901 (`/a/b/0`),所以这里全自己实现。

use once_cell::sync::Lazy;
use serde_json::Value;
use std::collections::HashMap;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum PathError {
    #[error("empty path")]
    Empty,
    #[error("invalid path syntax: {0}")]
    Syntax(String),
    #[error("path not found: {0}")]
    NotFound(String),
    #[error("type mismatch at segment {0}: expected {1}")]
    TypeMismatch(String, &'static str),
    #[error("index out of bounds at {0}")]
    IndexOutOfBounds(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PathSegment {
    Key(String),
    Index(usize),
}

/// 中文 / 英文别名归一 — 对应 `_clean_path`
static PATH_ALIASES: Lazy<HashMap<&'static str, &'static str>> = Lazy::new(|| {
    let mut m = HashMap::new();
    m.insert("姓名", "player.name");
    m.insert("角色", "player.role");
    m.insert("定位", "player.role");
    m.insert("背景", "player.background");
    m.insert("当前位置", "player.current_location");
    m.insert("位置", "player.current_location");
    m.insert("当前时间线", "world.time");
    m.insert("时间线", "world.time");
    m.insert("当前目标", "memory.current_objective");
    m.insert("目标", "memory.current_objective");
    m.insert("主线", "memory.main_quest");
    m.insert("记忆模式", "memory.mode");
    m.insert("权限", "permissions.mode");
    m
});

/// 归一化路径:剥离空白,中文别名替换。
pub fn clean_path(path: &str) -> String {
    let trimmed: String = path.trim().chars().filter(|c| !c.is_whitespace()).collect();
    PATH_ALIASES
        .get(trimmed.as_str())
        .map(|s| (*s).to_string())
        .unwrap_or(trimmed)
}

/// 解析 `a.b[0].c` → `[Key("a"), Key("b"), Index(0), Key("c")]`
pub fn parse_path(path: &str) -> Result<Vec<PathSegment>, PathError> {
    if path.is_empty() {
        return Err(PathError::Empty);
    }
    let mut segs = Vec::new();
    let mut buf = String::new();
    let mut chars = path.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '.' => {
                if !buf.is_empty() {
                    segs.push(PathSegment::Key(std::mem::take(&mut buf)));
                }
            }
            '[' => {
                if !buf.is_empty() {
                    segs.push(PathSegment::Key(std::mem::take(&mut buf)));
                }
                let mut idx_buf = String::new();
                let mut closed = false;
                for inner in chars.by_ref() {
                    if inner == ']' {
                        closed = true;
                        break;
                    }
                    idx_buf.push(inner);
                }
                if !closed {
                    return Err(PathError::Syntax(format!("unclosed `[` in {path}")));
                }
                let idx: usize = idx_buf
                    .parse()
                    .map_err(|_| PathError::Syntax(format!("bad index `{idx_buf}` in {path}")))?;
                segs.push(PathSegment::Index(idx));
            }
            _ => buf.push(c),
        }
    }
    if !buf.is_empty() {
        segs.push(PathSegment::Key(buf));
    }
    if segs.is_empty() {
        return Err(PathError::Empty);
    }
    Ok(segs)
}

/// 读取路径,不存在返回 None。
pub fn get_path<'a>(root: &'a Value, path: &str) -> Option<&'a Value> {
    let segs = parse_path(path).ok()?;
    get_path_segs(root, &segs)
}

/// 内部用 segs 形式的 get_path,供 typed_path dispatch 复用。
pub(crate) fn get_path_segs<'a>(root: &'a Value, segs: &[PathSegment]) -> Option<&'a Value> {
    let mut cur = root;
    for seg in segs {
        cur = match (cur, seg) {
            (Value::Object(map), PathSegment::Key(k)) => map.get(k)?,
            (Value::Array(arr), PathSegment::Index(i)) => arr.get(*i)?,
            // 兼容 Python:dict 上用数字 key 也可以(按 string 查)
            (Value::Object(map), PathSegment::Index(i)) => map.get(&i.to_string())?,
            _ => return None,
        };
    }
    Some(cur)
}

/// 写入路径。中间缺失节点自动创建为 Object;
/// 若路径中存在非 Object/Array 中间节点,直接覆盖为 Object。
pub fn set_path(root: &mut Value, path: &str, value: Value) -> Result<(), PathError> {
    let segs = parse_path(path)?;
    if segs.is_empty() {
        return Err(PathError::Empty);
    }
    set_path_segs(root, &segs, value)
}

/// 内部用 segs 形式的 set_path,供 typed_path dispatch 复用(避免再 parse 一遍)。
pub(crate) fn set_path_segs(root: &mut Value, segs: &[PathSegment], value: Value) -> Result<(), PathError> {
    let (last, parents) = segs.split_last().ok_or(PathError::Empty)?;
    // 保证 root 是 Object — Python 顶层就是 dict
    if !root.is_object() && !root.is_array() {
        *root = Value::Object(serde_json::Map::new());
    }
    let mut cur: &mut Value = root;
    for seg in parents {
        cur = descend_mut(cur, seg, /*create*/ true)?;
    }
    match last {
        PathSegment::Key(k) => {
            let obj = ensure_object(cur)?;
            obj.insert(k.clone(), value);
        }
        PathSegment::Index(i) => {
            let arr = ensure_array(cur)?;
            while arr.len() <= *i {
                arr.push(Value::Null);
            }
            arr[*i] = value;
        }
    }
    Ok(())
}

/// 删除路径,返回原值。不存在返回 None。
pub fn delete_path(root: &mut Value, path: &str) -> Result<Option<Value>, PathError> {
    let segs = parse_path(path)?;
    delete_path_segs(root, &segs)
}

pub(crate) fn delete_path_segs(
    root: &mut Value,
    segs: &[PathSegment],
) -> Result<Option<Value>, PathError> {
    let (last, parents) = segs.split_last().ok_or(PathError::Empty)?;
    let mut cur: &mut Value = root;
    for seg in parents {
        cur = match descend_mut(cur, seg, /*create*/ false) {
            Ok(v) => v,
            Err(_) => return Ok(None),
        };
    }
    let removed = match last {
        PathSegment::Key(k) => match cur {
            Value::Object(map) => map.remove(k),
            _ => None,
        },
        PathSegment::Index(i) => match cur {
            Value::Array(arr) if *i < arr.len() => Some(arr.remove(*i)),
            _ => None,
        },
    };
    Ok(removed)
}

/// 数组 append。路径如果不是 array,自动初始化为空数组。
pub fn append_path(root: &mut Value, path: &str, value: Value) -> Result<(), PathError> {
    let segs = parse_path(path)?;
    append_path_segs(root, &segs, value)
}

pub(crate) fn append_path_segs(
    root: &mut Value,
    segs: &[PathSegment],
    value: Value,
) -> Result<(), PathError> {
    let mut cur: &mut Value = root;
    for seg in segs {
        cur = descend_mut(cur, seg, /*create*/ true)?;
    }
    if !cur.is_array() {
        *cur = Value::Array(Vec::new());
    }
    if let Value::Array(arr) = cur {
        arr.push(value);
    }
    Ok(())
}

fn descend_mut<'a>(
    cur: &'a mut Value,
    seg: &PathSegment,
    create: bool,
) -> Result<&'a mut Value, PathError> {
    match seg {
        PathSegment::Key(k) => {
            if !cur.is_object() {
                if create {
                    *cur = Value::Object(serde_json::Map::new());
                } else {
                    return Err(PathError::TypeMismatch(k.clone(), "object"));
                }
            }
            let obj = cur.as_object_mut().expect("just ensured object");
            if !obj.contains_key(k) {
                if create {
                    obj.insert(k.clone(), Value::Object(serde_json::Map::new()));
                } else {
                    return Err(PathError::NotFound(k.clone()));
                }
            }
            Ok(obj.get_mut(k).expect("just inserted"))
        }
        PathSegment::Index(i) => {
            if !cur.is_array() {
                if create {
                    *cur = Value::Array(Vec::new());
                } else {
                    return Err(PathError::TypeMismatch(i.to_string(), "array"));
                }
            }
            let arr = cur.as_array_mut().expect("just ensured array");
            if *i >= arr.len() {
                if create {
                    while arr.len() <= *i {
                        arr.push(Value::Object(serde_json::Map::new()));
                    }
                } else {
                    return Err(PathError::IndexOutOfBounds(i.to_string()));
                }
            }
            Ok(arr.get_mut(*i).expect("just ensured"))
        }
    }
}

fn ensure_object(cur: &mut Value) -> Result<&mut serde_json::Map<String, Value>, PathError> {
    if !cur.is_object() {
        *cur = Value::Object(serde_json::Map::new());
    }
    Ok(cur.as_object_mut().expect("ensured object"))
}

fn ensure_array(cur: &mut Value) -> Result<&mut Vec<Value>, PathError> {
    if !cur.is_array() {
        *cur = Value::Array(Vec::new());
    }
    Ok(cur.as_array_mut().expect("ensured array"))
}
