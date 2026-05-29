//! tavern_cards —— SillyTavern V1/V2 角色卡 import/export。
//!
//! 对应 Python: `rpg/platform_app/tavern_cards.py`。
//!
//! 支持:
//! - 解析 V1(扁平)/ V2(`spec=chara_card_v2`,`data` 三层)JSON。
//! - 解析 PNG `tEXt` / `zTXt` chunk 里的 `chara` / `ccv3` 关键字嵌入卡。
//! - 反向:把内部 user_card → V2 JSON、嵌回 PNG。
//!
//! 字段映射详见 `tavern_to_user_card`。

use base64::{engine::general_purpose, Engine};
use byteorder::{BigEndian, ByteOrder};
use flate2::read::ZlibDecoder;
use flate2::write::ZlibEncoder;
use flate2::Compression;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{Read, Write};

use crate::error::{PlatformError, PlatformResult};

const PNG_SIGNATURE: [u8; 8] = [0x89, b'P', b'N', b'G', b'\r', b'\n', 0x1a, b'\n'];

/// V2 卡 wrapper(`{ spec, spec_version, data }`)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TavernCard {
    pub spec: String,
    pub spec_version: String,
    pub data: TavernData,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TavernData {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub personality: String,
    #[serde(default)]
    pub scenario: String,
    #[serde(default)]
    pub first_mes: String,
    #[serde(default)]
    pub mes_example: String,
    #[serde(default)]
    pub creator_notes: String,
    #[serde(default)]
    pub system_prompt: String,
    #[serde(default)]
    pub post_history_instructions: String,
    #[serde(default)]
    pub alternate_greetings: Vec<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub creator: String,
    #[serde(default)]
    pub character_version: String,
    #[serde(default)]
    pub extensions: Value,
    #[serde(default)]
    pub character_book: Value,
}

fn s(v: Option<&Value>) -> String {
    v.and_then(|x| x.as_str()).map(|s| s.to_string()).unwrap_or_default()
}

