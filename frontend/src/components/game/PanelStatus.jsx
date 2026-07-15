/* 游戏台状态面板(状态 tab)—— 纯机械从 game-panels.jsx 搬出,零行为变化。 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import { InlineEditField } from './InlineEditField.jsx';

// ── PanelStatus —— content-pack-aware 状态栏 ─────────────────────────
//
// Codex 评审定调:状态栏不是 backend state 的镜像,而是当前玩法模式的"驾驶舱"。
// 同一个组件,不同 profile;profile 由 content_pack.kind / scene.module_id 决定:
//
//   module_adventure (Ash Mine 等 5E 模组):
//     · 玩家 (Lv/Class/HP/AC/状态) — 数据源 player_character
//     · 冒险现场 (当前房间 + tagline + 目标) — scene.current_room + module_manifest
//     · 可见线索 — scene.current_room.visible_clues
//     · 出口 — scene.current_room.exits
//     · 资源 (背包) — player_character.inventory **不是** player.inventory
//     · 战斗 — 仅 encounter.active 时显示;round/当前行动/敌人 HP
//     · 最近裁定 — dice_log 最后一条
//   novel_adaptation / freeform:
//     · 玩家 (姓名/身份/所在) — player.{name,role,current_location,background}
//     · 当下世界 (时刻/天气/事件) — world.{time,weather,timeline}
//     · 身上之物 — player.inventory
//     · 本轮已知事件 — world.known_events
//
// 历史 bug (用户截图):
//   1) Ash Mine 标题写"当下世界" — 该是"冒险现场"
//   2) "身上之物 0 件" — Cinder 实际有短剑/短弓/火把,数据在 player_character.inventory
//   3) "身份: 5E 探险者" — 该是 "Lv1 探险者 · HP 10/10 · AC 14"
//   4) "本轮已知事件" 混入未经 RulesEngine 裁定的"遭遇灰布教徒并展开战斗"
// 全部由 profile 切换治本。

function _statusProfileFor(state) {
  const cp = (state && state.content_pack) || {};
  const scene = (state && state.scene) || {};
  if (cp.kind === "module_adventure" || scene.module_id) return "module";
  if (cp.kind === "novel_adaptation") return "novel";
  return "freeform";  // 渲染层与 novel 共用 NovelStatusProfile
}

function ModuleStatusProfile({ state }) {
  const { t } = useTranslation();
  const pc = (state && state.player_character) || {};
  const scene = (state && state.scene) || {};
  const room = scene.current_room || {};
  const manifest = scene.module_manifest || {};
  const encounter = (state && state.encounter) || {};
  const diceLog = Array.isArray(state && state.dice_log) ? state.dice_log : [];
  const memory = (state && state.memory) || {};
  // 5E 模组的背包真值源:player_character.inventory(由 rules engine 维护)。
  // 旧 PanelStatus 误读 player.inventory → "0 件" 显示错误。
  const inventory = Array.isArray(pc.inventory) ? pc.inventory : [];
  const conditions = Array.isArray(pc.conditions) && pc.conditions.length
    ? pc.conditions.join(" · ")
    : t('game.status.condition_normal');
  const hpPct = pc.max_hp > 0 ? Math.max(0, Math.min(100, Math.round(100 * (pc.hp || 0) / pc.max_hp))) : 0;
  const lastRoll = diceLog.length ? diceLog[diceLog.length - 1] : null;
  const liveEnemies = (encounter.combatants || []).filter(c => c && c.side === "enemy" && !c.defeated);
  const turnActor = (() => {
    if (!encounter.active) return null;
    const order = encounter.initiative_order || [];
    const idx = encounter.turn_index || 0;
    if (!order.length || idx >= order.length) return null;
    return order[idx];
  })();
  return (
    <div className="gp-stack">
      {/* 玩家 — 5E 字段 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>{t('game.status.player')}</h3>
          <span className="pill"><span className="dot ok" /> {pc.class_name || "—"}</span>
        </div>
        <div className="gp-kv">
          <div className="gp-row">
            <span className="gp-label">{t('game.status.name')}</span>
            <strong>
              {pc.display_name || pc.name || "—"}
              {pc.level ? ` · Lv${pc.level}` : ""}
              {pc.class_name ? ` ${pc.class_name}` : ""}
            </strong>
          </div>
          <div className="gp-row">
            <span className="gp-label">{t('game.status.hp')}</span>
            <span className="mono">{pc.hp ?? "—"}/{pc.max_hp ?? "—"} {pc.max_hp > 0 ? `(${hpPct}%)` : ""}</span>
          </div>
          <div className="gp-row">
            <span className="gp-label">{t('game.status.ac')}</span>
            <span className="mono">{pc.ac ?? "—"}</span>
          </div>
          <div className="gp-row">
            <span className="gp-label">{t('game.status.condition')}</span>
            <span>{conditions}</span>
          </div>
        </div>
      </div>

      {/* 冒险现场 — 当前房间 + 目标 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>{t('game.status.adventure_scene')}</h3>
          {encounter.active
            ? <span className="pill" style={{color:"var(--danger)"}}><span className="dot" style={{background:"var(--danger)"}}/> {t('game.status.in_combat')}</span>
            : <span className="pill ok"><span className="dot ok" /> {t('game.status.exploring')}</span>}
        </div>
        <div className="gp-kv">
          <div className="gp-row">
            <span className="gp-label">{t('game.status.position')}</span>
            <strong>{room.name || scene.location_id || "—"}</strong>
          </div>
          {(memory.current_objective || manifest.tagline) ? (
            <div className="gp-row">
              <span className="gp-label">{t('game.status.objective')}</span>
              <span style={{fontStyle:"italic"}}>{memory.current_objective || manifest.tagline}</span>
            </div>
          ) : null}
        </div>
        {room.description ? (
          <p className="gp-bio">{room.description}</p>
        ) : null}
      </div>

      {/* 可见线索 */}
      {(room.visible_clues && room.visible_clues.length) ? (
        <div className="gp-section">
          <div className="section-head">
            <h3>{t('game.status.visible_clues')}</h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>{room.visible_clues.length}</span>
          </div>
          <ul className="gp-flat-list">
            {room.visible_clues.map((c, i) => (
              <li key={c.id || i}>
                <span>{(c && c.text) || c.id || t('game.status.clue_label')}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* 出口 */}
      {(room.exits && room.exits.length) ? (
        <div className="gp-section">
          <div className="section-head">
            <h3>{t('game.status.exits')}</h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>{room.exits.length}</span>
          </div>
          <ul className="gp-flat-list">
            {room.exits.map((ex, i) => (
              <li key={ex.to || i}>
                <span>{(ex && ex.label) || ex.to || t('game.status.exit_label')}</span>
                <span className="muted-2 mono" style={{fontSize: 11.5}}>{ex.to || ""}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* 资源 — 5E 背包 (player_character.inventory) */}
      <div className="gp-section">
        <div className="section-head">
          <h3>{t('game.status.resources')}</h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{t('game.status.items_count', { count: inventory.length })}</span>
        </div>
        {inventory.length === 0 ? (
          <p className="muted-2" style={{fontSize: 12.5, margin: "4px 0 0"}}>{t('game.status.backpack_empty')}</p>
        ) : (
          <ul className="gp-flat-list">
            {inventory.map((it, i) => (
              <li key={it.id || it.name || i}>
                <span>{(it && (it.name || it.id)) || t('game.status.unnamed_item')}</span>
                <span className="muted-2 mono" style={{fontSize: 11.5}}>
                  {(it && it.qty != null) ? `×${it.qty}` : (it && it.quality) || ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 战斗 — 仅 encounter.active 时显示 */}
      {encounter.active ? (
        <div className="gp-section">
          <div className="section-head">
            <h3>{t('game.status.combat')}</h3>
            <span className="pill" style={{color:"var(--danger)"}}>
              {t('game.status.round', { round: encounter.round || 1 })}
            </span>
          </div>
          {turnActor ? (
            <div className="gp-kv">
              <div className="gp-row">
                <span className="gp-label">{t('game.status.current_action')}</span>
                <strong>{turnActor.name || turnActor.id || "—"}</strong>
              </div>
            </div>
          ) : null}
          {liveEnemies.length ? (
            <ul className="gp-flat-list">
              {liveEnemies.map((c, i) => (
                <li key={c.id || i}>
                  <span>{c.name || c.id || t('game.status.enemy')}</span>
                  <span className="muted-2 mono" style={{fontSize: 11.5}}>HP {c.hp ?? "—"}/{c.max_hp ?? "—"}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {/* 最近裁定 — dice_log 末尾 */}
      {lastRoll ? (
        <div className="gp-section">
          <div className="section-head">
            <h3>{t('game.status.last_ruling')}</h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>{lastRoll.kind || "?"}</span>
          </div>
          <div className="gp-kv">
            <div className="gp-row">
              <span className="gp-label">{lastRoll.actor || "—"}</span>
              <span className="mono">
                {lastRoll.expression || ""}{lastRoll.total != null ? ` = ${lastRoll.total}` : ""}
                {lastRoll.dc != null ? ` vs DC ${lastRoll.dc}` : ""}
                {lastRoll.success === true ? t('game.status.roll_success') : lastRoll.success === false ? t('game.status.roll_failure') : ""}
              </span>
            </div>
            {lastRoll.damage ? (
              <div className="gp-row">
                <span className="gp-label">{t('game.status.damage')}</span>
                <span className="mono">{
                  typeof lastRoll.damage === "object"
                    ? `${lastRoll.damage.amount ?? "—"} ${lastRoll.damage.type || ""}`.trim()
                    : String(lastRoll.damage)
                }</span>
              </div>
            ) : null}
            {lastRoll.reason ? (
              <div className="gp-row">
                <span className="gp-label">{t('game.status.ruling_source')}</span>
                <span style={{fontSize: 12, fontStyle:"italic"}}>{lastRoll.reason}</span>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function NovelStatusProfile({ state }) {
  const { t } = useTranslation();
  // 防御:backend /api/state 在新存档/部分字段缺失时不给出完整结构,
  // 嵌套访问点必须兜底,否则 undefined.x → 白屏(task 5)。
  const p = (state && state.player) || {};
  const w = (state && state.world) || {};
  const timeline = w.timeline || {};
  const inventory = Array.isArray(p.inventory) ? p.inventory : [];
  const knownEvents = Array.isArray(w.known_events) ? w.known_events : [];
  // 能力/技能 = memory.abilities 桶(GM 检测到「掌握/习得」会自动写,玩家也可手动增删)。
  // 群反馈(行者无疆):状态面板参数只读,修来的能力没有结构化的家、只能塞玩家笔记 → 给它一个增删入口。
  const abilities = (state && state.memory && Array.isArray(state.memory.abilities)) ? state.memory.abilities : [];
  const [playerExpanded, setPlayerExpanded] = React.useState(false);

  // 玩家卡字段可编辑:外貌/性格/语气/背景走 /player/profile/set,成功后刷新 state + toast。
  // 失败时 InlineEditField 内部已 toast,这里不重复处理。
  const onProfileSubmit = (field) => async (value) => {
    await window.api.game.playerProfileSet({ field, value });
    try { window.dispatchEvent(new CustomEvent("game-state-refresh")); } catch (_) {}
    window.__apiToast?.(t('game.status.profile_saved'), { kind: "ok", duration: 1500 });
  };

  return (
    <div className="gp-stack">
      <div className="gp-section">
        <div className="section-head">
          <h3>{t('game.status.player')}</h3>
          <button
            className="iconbtn"
            style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4 }}
            onClick={() => setPlayerExpanded(v => !v)}
            data-tip={playerExpanded ? t('game.status.collapse_detail') : t('game.status.expand_detail')}
          >
            {playerExpanded ? t('game.status.collapse_detail') : t('game.status.expand_detail')}
          </button>
        </div>
        <div className="gp-kv">
          <div className="gp-row">
            <span className="gp-label">{t('game.status.name')}</span>
            <strong style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <span>{p.display_name || p.name || "—"}</span>
              {(() => {
                // 老存档兼容：isekai → 按 soul 显示
                const origin = p.player_origin === 'isekai' ? 'soul' : p.player_origin;
                const ORIGIN_BADGES = {
                  soul:   { icon: '◈', label: t('game.status.origin_soul'),   title: t('game.status.origin_soul_title'),   bg: 'rgba(85,130,200,.18)',  color: '#8db4e8', border: 'rgba(85,130,200,.35)' },
                  body:   { icon: '◉', label: t('game.status.origin_body'),   title: t('game.status.origin_body_title'),   bg: 'rgba(220,140,80,.16)', color: '#e8a87c', border: 'rgba(220,140,80,.38)' },
                  dual:   { icon: '◑', label: t('game.status.origin_dual'),   title: t('game.status.origin_dual_title'),   bg: 'rgba(160,130,210,.16)', color: '#b8a0e8', border: 'rgba(160,130,210,.35)' },
                  native: { icon: '◎', label: t('game.status.origin_native'), title: t('game.status.origin_native_title'), bg: 'rgba(150,143,133,.15)', color: '#b8b0a5', border: 'rgba(150,143,133,.3)' },
                };
                const badge = origin && ORIGIN_BADGES[origin];
                if (!badge) return null;
                return (
                  <span title={badge.title}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 3,
                      padding: '1px 7px', borderRadius: 10, fontSize: 11, fontWeight: 600,
                      background: badge.bg, color: badge.color,
                      border: `1px solid ${badge.border}`,
                    }}>{badge.icon} {badge.label}</span>
                );
              })()}
            </strong>
          </div>
          <div className="gp-row"><span className="gp-label">{t('game.status.identity')}</span><span>{p.role || "—"}</span></div>
          <div className="gp-row"><span className="gp-label">{t('game.status.location')}</span><span>{p.current_location || "—"}</span></div>
        </div>
        {playerExpanded && (
          <div className="gp-player-detail" style={{ marginTop: 8 }}>
            <div style={{ marginBottom: 6 }}>
              <div className="gp-label" style={{ marginBottom: 2 }}>{t('game.status.appearance')}</div>
              <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>
                <InlineEditField value={p.appearance || ""} multiline
                  emptyLabel={t('game.status.tap_to_set')}
                  onSubmit={onProfileSubmit('appearance')} />
              </div>
            </div>
            <div style={{ marginBottom: 6 }}>
              <div className="gp-label" style={{ marginBottom: 2 }}>{t('game.status.personality')}</div>
              <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>
                <InlineEditField value={p.personality || ""} multiline
                  emptyLabel={t('game.status.tap_to_set')}
                  onSubmit={onProfileSubmit('personality')} />
              </div>
            </div>
            <div style={{ marginBottom: 6 }}>
              <div className="gp-label" style={{ marginBottom: 2 }}>{t('game.status.speech_style')}</div>
              <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>
                <InlineEditField value={p.speech_style || ""} multiline
                  emptyLabel={t('game.status.tap_to_set')}
                  onSubmit={onProfileSubmit('speech_style')} />
              </div>
            </div>
            {p.background && !p.personality && (
              <div style={{ marginBottom: 6 }}>
                <div className="gp-label" style={{ marginBottom: 2 }}>{t('game.status.background')}</div>
                <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>
                  <InlineEditField value={p.background || ""} multiline
                    emptyLabel={t('game.status.tap_to_set')}
                    onSubmit={onProfileSubmit('background')} />
                </div>
              </div>
            )}
            {p.identity_role_desc && (
              <div style={{ marginBottom: 6 }}>
                <div className="gp-label" style={{ marginBottom: 2 }}>{t('game.status.entry_position')}</div>
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>{p.identity_role_desc}</p>
              </div>
            )}
            {p.secrets && (
              <div style={{ marginBottom: 6, padding: "6px 8px", background: "var(--panel-3)", borderRadius: 6, border: "1px dashed var(--line)" }}>
                <div className="gp-label" style={{ marginBottom: 2, color: "var(--accent)" }}>
                  {t('game.status.secrets_label')}
                </div>
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, fontStyle: "italic" }}>{p.secrets}</p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 能力 / 技能 — memory.abilities 桶(GM 检测「掌握/习得」会自动写,玩家也可手动增删)。
          群反馈(行者无疆):状态参数只读、修来的能力没结构化的家 → 给个增删入口,不必只塞玩家笔记。 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>{t('game.status.abilities')}<span className="muted-2 mono" style={{marginLeft: 8, fontSize: 11}}>{abilities.length}</span></h3>
          <button className="iconbtn" data-tip={t('game.status.add_ability_tip')} data-tip-pos="below" aria-label={t('game.status.add_ability_tip')}
            onClick={async () => {
              const txt = await window.__prompt({ title: t('game.status.add_ability_prompt') });
              if (!txt || !txt.trim()) return;
              try {
                await window.api.game.memoryAdd({ bucket: "abilities", text: txt.trim() });
                try { window.dispatchEvent(new CustomEvent('game-state-refresh')); } catch (_) {}
                window.__apiToast?.(t('game.status.ability_added'), { kind: "ok" });
              } catch (e) { window.__apiToast?.(t('game.status.ability_add_failed'), { kind: "danger", detail: e?.message }); }
            }}>
            <Icon name="plus" size={12} />
          </button>
        </div>
        {abilities.length === 0 ? (
          <div className="muted-2" style={{padding: "10px 4px", fontSize: 12.5, lineHeight: 1.7}}>{t('game.status.abilities_empty')}</div>
        ) : (
          <ul className="gp-flat-list">
            {abilities.map((ab, i) => (
              <li key={i} style={{display: "flex", alignItems: "flex-start", gap: 6}}>
                <span style={{flex: 1}}><Icon name="sparkle" size={12} style={{verticalAlign: "-2px", marginRight: 6}} />{typeof ab === "string" ? ab : (ab?.text || ab?.name || JSON.stringify(ab))}</span>
                <button className="iconbtn" data-tip={t('game.status.remove_ability_tip')} data-tip-pos="below" aria-label={t('game.status.remove_ability_tip')}
                  onClick={async () => {
                    if (!await window.__confirm({ message: t('game.status.remove_ability_confirm'), danger: true })) return;
                    try {
                      await window.api.game.memoryRemove({ bucket: "abilities", index: i });
                      try { window.dispatchEvent(new CustomEvent('game-state-refresh')); } catch (_) {}
                      window.__apiToast?.(t('game.status.ability_removed'), { kind: "ok" });
                    } catch (e) { window.__apiToast?.(t('game.status.ability_remove_failed'), { kind: "danger", detail: e?.message }); }
                  }}>
                  <Icon name="close" size={12} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="gp-section">
        <div className="section-head">
          <h3>{t('game.status.world_now')}</h3>
          <span className="pill ok"><span className="dot ok" /> {t('game.status.locked')}</span>
        </div>
        <div className="gp-kv">
          <div className="gp-row"><span className="gp-label">{t('game.status.time')}</span><span>{w.time || "—"}</span></div>
          <div className="gp-row"><span className="gp-label">{t('game.status.weather')}</span><span>{w.weather || "—"}</span></div>
          <div className="gp-row"><span className="gp-label">{t('game.status.event')}</span><span>{timeline.current_label || "—"}{timeline.current_phase ? ` · ${timeline.current_phase}` : ""}</span></div>
        </div>
      </div>

      <div className="gp-section">
        <div className="section-head"><h3>{t('game.status.inventory')}</h3><span className="muted-2 mono" style={{fontSize: 11}}>{t('game.status.items_count', { count: inventory.length })}</span></div>
        <ul className="gp-flat-list">
          {inventory.map((it, i) => (
            <li key={i}>
              <span>{(it && it.name) || t('game.status.unnamed_item')}</span>
              <span className="muted-2" style={{fontSize: 11.5}}>{(it && it.quality) || ""}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="gp-section">
        <div className="section-head"><h3>{t('game.status.known_events')}</h3></div>
        <ol className="gp-events">
          {knownEvents.map((e, i) => (<li key={i}>{e}</li>))}
        </ol>
      </div>
    </div>
  );
}

function PanelStatus({ state }) {
  // 单一 PanelStatus 入口,根据 content_pack.kind / scene.module_id 选 profile。
  // 同组件、不同数据适配器 — 不做两套面板,避免双方 drift。
  const profile = _statusProfileFor(state);
  if (profile === "module") return <ModuleStatusProfile state={state} />;
  // novel & freeform 共用旧版渲染
  return <NovelStatusProfile state={state} />;
}

export { PanelStatus };
