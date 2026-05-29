//! dice — 骰子表达式解析与掷骰。纯函数，可由 seed 控制。
//! 对应 Python: rpg/rules/dice.py

use once_cell::sync::Lazy;
use rand::{Rng, SeedableRng};
use rand::rngs::StdRng;
use regex::Regex;
use serde::{Deserialize, Serialize};
use thiserror::Error;

static EXPR_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)^\s*(\d+)?\s*d\s*(\d+)\s*(?:([+-])\s*(\d+))?\s*$").unwrap()
});

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RollResult {
    pub expression: String,
    pub rolls: Vec<i32>,
    pub modifier: i32,
    pub total: i32,
    pub advantage: bool,
    pub disadvantage: bool,
    /// d20 检定时记录两次原始骰，用于显示 / 审计
    #[serde(skip_serializing_if = "Option::is_none")]
    pub d20_raw: Option<Vec<i32>>,
}

#[derive(Debug, Error)]
pub enum DiceError {
    #[error("dice expression is None/empty")]
    EmptyExpression,
    #[error("无法解析骰子表达式：{0}")]
    ParseError(String),
    #[error("骰子参数非法：{0}")]
    InvalidParams(String),
    #[error("骰子参数过大：{0}")]
    ParamsTooLarge(String),
}

/// 解析 1d20+3 / 2d6 / d20-1 / 1d8 形态，返回 (count, sides, modifier)。
pub fn parse_expression(expression: &str) -> Result<(u32, u32, i32), DiceError> {
    if expression.is_empty() {
        return Err(DiceError::EmptyExpression);
    }
    let caps = EXPR_RE.captures(expression)
        .ok_or_else(|| DiceError::ParseError(expression.to_string()))?;

    let count: u32 = caps.get(1).map(|m| m.as_str().parse().unwrap_or(1)).unwrap_or(1);
    let sides: u32 = caps.get(2).map(|m| m.as_str().parse().unwrap_or(0)).unwrap_or(0);
    let sign = caps.get(3).map(|m| m.as_str()).unwrap_or("+");
    let mod_val: i32 = caps.get(4).map(|m| m.as_str().parse().unwrap_or(0)).unwrap_or(0);
    let modifier = if sign == "-" { -mod_val } else { mod_val };

    if count == 0 || sides == 0 {
        return Err(DiceError::InvalidParams(expression.to_string()));
    }
    if count > 100 || sides > 1000 {
        return Err(DiceError::ParamsTooLarge(expression.to_string()));
    }
    Ok((count, sides, modifier))
}

fn make_rng(seed: Option<u64>) -> StdRng {
    match seed {
        Some(s) => StdRng::seed_from_u64(s),
        None => StdRng::from_entropy(),
    }
}

/// 掷骰。advantage/disadvantage 仅对 d20 单骰生效。两者同时为 true 互相抵消。
pub fn roll(
    expression: &str,
    seed: Option<u64>,
    advantage: bool,
    disadvantage: bool,
) -> Result<RollResult, DiceError> {
    let (count, sides, modifier) = parse_expression(expression)?;
    let mut rng = make_rng(seed);

    let (mut adv, mut dis) = (advantage, disadvantage);
    if adv && dis {
        adv = false;
        dis = false;
    }

    let d20_raw: Option<Vec<i32>>;
    let rolls: Vec<i32>;

    if sides == 20 && count == 1 && (adv || dis) {
        let a = rng.gen_range(1..=20i32);
        let b = rng.gen_range(1..=20i32);
        d20_raw = Some(vec![a, b]);
        let chosen = if adv { a.max(b) } else { a.min(b) };
        rolls = vec![chosen];
    } else {
        d20_raw = None;
        rolls = (0..count).map(|_| rng.gen_range(1..=(sides as i32))).collect();
    }

    let total = rolls.iter().sum::<i32>() + modifier;
    Ok(RollResult {
        expression: expression.to_string(),
        rolls,
        modifier,
        total,
        advantage: adv,
        disadvantage: dis,
        d20_raw,
    })
}

/// d20 自然 20 视为暴击。
pub fn is_critical_hit(result: &RollResult) -> bool {
    if result.d20_raw.is_some() {
        return result.rolls.first() == Some(&20);
    }
    result.rolls == vec![20] && result.expression.to_lowercase().contains("d20")
}

pub fn is_critical_miss(result: &RollResult) -> bool {
    if result.d20_raw.is_some() {
        return result.rolls.first() == Some(&1);
    }
    result.rolls == vec![1] && result.expression.to_lowercase().contains("d20")
}