fn list_of_str(v: Option<&Value>) -> Vec<String> {
    v.and_then(|x| x.as_array())
        .map(|arr| arr.iter().filter_map(|i| i.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default()
}

/// 入口:吃 JSON `Value` / 字符串 / 字节,返回标准化 V2。
pub fn parse_card_value(data: &Value) -> PlatformResult<TavernCard> {
    if !data.is_object() {
        return Err(PlatformError::validation(format!(
            "不支持的角色卡类型: {}",
            data
        )));
    }
    let spec = data.get("spec").and_then(|s| s.as_str()).unwrap_or("");
    if spec == "chara_card_v2" || spec == "chara_card_v3" {
        normalize_v2(data)
    } else {
        v1_to_v2(data)
    }
}

/// 从字符串解析(裸 JSON 或 base64(JSON))。
pub fn parse_card_str(text: &str) -> PlatformResult<TavernCard> {
    let stripped = text.trim();
    if stripped.starts_with('{') {
        let v: Value = serde_json::from_str(stripped)?;
        return parse_card_value(&v);
    }
    let decoded = general_purpose::STANDARD
        .decode(stripped.as_bytes())
        .map_err(|e| PlatformError::validation(format!("无法解析角色卡(非 JSON 也非 base64): {e}")))?;
    let text2 = String::from_utf8(decoded)
        .map_err(|e| PlatformError::validation(format!("无法解析角色卡(utf8): {e}")))?;
    let v: Value = serde_json::from_str(&text2)?;
    parse_card_value(&v)
}

fn normalize_v2(card: &Value) -> PlatformResult<TavernCard> {
    let d = card.get("data").cloned().unwrap_or(Value::Null);
    let do_obj = d.as_object();
    let name = s(do_obj.and_then(|o| o.get("name")));
    let name = name.trim().to_string();
    if name.is_empty() {
        return Err(PlatformError::validation("角色卡缺少 name"));
    }
    let spec = card
        .get("spec")
        .and_then(|s| s.as_str())
        .unwrap_or("chara_card_v2")
        .to_string();
    let spec_version = card
        .get("spec_version")
        .and_then(|s| s.as_str())
        .unwrap_or("2.0")
        .to_string();
    let data = TavernData {
        name,
        description: s(do_obj.and_then(|o| o.get("description"))),
        personality: s(do_obj.and_then(|o| o.get("personality"))),
        scenario: s(do_obj.and_then(|o| o.get("scenario"))),
        first_mes: s(do_obj.and_then(|o| o.get("first_mes"))),
        mes_example: s(do_obj.and_then(|o| o.get("mes_example"))),
        creator_notes: s(do_obj.and_then(|o| o.get("creator_notes"))),
        system_prompt: s(do_obj.and_then(|o| o.get("system_prompt"))),
        post_history_instructions: s(do_obj.and_then(|o| o.get("post_history_instructions"))),
        alternate_greetings: list_of_str(do_obj.and_then(|o| o.get("alternate_greetings"))),
        tags: list_of_str(do_obj.and_then(|o| o.get("tags"))),
        creator: s(do_obj.and_then(|o| o.get("creator"))),
        character_version: s(do_obj.and_then(|o| o.get("character_version"))),
        extensions: do_obj
            .and_then(|o| o.get("extensions"))
            .cloned()
            .unwrap_or(Value::Object(Default::default())),
        character_book: do_obj
            .and_then(|o| o.get("character_book"))
            .cloned()
            .unwrap_or(Value::Null),
    };
    Ok(TavernCard {
        spec,
        spec_version,
        data,
    })
}

fn v1_to_v2(card: &Value) -> PlatformResult<TavernCard> {
    let name_raw = s(card.get("name"));
    let name = if name_raw.is_empty() {
        s(card.get("char_name"))
    } else {
        name_raw
    };
    let name = name.trim().to_string();
    if name.is_empty() {
        return Err(PlatformError::validation("V1 角色卡缺少 name"));
    }
    let take = |a: &str, b: &str| -> String {
        let v = s(card.get(a));
        if v.is_empty() {
            s(card.get(b))
        } else {
            v
        }
    };
    let character_version = {
        let v = s(card.get("character_version"));
        if v.is_empty() { "1.0".to_string() } else { v }
    };
    let wrapped = json!({
        "spec": "chara_card_v1",
        "spec_version": "1.0",
        "data": {
            "name": name,
            "description": take("description", "char_persona"),
            "personality": s(card.get("personality")),
            "scenario": take("scenario", "world_scenario"),
            "first_mes": take("first_mes", "char_greeting"),
            "mes_example": take("mes_example", "example_dialogue"),
            "creator": s(card.get("creator")),
            "character_version": character_version,
            "tags": card.get("tags").cloned().unwrap_or(Value::Array(vec![])),
        }
    });
    normalize_v2(&wrapped)
}

// ─── PNG tEXt / zTXt 解析 ──────────────────────────────────────────────

/// 从 PNG 字节里读出嵌入的卡。
pub fn parse_png_card(blob: &[u8]) -> PlatformResult<TavernCard> {
    if blob.len() < 8 || blob[..8] != PNG_SIGNATURE {
        return Err(PlatformError::validation("不是合法 PNG 文件"));
    }
    let mut offset = 8usize;
    let mut text_chunks: HashMap<String, String> = HashMap::new();
    while offset + 8 <= blob.len() {
        let length = BigEndian::read_u32(&blob[offset..offset + 4]) as usize;
        let chunk_type = std::str::from_utf8(&blob[offset + 4..offset + 8])
            .unwrap_or("")
            .to_string();
        let body_start = offset + 8;
        if body_start + length + 4 > blob.len() {
            break;
        }
        let body = &blob[body_start..body_start + length];
        offset = body_start + length + 4; // skip CRC

        if chunk_type == "IEND" {
            break;
        }
        if chunk_type == "tEXt" {
            if let Some(idx) = body.iter().position(|&b| b == 0) {
                let key = String::from_utf8_lossy(&body[..idx]).into_owned();
                let val = String::from_utf8_lossy(&body[idx + 1..]).into_owned();
                text_chunks.insert(key, val);
            }
        } else if chunk_type == "zTXt" {
            if let Some(idx) = body.iter().position(|&b| b == 0) {
                let key = String::from_utf8_lossy(&body[..idx]).into_owned();
                // rest[0] = compression method, rest[1..] = compressed
                if idx + 2 <= body.len() {
                    let compressed = &body[idx + 2..];
                    let mut dec = ZlibDecoder::new(compressed);
                    let mut out = String::new();
                    if dec.read_to_string(&mut out).is_ok() {
                        text_chunks.insert(key, out);
                    }
                }
            }
        }
    }
    for key in ["ccv3", "chara"] {
        if let Some(raw) = text_chunks.get(key) {
            return parse_card_str(raw);
        }
    }
    Err(PlatformError::validation(
        "PNG 不包含 chara/ccv3 tEXt chunk",
    ))
}

// ─── Mapping: V2 ↔ user_character_cards payload ────────────────────────

/// V2 → `upsert_user_card` 用的 payload。返回 JSON `Value` 方便 user_cards 接收。
pub fn tavern_to_user_card(card: &TavernCard) -> Value {
    let d = &card.data;
    // 切 mes_example,提取首段 {{char}}: 后内容做 sample_dialogue。
    let mut samples: Vec<String> = Vec::new();
    'outer: for chunk in regex::Regex::new(r"<START>|---").unwrap().split(&d.mes_example) {
        let chunk = chunk.trim();
        if chunk.is_empty() {
            continue;
        }
        for line in chunk.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Some(rest) = line.strip_prefix("{{char}}:") {
                samples.push(rest.trim().to_string());
                if samples.len() >= 4 {
                    break 'outer;
                }
            }
        }
        if !samples.is_empty() {
            break;
        }
    }

    let truncate = |s: &str, n: usize| -> String {
        s.chars().take(n).collect()
    };
    json!({
        "name": d.name,
        "identity": truncate(&d.description, 2000),
        "personality": truncate(&d.personality, 1500),
        "speech_style": "",
        "current_status": "",
        "secrets": "",
        "sample_dialogue": samples,
        "tags": d.tags,
        "metadata": {
            "tavern_imported": true,
            "scenario": d.scenario,
            "first_mes": d.first_mes,
            "alternate_greetings": d.alternate_greetings,
            "creator_notes": d.creator_notes,
            "system_prompt": d.system_prompt,
            "post_history_instructions": d.post_history_instructions,
            "creator": d.creator,
            "character_version": d.character_version,
            "extensions": d.extensions,
            "character_book": d.character_book,
            "spec": card.spec,
            "spec_version": card.spec_version,
        }
    })
}

