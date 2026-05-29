#![allow(clippy::field_reassign_with_default)]
//! typed_path hot-path micro-benchmarks
//!
//! 覆盖三条 hot path:
//!   - get_path: 顶层标量 / typed 子树字段 / 深路径数组下标
//!   - set_path: 顶层标量 / typed 子树字段(含 round-trip)
//!   - push_audit: 直接 typed push,~ns 级;cap 200 触发 drain

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use rpg_schemas::{AuditEntry, GameStateData};
use rpg_state::typed_path;
use serde_json::json;

// ── fixture helpers ──────────────────────────────────────────────────────────

fn make_state_with_encounter() -> GameStateData {
    let mut data = GameStateData::default();
    data.turn = 42;
    data.encounter.round = 5;
    data.encounter.encounter_id = "enc-001".to_string();
    data.encounter.combatants = vec![
        json!({"name": "goblin", "hp": 10}),
        json!({"name": "ogre",   "hp": 50}),
    ];
    data
}

fn make_state_with_audit(n: usize) -> GameStateData {
    let mut data = GameStateData::default();
    for i in 0..n {
        typed_path::push_audit(
            &mut data,
            AuditEntry::blocked("gm", &format!("player.field{}", i), "hard_forbidden", i as u64),
        );
    }
    data
}

// ── get_path benches ─────────────────────────────────────────────────────────

fn bench_get_path_scalar(c: &mut Criterion) {
    let data = make_state_with_encounter();
    c.bench_function("get_path/scalar_turn", |b| {
        b.iter(|| {
            let v = typed_path::get_path(black_box(&data), black_box("turn"));
            black_box(v);
        });
    });
}

fn bench_get_path_scalar_is_new(c: &mut Criterion) {
    let data = make_state_with_encounter();
    c.bench_function("get_path/scalar_is_new", |b| {
        b.iter(|| {
            let v = typed_path::get_path(black_box(&data), black_box("is_new"));
            black_box(v);
        });
    });
}

fn bench_get_path_typed_subtree_field(c: &mut Criterion) {
    let data = make_state_with_encounter();
    c.bench_function("get_path/typed_subtree_encounter.round", |b| {
        b.iter(|| {
            let v = typed_path::get_path(black_box(&data), black_box("encounter.round"));
            black_box(v);
        });
    });
}

fn bench_get_path_deep_array_index(c: &mut Criterion) {
    let data = make_state_with_encounter();
    c.bench_function("get_path/deep_array_combatants[1].name", |b| {
        b.iter(|| {
            let v = typed_path::get_path(
                black_box(&data),
                black_box("encounter.combatants[1].name"),
            );
            black_box(v);
        });
    });
}

// ── set_path benches ─────────────────────────────────────────────────────────

fn bench_set_path_scalar(c: &mut Criterion) {
    c.bench_function("set_path/scalar_turn", |b| {
        b.iter(|| {
            let mut data = GameStateData::default();
            let _ = typed_path::set_path(black_box(&mut data), black_box("turn"), black_box(json!(99)));
        });
    });
}

fn bench_set_path_typed_subtree(c: &mut Criterion) {
    c.bench_function("set_path/typed_subtree_encounter.round", |b| {
        b.iter(|| {
            let mut data = GameStateData::default();
            let _ = typed_path::set_path(
                black_box(&mut data),
                black_box("encounter.round"),
                black_box(json!(3)),
            );
        });
    });
}

fn bench_set_path_player_character_hp(c: &mut Criterion) {
    c.bench_function("set_path/typed_subtree_player_character.hp", |b| {
        b.iter(|| {
            let mut data = GameStateData::default();
            let _ = typed_path::set_path(
                black_box(&mut data),
                black_box("player_character.hp"),
                black_box(json!(42)),
            );
        });
    });
}

// ── push_audit bench ─────────────────────────────────────────────────────────

fn bench_push_audit_no_cap(c: &mut Criterion) {
    c.bench_function("push_audit/no_cap (0→1 entries)", |b| {
        b.iter(|| {
            let mut data = GameStateData::default();
            typed_path::push_audit(
                black_box(&mut data),
                AuditEntry::blocked("gm", "player.name", "hard_forbidden", 1),
            );
            black_box(&data);
        });
    });
}

fn bench_push_audit_at_cap(c: &mut Criterion) {
    // 预填 200 条,每次 push 触发 drain(1 条)
    let data_template = make_state_with_audit(200);
    c.bench_function("push_audit/at_cap_drain (200→200 entries)", |b| {
        b.iter(|| {
            let mut data = data_template.clone();
            typed_path::push_audit(
                black_box(&mut data),
                AuditEntry::blocked("gm", "player.name", "hard_forbidden", 201),
            );
            black_box(&data);
        });
    });
}

/// 变参对比:push_audit 在不同 log 深度下的成本
fn bench_push_audit_varying_depth(c: &mut Criterion) {
    let mut group = c.benchmark_group("push_audit/varying_log_depth");
    for &initial_n in &[0usize, 50, 100, 199, 200] {
        let data_template = make_state_with_audit(initial_n);
        group.bench_with_input(
            BenchmarkId::from_parameter(initial_n),
            &initial_n,
            |b, _| {
                b.iter(|| {
                    let mut data = data_template.clone();
                    typed_path::push_audit(
                        black_box(&mut data),
                        AuditEntry::blocked("gm", "p", "hard_forbidden", 999),
                    );
                    black_box(&data);
                });
            },
        );
    }
    group.finish();
}

criterion_group!(
    benches,
    bench_get_path_scalar,
    bench_get_path_scalar_is_new,
    bench_get_path_typed_subtree_field,
    bench_get_path_deep_array_index,
    bench_set_path_scalar,
    bench_set_path_typed_subtree,
    bench_set_path_player_character_hp,
    bench_push_audit_no_cap,
    bench_push_audit_at_cap,
    bench_push_audit_varying_depth,
);
criterion_main!(benches);