/// 反向:本人卡 → V2 JSON。
pub fn user_card_to_tavern_v2(card: &Value) -> TavernCard {
    let md = card
        .get("metadata")
        .cloned()
        .unwrap_or(Value::Object(Default::default()));
    let samples = card
        .get("sample_dialogue")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let mes_example = if !samples.is_empty() {
        samples
            .iter()
            .take(4)
            .filter_map(|s| s.as_str())
            .map(|s| format!("<START>\n{{{{user}}}}: \n{{{{char}}}}: {}", s))
            .collect::<Vec<_>>()
            .join("\n")
    } else {
        String::new()
    };
    let identity = {
        let v = s(card.get("identity"));
        if v.is_empty() { s(card.get("appearance")) } else { v }
    };
    let mes_example_md = s(md.get("mes_example"));
    let mes_example_final = if mes_example_md.is_empty() {
        mes_example
    } else {
        mes_example_md
    };
    TavernCard {
        spec: "chara_card_v2".to_string(),
        spec_version: "2.0".to_string(),
        data: TavernData {
            name: s(card.get("name")),
            description: identity,
            personality: s(card.get("personality")),
            scenario: s(md.get("scenario")),
            first_mes: s(md.get("first_mes")),
            mes_example: mes_example_final,
            creator_notes: s(md.get("creator_notes")),
            system_prompt: s(md.get("system_prompt")),
            post_history_instructions: s(md.get("post_history_instructions")),
            alternate_greetings: list_of_str(md.get("alternate_greetings")),
            tags: list_of_str(card.get("tags")),
            creator: s(md.get("creator")),
            character_version: {
                let v = s(md.get("character_version"));
                if v.is_empty() { "1.0".to_string() } else { v }
            },
            extensions: md
                .get("extensions")
                .cloned()
                .unwrap_or(Value::Object(Default::default())),
            character_book: md.get("character_book").cloned().unwrap_or(Value::Null),
        },
    }
}

// ─── PNG 嵌入导出 ──────────────────────────────────────────────────────

/// 把 V2 卡 JSON 嵌入 PNG 的 `tEXt chara` chunk。
pub fn write_png_card(card: &TavernCard, template_png: Option<&[u8]>) -> PlatformResult<Vec<u8>> {
    let png: Vec<u8> = match template_png {
        Some(t) if t.len() >= 8 && t[..8] == PNG_SIGNATURE => t.to_vec(),
        _ => minimal_png(),
    };

    let json_str = serde_json::to_string(card)?;
    let chara_b64 = general_purpose::STANDARD.encode(json_str.as_bytes());
    let mut chunk_data = Vec::new();
    chunk_data.extend_from_slice(b"chara");
    chunk_data.push(0);
    chunk_data.extend_from_slice(chara_b64.as_bytes());

    let mut text_chunk = Vec::new();
    let mut len_buf = [0u8; 4];
    BigEndian::write_u32(&mut len_buf, chunk_data.len() as u32);
    text_chunk.extend_from_slice(&len_buf);
    text_chunk.extend_from_slice(b"tEXt");
    text_chunk.extend_from_slice(&chunk_data);
    let mut hasher = crc32fast::Hasher::new();
    hasher.update(b"tEXt");
    hasher.update(&chunk_data);
    let crc = hasher.finalize();
    let mut crc_buf = [0u8; 4];
    BigEndian::write_u32(&mut crc_buf, crc);
    text_chunk.extend_from_slice(&crc_buf);

    // 在 IEND chunk 的 length 字段前插入。
    let iend_pos = png
        .windows(4)
        .rposition(|w| w == b"IEND")
        .ok_or_else(|| PlatformError::validation("template_png 没有 IEND chunk"))?;
    if iend_pos < 4 {
        return Err(PlatformError::validation("template_png IEND 偏移异常"));
    }
    let insert_at = iend_pos - 4;
    let mut out = Vec::with_capacity(png.len() + text_chunk.len());
    out.extend_from_slice(&png[..insert_at]);
    out.extend_from_slice(&text_chunk);
    out.extend_from_slice(&png[insert_at..]);
    Ok(out)
}

fn minimal_png() -> Vec<u8> {
    // 1x1 透明 RGBA。
    let mut out = Vec::new();
    out.extend_from_slice(&PNG_SIGNATURE);

    // IHDR
    let mut ihdr_data = Vec::with_capacity(13);
    let mut buf = [0u8; 4];
    BigEndian::write_u32(&mut buf, 1);
    ihdr_data.extend_from_slice(&buf);
    ihdr_data.extend_from_slice(&buf);
    ihdr_data.extend_from_slice(&[8, 6, 0, 0, 0]);
    push_chunk(&mut out, b"IHDR", &ihdr_data);

    // IDAT (zlib(filter byte + RGBA))
    let raw = [0u8; 5];
    let mut enc = ZlibEncoder::new(Vec::new(), Compression::default());
    enc.write_all(&raw).expect("zlib raw");
    let compressed = enc.finish().expect("zlib finish");
    push_chunk(&mut out, b"IDAT", &compressed);

    // IEND
    push_chunk(&mut out, b"IEND", &[]);
    out
}

fn push_chunk(out: &mut Vec<u8>, ty: &[u8; 4], data: &[u8]) {
    let mut len_buf = [0u8; 4];
    BigEndian::write_u32(&mut len_buf, data.len() as u32);
    out.extend_from_slice(&len_buf);
    out.extend_from_slice(ty);
    out.extend_from_slice(data);
    let mut hasher = crc32fast::Hasher::new();
    hasher.update(ty);
    hasher.update(data);
    let crc = hasher.finalize();
    let mut crc_buf = [0u8; 4];
    BigEndian::write_u32(&mut crc_buf, crc);
    out.extend_from_slice(&crc_buf);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn v1_minimal() {
        let v = json!({"name": "Alice", "description": "test"});
        let c = parse_card_value(&v).unwrap();
        assert_eq!(c.data.name, "Alice");
        assert_eq!(c.spec, "chara_card_v1");
    }

    #[test]
    fn png_roundtrip() {
        let card = TavernCard {
            spec: "chara_card_v2".to_string(),
            spec_version: "2.0".to_string(),
            data: TavernData { name: "Bob".to_string(), ..Default::default() },
        };
        let blob = write_png_card(&card, None).unwrap();
        let back = parse_png_card(&blob).unwrap();
        assert_eq!(back.data.name, "Bob");
    }
}
