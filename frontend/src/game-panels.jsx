/* Right-side panel for the Game Console.
   Tabs: status / memory / worldbook / characters / timeline / context / debug
   Each tab is data-driven from window.MOCK_STATE. */

import React from 'react';
import { useState, useMemo } from 'react';
import { Icon } from './game-icons.jsx';

const PANEL_TABS = [
  { id: "status", label: "状态", icon: "status" },
  { id: "rules", label: "5E 规则", icon: "debug" },
  { id: "memory", label: "记忆", icon: "memory" },
  { id: "worldbook", label: "世界书", icon: "world" },
  // Codex 评审:tab 改名"人物" — 不再是"完整角色卡库"的镜像,而是三层运行时索引:
  // 当前在场 (active_entities + encounter.combatants) / 关系 (relationships) /
  // 已固定角色卡 (entity.card_id 链接到平台 user_cards)。提升为长期角色卡只能在
  // 平台『角色卡』页操作,游戏内不创建。
  { id: "cards", label: "人物", icon: "cards" },
  { id: "timeline", label: "世界线", icon: "timeline" },
  { id: "context", label: "上下文", icon: "context" },
  // 调试 tab 仅当 localStorage.rpg_devmode === "1" 时启用; 玩家面看不到
  ...(typeof localStorage !== "undefined" && localStorage.getItem("rpg_devmode") === "1"
      ? [{ id: "debug", label: "调试", icon: "debug" }]
      : []),
];

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
    : "正常";
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
          <h3>玩家</h3>
          <span className="pill"><span className="dot ok" /> {pc.class_name || "—"}</span>
        </div>
        <div className="gp-kv">
          <div className="gp-row">
            <span className="gp-label">姓名</span>
            <strong>
              {pc.name || "—"}
              {pc.level ? ` · Lv${pc.level}` : ""}
              {pc.class_name ? ` ${pc.class_name}` : ""}
            </strong>
          </div>
          <div className="gp-row">
            <span className="gp-label">HP</span>
            <span className="mono">{pc.hp ?? "—"}/{pc.max_hp ?? "—"} {pc.max_hp > 0 ? `(${hpPct}%)` : ""}</span>
          </div>
          <div className="gp-row">
            <span className="gp-label">AC</span>
            <span className="mono">{pc.ac ?? "—"}</span>
          </div>
          <div className="gp-row">
            <span className="gp-label">状态</span>
            <span>{conditions}</span>
          </div>
        </div>
      </div>

      {/* 冒险现场 — 当前房间 + 目标 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>冒险现场</h3>
          {encounter.active
            ? <span className="pill" style={{color:"var(--danger)"}}><span className="dot" style={{background:"var(--danger)"}}/> 战斗中</span>
            : <span className="pill ok"><span className="dot ok" /> 探索</span>}
        </div>
        <div className="gp-kv">
          <div className="gp-row">
            <span className="gp-label">位置</span>
            <strong>{room.name || scene.location_id || "—"}</strong>
          </div>
          {(memory.current_objective || manifest.tagline) ? (
            <div className="gp-row">
              <span className="gp-label">目标</span>
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
            <h3>可见线索</h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>{room.visible_clues.length}</span>
          </div>
          <ul className="gp-flat-list">
            {room.visible_clues.map((c, i) => (
              <li key={c.id || i}>
                <span>{(c && c.text) || c.id || "线索"}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* 出口 */}
      {(room.exits && room.exits.length) ? (
        <div className="gp-section">
          <div className="section-head">
            <h3>出口</h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>{room.exits.length}</span>
          </div>
          <ul className="gp-flat-list">
            {room.exits.map((ex, i) => (
              <li key={ex.to || i}>
                <span>{(ex && ex.label) || ex.to || "出口"}</span>
                <span className="muted-2 mono" style={{fontSize: 11.5}}>{ex.to || ""}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* 资源 — 5E 背包 (player_character.inventory) */}
      <div className="gp-section">
        <div className="section-head">
          <h3>资源</h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{inventory.length} 件</span>
        </div>
        {inventory.length === 0 ? (
          <p className="muted-2" style={{fontSize: 12.5, margin: "4px 0 0"}}>背包为空</p>
        ) : (
          <ul className="gp-flat-list">
            {inventory.map((it, i) => (
              <li key={it.id || it.name || i}>
                <span>{(it && (it.name || it.id)) || "未命名物品"}</span>
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
            <h3>战斗</h3>
            <span className="pill" style={{color:"var(--danger)"}}>
              第 {encounter.round || 1} 回合
            </span>
          </div>
          {turnActor ? (
            <div className="gp-kv">
              <div className="gp-row">
                <span className="gp-label">当前行动</span>
                <strong>{turnActor.name || turnActor.id || "—"}</strong>
              </div>
            </div>
          ) : null}
          {liveEnemies.length ? (
            <ul className="gp-flat-list">
              {liveEnemies.map((c, i) => (
                <li key={c.id || i}>
                  <span>{c.name || c.id || "敌人"}</span>
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
            <h3>最近裁定</h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>{lastRoll.kind || "?"}</span>
          </div>
          <div className="gp-kv">
            <div className="gp-row">
              <span className="gp-label">{lastRoll.actor || "—"}</span>
              <span className="mono">
                {lastRoll.expression || ""}{lastRoll.total != null ? ` = ${lastRoll.total}` : ""}
                {lastRoll.dc != null ? ` vs DC ${lastRoll.dc}` : ""}
                {lastRoll.success === true ? " 成功" : lastRoll.success === false ? " 失败" : ""}
              </span>
            </div>
            {lastRoll.damage ? (
              <div className="gp-row">
                <span className="gp-label">伤害</span>
                <span className="mono">{
                  typeof lastRoll.damage === "object"
                    ? `${lastRoll.damage.amount ?? "—"} ${lastRoll.damage.type || ""}`.trim()
                    : String(lastRoll.damage)
                }</span>
              </div>
            ) : null}
            {lastRoll.reason ? (
              <div className="gp-row">
                <span className="gp-label">来源</span>
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
  // 防御:backend /api/state 在新存档/部分字段缺失时不给出完整结构,
  // 嵌套访问点必须兜底,否则 undefined.x → 白屏(task 5)。
  const p = (state && state.player) || {};
  const w = (state && state.world) || {};
  const timeline = w.timeline || {};
  const inventory = Array.isArray(p.inventory) ? p.inventory : [];
  const knownEvents = Array.isArray(w.known_events) ? w.known_events : [];
  const [playerExpanded, setPlayerExpanded] = React.useState(false);

  const hasDetail = !!(p.appearance || p.personality || p.speech_style || p.secrets || p.background || p.identity_role_desc);

  return (
    <div className="gp-stack">
      <div className="gp-section">
        <div className="section-head">
          <h3>玩家</h3>
          {hasDetail && (
            <button
              className="iconbtn"
              style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4 }}
              onClick={() => setPlayerExpanded(v => !v)}
              data-tip={playerExpanded ? "收起详情" : "查看完整设定"}
            >
              {playerExpanded ? "▲ 收起" : "▼ 详情"}
            </button>
          )}
        </div>
        <div className="gp-kv">
          <div className="gp-row"><span className="gp-label">姓名</span><strong>{p.name || "—"}</strong></div>
          <div className="gp-row"><span className="gp-label">身份</span><span>{p.role || "—"}</span></div>
          <div className="gp-row"><span className="gp-label">所在</span><span>{p.current_location || "—"}</span></div>
        </div>
        {playerExpanded && hasDetail && (
          <div className="gp-player-detail" style={{ marginTop: 8 }}>
            {p.appearance && (
              <div style={{ marginBottom: 6 }}>
                <div className="gp-label" style={{ marginBottom: 2 }}>外貌</div>
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>{p.appearance}</p>
              </div>
            )}
            {p.personality && (
              <div style={{ marginBottom: 6 }}>
                <div className="gp-label" style={{ marginBottom: 2 }}>性格 / 设定</div>
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>{p.personality}</p>
              </div>
            )}
            {p.speech_style && (
              <div style={{ marginBottom: 6 }}>
                <div className="gp-label" style={{ marginBottom: 2 }}>语气</div>
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>{p.speech_style}</p>
              </div>
            )}
            {p.background && !p.personality && (
              <div style={{ marginBottom: 6 }}>
                <div className="gp-label" style={{ marginBottom: 2 }}>背景</div>
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>{p.background}</p>
              </div>
            )}
            {p.identity_role_desc && (
              <div style={{ marginBottom: 6 }}>
                <div className="gp-label" style={{ marginBottom: 2 }}>入场定位</div>
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>{p.identity_role_desc}</p>
              </div>
            )}
            {p.secrets && (
              <div style={{ marginBottom: 6, padding: "6px 8px", background: "var(--panel-3)", borderRadius: 6, border: "1px dashed var(--line)" }}>
                <div className="gp-label" style={{ marginBottom: 2, color: "var(--accent)" }}>
                  🔒 玩家隐藏知识 — 仅你可见
                </div>
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, fontStyle: "italic" }}>{p.secrets}</p>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="gp-section">
        <div className="section-head">
          <h3>当下世界</h3>
          <span className="pill ok"><span className="dot ok" /> 锁定</span>
        </div>
        <div className="gp-kv">
          <div className="gp-row"><span className="gp-label">时刻</span><span>{w.time || "—"}</span></div>
          <div className="gp-row"><span className="gp-label">天气</span><span>{w.weather || "—"}</span></div>
          <div className="gp-row"><span className="gp-label">事件</span><span>{timeline.current_label || "—"}{timeline.current_phase ? ` · ${timeline.current_phase}` : ""}</span></div>
        </div>
      </div>

      <div className="gp-section">
        <div className="section-head"><h3>身上之物</h3><span className="muted-2 mono" style={{fontSize: 11}}>{inventory.length} 件</span></div>
        <ul className="gp-flat-list">
          {inventory.map((it, i) => (
            <li key={i}>
              <span>{(it && it.name) || "未命名物品"}</span>
              <span className="muted-2" style={{fontSize: 11.5}}>{(it && it.quality) || ""}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="gp-section">
        <div className="section-head"><h3>本轮已知事件</h3></div>
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

function PanelMemory({ state, density }) {
  const m = state.memory;
  return (
    <div className="gp-stack">
      <div className="gp-section">
        <div className="section-head"><h3>当前目标</h3><span className="pill">主线</span></div>
        <p className="serif gp-quest">{m.main_quest}</p>
        <p className="muted" style={{fontSize: 13, marginTop: 6}}>{m.current_objective}</p>
      </div>

      <div className="gp-section">
        <div className="section-head">
          <h3>固定记忆<span className="muted-2" style={{marginLeft: 8, fontSize: 11, textTransform: "none"}}>本轮上下文必带</span></h3>
          <button className="iconbtn" data-tip="添加固定记忆" data-tip-pos="below"
            onClick={async () => {
              const t = prompt("新增固定记忆", "");
              if (!t) return;
              try { await window.api.game.memoryAdd({ kind: "pinned", text: t }); window.__apiToast?.("已添加", { kind: "ok" }); }
              catch (e) { window.__apiToast?.("添加失败", { kind: "danger", detail: e?.message }); }
            }}>
            <Icon name="plus" />
          </button>
        </div>
        <ul className="gp-pin-list">
          {(m.pinned || []).map((t, i) => (
            <li key={i}>
              <span className="gp-pin-mark"><Icon name="pin" size={12} /></span>
              <span className="serif">{t}</span>
              <button className="iconbtn" data-tip="解除"
                onClick={async () => {
                  if (!confirm("解除固定记忆？")) return;
                  try { await window.api.game.memoryRemove({ kind: "pinned", index: i, text: t }); window.__apiToast?.("已解除", { kind: "ok" }); }
                  catch (e) { window.__apiToast?.("操作失败", { kind: "danger", detail: e?.message }); }
                }}>
                <Icon name="close" size={12} />
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="gp-section">
        <div className="section-head"><h3>事实库<span className="muted-2" style={{marginLeft: 8, fontSize: 11, textTransform: "none"}}>由 GM 提取</span></h3></div>
        <ul className="gp-flat-list">
          {(m.facts || []).map((t, i) => (<li key={i}><span>{t}</span></li>))}
        </ul>
      </div>

      <div className="gp-section">
        <div className="section-head"><h3>玩家笔记</h3>
          <button className="iconbtn" data-tip="新增笔记" data-tip-pos="below"
            onClick={async () => {
              const t = prompt("新增玩家笔记", "");
              if (!t) return;
              try { await window.api.game.memoryAdd({ kind: "note", text: t }); window.__apiToast?.("已添加", { kind: "ok" }); }
              catch (e) { window.__apiToast?.("添加失败", { kind: "danger", detail: e?.message }); }
            }}>
            <Icon name="plus" />
          </button>
        </div>
        <ul className="gp-flat-list">
          {(m.notes || []).map((t, i) => (<li key={i}><span>{t}</span></li>))}
        </ul>
      </div>

      <div className="gp-section">
        <div className="section-head">
          <h3>召回<span className="muted-2" style={{marginLeft: 8, fontSize: 11, textTransform: "none"}}>本轮从原文 / RAG / 历史找回的参考资料</span></h3>
          <span className="pill mono">{(state.memory && state.memory.last_context && state.memory.last_context.retrieval_chunks) || 0} 段</span>
        </div>
        <pre className="gp-quote">{m.last_retrieval || "（暂无召回）"}</pre>
      </div>
    </div>
  );
}

function PanelWorldbook({ state }) {
  // task 33：兜底 world / player / worldline.constraints 缺失
  const w = (state && state.world) || {};
  const p = (state && state.player) || {};
  const constraints = Array.isArray(state && state.worldline && state.worldline.constraints)
    ? state.worldline.constraints : [];
  return (
    <div className="gp-stack">
      <div className="gp-section">
        <div className="section-head"><h3>地点 · 时刻</h3></div>
        <div className="gp-kv">
          <div className="gp-row"><span className="gp-label">所在</span><span>{p.current_location || "—"}</span></div>
          <div className="gp-row"><span className="gp-label">时刻</span><span>{w.time || "—"}</span></div>
          <div className="gp-row"><span className="gp-label">天气</span><span>{w.weather || "—"}</span></div>
        </div>
      </div>
      <div className="gp-section">
        <div className="section-head"><h3>世界规则</h3><span className="muted-2" style={{fontSize: 11}}>{constraints.length} 条约束</span></div>
        <ul className="gp-flat-list">
          {/* task 48：原代码 constraints.map 之后还硬加一行『灯塔不可在天黑前点燃』示例，
              在导入剧本里完全不相关。删掉。空 constraints 时显示空态。 */}
          {constraints.length === 0 && (
            <li><span className="muted-2">（本剧本暂未配置世界规则）</span></li>
          )}
          {constraints.map((c, i) => (
            <li key={i}><span><Icon name="lock" size={12} style={{verticalAlign: "-2px", marginRight: 6}} />{typeof c === "string" ? c : (c?.text || c?.label || JSON.stringify(c))}</span></li>
          ))}
        </ul>
      </div>
      <div className="gp-section">
        <div className="section-head"><h3>本轮重要词条</h3></div>
        {/* task 48：原硬编码 8 个 chip（雾港/残页/黑铁怀表/沈知微/韩司直/阿衡/北港/灯塔）
            完全不顾当前剧本/state。改为从 state.world.known_events 派生；空就空态。 */}
        <div className="gp-chips">
          {Array.isArray(w.known_events) && w.known_events.length > 0
            ? w.known_events.map((ev, i) => (
                <span key={i} className="gp-chip">{typeof ev === "string" ? ev : (ev?.label || ev?.text || JSON.stringify(ev))}</span>
              ))
            : <span className="muted-2" style={{fontSize: 12}}>暂无本轮重要词条</span>}
        </div>
      </div>
    </div>
  );
}

// ── 三层人物系统 (Codex 评审落地) ─────────────────────────────────
//
// 设计原则 (用户硬要求):
// - 完整角色卡是 *长期资产*,只在平台『角色卡』页创建 / 提升。
// - 游戏界面的"人物"侧边栏只显示 *运行时索引*,不带任何提升 / 创建按钮。
// - 三层:
//     1) 当前在场 — active_entities (source=room_data/encounter/...) +
//                   encounter.combatants (兜底)。来源决定可信度。
//     2) 关系 — state.relationships 里玩家与角色的明确态度变化。
//     3) 已固定角色卡 — active_entities 里 card_id 链接到 user_cards 的条目。
//
// 之前 bug:右侧 tab 只读 state.relationships → GM 写的关系标签才会出现,
// 灰烬教典狱出现在正文但侧边栏看不到。新设计直接读 active_entities,
// 模组房间数据 / 合法 encounter combatants 都自动同步。
//
// CharacterCard 现在是纯只读卡片:无 onEdit / onPromote。
// 唯一交互:拖拽到 composer / @mention 插入。

function _toneColorOfDisposition(disposition) {
  // disposition: friendly/hostile/neutral/unknown (5E 模组实体)
  // 或旧 tone 字串 (信任/亲近/戒备/敌意/未知) — 都映射到 pill 配色。
  const d = String(disposition || "").toLowerCase();
  if (d === "信任" || d === "friendly" || d === "ally") return "ok";
  if (d === "戒备" || d === "warn") return "warn";
  if (d === "亲近" || d === "info") return "info";
  if (d === "敌意" || d === "hostile" || d === "enemy") return "danger";
  return "";
}

function _entityTypeLabel(kind, source) {
  if (kind === "enemy") return "敌人";
  if (kind === "npc") return "NPC";
  if (kind === "ally") return "盟友";
  if (kind === "unknown" && source === "gm_provisional") return "待确认";
  return "—";
}

function CharacterCard({ name, info, subtitle }) {
  // info: { tone | disposition, note?, role? }
  const dispLabel = info.tone || info.disposition || "—";
  const toneColor = _toneColorOfDisposition(dispLabel);
  const onDragStart = (e) => {
    e.dataTransfer.effectAllowed = "copy";
    e.dataTransfer.setData("text/plain", `@${name}`);
    e.dataTransfer.setData("application/x-rpg-character", JSON.stringify({ name, info }));
    e.currentTarget.classList.add("dragging");
  };
  const onDragEnd = (e) => { e.currentTarget.classList.remove("dragging"); };
  return (
    <div className="gp-card" draggable="true" onDragStart={onDragStart} onDragEnd={onDragEnd}>
      <div className="gp-card-head">
        <div className="gp-card-avatar serif">{(name || "?").slice(0, 1)}</div>
        <div style={{minWidth: 0, flex: 1}}>
          <div className="gp-card-name">{name}</div>
          <div className="gp-card-tone">
            <span className={`pill ${toneColor}`}><span className={`dot ${toneColor}`} />{dispLabel}</span>
            {subtitle ? <span className="muted-2 mono" style={{marginLeft: 6, fontSize: 11}}>{subtitle}</span> : null}
          </div>
        </div>
        {/* 仅保留 @mention 插入交互;移除『编辑』『转为用户角色卡』按钮 —
            创建 / 提升只在平台『角色卡』页操作 (Codex 评审硬要求)。 */}
        <button className="iconbtn" data-tip="插入 @角色 到输入框" data-tip-pos="below"
          onClick={() => {
            if (typeof window.__rpgInsertMention === "function") window.__rpgInsertMention(name);
            else if (navigator.clipboard) {
              navigator.clipboard.writeText("@" + name);
              window.__apiToast?.("@" + name + " 已复制", { kind: "ok", duration: 1500 });
            }
          }}>
          <Icon name="at" size={14} />
        </button>
      </div>
      {(info.note || info.role) ? (
        <p className="gp-card-note">{info.note || info.role}</p>
      ) : null}
    </div>
  );
}

function PanelCharacters({ state }) {
  // ── 数据源 ─────────────────────────────────────────────
  // 1. active_entities: 后端在 enter_room / start_encounter 时同步的运行时索引
  // 2. encounter.combatants: 战斗中 enemy/ally combatants (active_entities 里
  //    应该已经有,但兜底:即便 active_entities 还没同步,也能从 combatants 临时构造)
  // 3. relationships: 玩家与角色的明确态度变化(可能是 string 也可能是 dict)
  const activeRaw = Array.isArray(state && state.active_entities) ? state.active_entities : [];
  const encounter = (state && state.encounter) || {};
  const combatants = Array.isArray(encounter.combatants) ? encounter.combatants : [];
  const relationships = (state && state.relationships) || {};

  // 当前在场:active_entities + (战斗中的 combatants 兜底);按 id 去重
  const byId = new Map();
  for (const e of activeRaw) {
    if (e && e.id && e.status !== "defeated") byId.set(String(e.id), e);
  }
  if (encounter.active) {
    for (const c of combatants) {
      if (!c || c.defeated) continue;
      const side = String(c.side || "").toLowerCase();
      if (side === "party") continue;  // 玩家自己不进
      const cid = String(c.id || c.instance_id || "");
      if (!cid || byId.has(cid)) continue;
      byId.set(cid, {
        id: cid, name: c.name || cid,
        kind: side === "enemy" ? "enemy" : side === "ally" ? "ally" : "unknown",
        disposition: side === "enemy" ? "hostile" : side === "ally" ? "friendly" : "unknown",
        source: "encounter",
        stat_block_id: c.stat_block_id || "",
        hp: c.hp, max_hp: c.max_hp,
      });
    }
  }
  const inScene = Array.from(byId.values());

  // 关系:统一规范化
  const normalize = (info) => {
    if (typeof info === "string") return { tone: info, note: "" };
    if (info && typeof info === "object") return { tone: info.tone || "中立", note: info.note || info.description || "" };
    return { tone: "中立", note: "" };
  };
  const relEntries = Object.entries(relationships).map(([name, info]) => ({ name, info: normalize(info) }));

  // 已固定角色卡:active_entities 里 card_id 不空的
  const pinned = activeRaw.filter(e => e && e.card_id);

  return (
    <div className="gp-stack">
      {/* 当前在场 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>当前在场<span className="muted-2" style={{marginLeft: 8, fontSize: 11, textTransform: "none"}}>本房间 / 战斗</span></h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{inScene.length}</span>
        </div>
        {inScene.length === 0 ? (
          <div className="muted-2" style={{padding: "12px 4px", fontSize: 12.5, lineHeight: 1.7}}>
            本房间暂无在场人物。模组房间的 NPC / 敌人或合法 RulesEngine 触发的 encounter 出现时会自动显示在这里。
          </div>
        ) : (
          <div className="gp-cards">
            {inScene.map((e) => {
              const subtitle = _entityTypeLabel(e.kind, e.source) +
                (e.hp != null && e.max_hp != null ? ` · HP ${e.hp}/${e.max_hp}` : "");
              return (
                <CharacterCard key={e.id}
                  name={e.name || e.id}
                  info={{ disposition: e.disposition, note: e.role || "", role: e.role }}
                  subtitle={subtitle}
                />
              );
            })}
          </div>
        )}
      </div>

      {/* 关系 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>关系<span className="muted-2" style={{marginLeft: 8, fontSize: 11, textTransform: "none"}}>玩家与角色的明确态度</span></h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{relEntries.length}</span>
        </div>
        {relEntries.length === 0 ? (
          <div className="muted-2" style={{padding: "12px 4px", fontSize: 12.5, lineHeight: 1.7}}>
            暂无明确关系。GM 输出含『关系：X -&gt; Y』结构化标签时会自动写入这里。
          </div>
        ) : (
          <div className="gp-cards">
            {relEntries.map(({ name, info }) => (
              <CharacterCard key={name} name={name} info={info} />
            ))}
          </div>
        )}
      </div>

      {/* 已固定角色卡 — 只在有时显示,避免空区污染 */}
      {pinned.length > 0 ? (
        <div className="gp-section">
          <div className="section-head">
            <h3>已固定角色卡<span className="muted-2" style={{marginLeft: 8, fontSize: 11, textTransform: "none"}}>链接到平台 user_cards</span></h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>{pinned.length}</span>
          </div>
          <div className="gp-cards">
            {pinned.map((e) => (
              <CharacterCard key={e.id}
                name={e.name || e.id}
                info={{ disposition: e.disposition, note: e.role || "", role: e.role }}
                subtitle={`已固定 · card:${e.card_id}`}
              />
            ))}
          </div>
        </div>
      ) : null}

      {/* 创建 / 提升入口提示 — 引导用户去平台,不在此创建 */}
      <div className="gp-section" style={{background: "transparent", borderTop: "1px dashed var(--line)", marginTop: 4}}>
        <p className="muted-2" style={{fontSize: 12, lineHeight: 1.7, margin: "8px 4px 0"}}>
          想把某个 NPC 提升为长期『用户角色卡』? 到<strong>『平台 → 角色卡』</strong>页操作。
          游戏界面只显示运行时实体,不在此创建 / 导出角色卡。
        </p>
      </div>
    </div>
  );
}

// CharacterEditModal 已删除 — 创建 / 编辑 / 提升用户角色卡的 UI 只能在
// 平台『角色卡』页 (platform-app.jsx → promoteNpcToUserCard)。
// 游戏内人物侧边栏只展示运行时实体,不创建任何持久化卡片。

// task 136h: 世界线收束·锚点 子组件 — 嵌入 PanelTimeline 底部
// 从 /api/saves/:id/anchors 拉取, 跟 timeline 数据互相独立。
function WorldlineAnchorsSection({ saveId }) {
  const { useEffect, useRef } = React;
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [expandedPhase, setExpandedPhase] = useState({});
  const lastSaveId = useRef(null);

  useEffect(() => {
    if (!saveId) { setData(null); setError(""); return; }
    if (saveId === lastSaveId.current && data !== null) return;
    lastSaveId.current = saveId;
    let cancelled = false;
    setLoading(true);
    setError("");
    const base = (typeof window !== "undefined" && window.__API_BASE) || "";
    fetch(`${base}/api/saves/${saveId}/anchors`, { credentials: "include" })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(json => {
        if (!cancelled) { setData(json); setLoading(false); }
      })
      .catch(e => {
        if (!cancelled) { setError(String(e?.message || e)); setLoading(false); }
      });
    return () => { cancelled = true; };
  }, [saveId]);

  if (!saveId) return null;
  if (error) {
    return (
      <div className="gp-section">
        <div className="section-head"><h3>世界线收束·锚点</h3></div>
        <p style={{fontSize: 12.5, color: "var(--danger)", padding: "4px"}}>加载失败: {error}</p>
      </div>
    );
  }
  if (loading || data === null) {
    return (
      <div className="gp-section">
        <div className="section-head"><h3>世界线收束·锚点</h3></div>
        <p className="muted-2" style={{fontSize: 12.5, padding: "4px"}}>正在加载锚点…</p>
      </div>
    );
  }
  const summary = data.summary || {};
  const byPhase = Array.isArray(data.by_phase) ? data.by_phase : [];
  const recentPending = Array.isArray(data.recent_pending) ? data.recent_pending : [];
  const recentOccurred = Array.isArray(data.recent_occurred) ? data.recent_occurred : [];
  const total = summary.total || 0;

  if (total === 0) {
    return (
      <div className="gp-section">
        <div className="section-head"><h3>世界线收束·锚点</h3></div>
        <p className="muted-2" style={{fontSize: 12.5, padding: "4px"}}>
          该存档尚未生成锚点 (剧本可能还在 seed 中, 或没有 chapter_facts)
        </p>
      </div>
    );
  }

  const driftPct = Math.round((summary.avg_drift || 0) * 100);
  const driftColor = driftPct >= 60 ? "var(--danger)" : driftPct >= 30 ? "var(--warn)" : "var(--ok)";

  return (
    <>
      {/* 总览 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>世界线收束·锚点</h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{total} 个锚点</span>
        </div>
        <div className="gp-kv" style={{marginBottom: 6}}>
          <div className="gp-row">
            <span className="gp-label">整体收束</span>
            <span className="serif">
              <span style={{color: "var(--muted-2)"}}>待发生 </span>
              <strong>{summary.pending || 0}</strong>
              <span style={{color: "var(--muted-2)"}}> · 已发生 </span>
              <strong style={{color: "var(--ok)"}}>{summary.occurred || 0}</strong>
              <span style={{color: "var(--muted-2)"}}> · 变体 </span>
              <strong style={{color: "var(--warn)"}}>{summary.variant || 0}</strong>
              <span style={{color: "var(--muted-2)"}}> · 绕过 </span>
              <strong style={{color: "var(--danger)"}}>{summary.superseded || 0}</strong>
            </span>
          </div>
          <div className="gp-row">
            <span className="gp-label">平均偏离度</span>
            <span className="mono" style={{color: driftColor}}>
              {(summary.avg_drift || 0).toFixed(2)} ({driftPct}%)
            </span>
          </div>
          {summary.fatal_pending > 0 && (
            <div className="gp-row">
              <span className="gp-label">死神来了·必发生</span>
              <span className="mono" style={{color: "var(--danger)", fontWeight: 600}}>
                {summary.fatal_pending} 待触发
              </span>
            </div>
          )}
        </div>
        {/* drift 进度条 */}
        <div style={{height: 4, background: "var(--panel-3)", borderRadius: 2, overflow: "hidden", marginBottom: 4}}>
          <div style={{width: driftPct + "%", height: "100%", background: driftColor, transition: "width 0.3s"}} />
        </div>
        <p className="muted-2" style={{fontSize: 11, margin: "4px 0 0"}}>
          0% = 完全按原著 · 100% = 高度偏离 (GM 会主动收束)
        </p>
      </div>

      {/* 按 phase 分组 */}
      {byPhase.length > 0 && (
        <div className="gp-section">
          <div className="section-head"><h3>按阶段分布</h3></div>
          <div className="gp-track">
            {byPhase.map((p, i) => {
              const pressure = p.convergence_pressure || 0;
              const pressureColor = pressure >= 0.6 ? "var(--danger)" :
                                    pressure >= 0.3 ? "var(--warn)" : "var(--ok)";
              const expanded = !!expandedPhase[p.phase_label];
              return (
                <div key={i} className="gp-anchor">
                  <div className="gp-anchor-dot" style={{background: pressureColor, border: "2px solid var(--line)"}} />
                  <div className="gp-anchor-body">
                    <div
                      className="gp-anchor-label"
                      style={{cursor: "pointer"}}
                      onClick={() => setExpandedPhase(prev => ({...prev, [p.phase_label]: !prev[p.phase_label]}))}
                    >
                      {p.phase_label || "(未分阶段)"}
                      <span className="muted-2" style={{marginLeft: 6, fontSize: 10}}>
                        {p.occurred + p.variant}/{p.total} 已收束
                      </span>
                      {p.fatal_pending > 0 && (
                        <span className="pill" style={{marginLeft: 6, fontSize: 10, background: "var(--danger)", color: "#fff"}}>
                          {p.fatal_pending} 必发生
                        </span>
                      )}
                    </div>
                    <div className="gp-anchor-phase" style={{color: "var(--muted-2)", fontSize: 11}}>
                      drift {p.avg_drift.toFixed(2)} · 收束压力 {Math.round(pressure * 100)}%
                      {expanded ? " · ▲" : " · ▼"}
                    </div>
                  </div>
                  {i < byPhase.length - 1 && <div className="gp-anchor-line" />}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 待发生锚点 (top 12) */}
      {recentPending.length > 0 && (
        <div className="gp-section">
          <div className="section-head">
            <h3>待发生锚点</h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>top {recentPending.length}</span>
          </div>
          <ul className="gp-flat-list">
            {recentPending.map((a, i) => (
              <li key={"p:" + i}>
                <span>
                  <span className="mono" style={{fontSize: 10.5, color: "var(--muted-2)", marginRight: 6}}>
                    ch{a.chapter}
                  </span>
                  {a.is_fatal && (
                    <span className="pill" style={{fontSize: 10, marginRight: 4, background: "var(--danger)", color: "#fff"}}>必发生</span>
                  )}
                  {a.summary || a.anchor_key}
                </span>
                <span className="mono" style={{fontSize: 10.5, color: "var(--muted-2)"}}>
                  imp {a.importance}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 已发生锚点 */}
      {recentOccurred.length > 0 && (
        <div className="gp-section">
          <div className="section-head">
            <h3>近期已收束</h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>recent {recentOccurred.length}</span>
          </div>
          <ul className="gp-flat-list">
            {recentOccurred.map((a, i) => {
              const statusColor = a.status === "occurred" ? "var(--ok)" : "var(--warn)";
              const driftPctOne = Math.round((a.drift_score || 0) * 100);
              return (
                <li key={"o:" + i}>
                  <span>
                    <span className="mono" style={{fontSize: 10.5, color: "var(--muted-2)", marginRight: 6}}>
                      ch{a.chapter}
                    </span>
                    <span style={{color: statusColor, fontWeight: 600, marginRight: 4}}>
                      {a.status === "occurred" ? "[原著]" : "[变体]"}
                    </span>
                    {a.summary || a.anchor_key}
                    {a.how_it_happened && (
                      <div className="muted-2" style={{fontSize: 11, marginTop: 2, paddingLeft: 12}}>
                        → {a.how_it_happened}
                      </div>
                    )}
                  </span>
                  <span className="mono" style={{fontSize: 10.5, color: statusColor}}>
                    drift {driftPctOne}%
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </>
  );
}


// task 107G: 双时间线 panel — 剧本期望线 + 实际足迹线
// 从 /api/saves/:id/timeline 按需拉取,saveId 由 state._raw.save_id 提供。
function PanelTimeline({ state }) {
  const { useEffect, useRef } = React;
  const saveId = state && state._raw && state._raw.save_id;
  const [data, setData] = useState(null);    // null = 未加载, {} = 加载中/完毕
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [expandedPhase, setExpandedPhase] = useState({});  // {phase_index: bool}
  const lastSaveId = useRef(null);

  useEffect(() => {
    if (!saveId) { setData(null); setError(""); return; }
    if (saveId === lastSaveId.current && data !== null) return;  // 已加载且 save 没变
    lastSaveId.current = saveId;
    let cancelled = false;
    setLoading(true);
    setError("");
    // task 107G fix: 前端 5173, backend 7860 — 必须绝对 URL + credentials
    const base = (typeof window !== "undefined" && window.__API_BASE) || "";
    fetch(`${base}/api/saves/${saveId}/timeline`, { credentials: "include" })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(json => {
        if (!cancelled) { setData(json); setLoading(false); }
      })
      .catch(e => {
        if (!cancelled) { setError(String(e?.message || e)); setLoading(false); }
      });
    return () => { cancelled = true; };
  }, [saveId]);

  if (!saveId) {
    return (
      <div className="gp-stack">
        <div className="gp-section">
          <p className="muted-2" style={{fontSize: 12.5, padding: "12px 4px"}}>
            暂无存档上下文，请先进入游戏后查看时间线。
          </p>
        </div>
      </div>
    );
  }

  // task 107G fix: error 检查提前于 loading, 否则 fetch 失败 + data===null 永远显示加载中
  if (error) {
    return (
      <div className="gp-stack">
        <div className="gp-section">
          <p style={{fontSize: 12.5, color: "var(--danger)", padding: "12px 4px"}}>
            加载时间线失败: {error}
          </p>
          <p className="muted-2" style={{fontSize: 11.5, padding: "0 4px"}}>
            (后端是否运行 / 当前账号是否拥有此存档?)
          </p>
        </div>
      </div>
    );
  }

  if (loading || data === null) {
    return (
      <div className="gp-stack">
        <div className="gp-section">
          <p className="muted-2" style={{fontSize: 12.5, padding: "12px 4px"}}>正在加载时间线…</p>
        </div>
      </div>
    );
  }

  const scriptAnchors = Array.isArray(data.script_anchors) ? data.script_anchors : [];
  const savePhases   = Array.isArray(data.save_phases)    ? data.save_phases    : [];
  const currentPhaseIndex = data.current_phase_index ?? 0;

  // 判断剧本锚点相对当前 phase 的状态
  // 用 chapter_min 与实际足迹最大 turn 做简单映射:
  // 若 save_phases 有记录,取最后一个 phase 的 phase_index 作为"已过" baseline
  const latestPhase = savePhases.length ? savePhases[savePhases.length - 1].phase_index : -1;

  return (
    <div className="gp-stack">
      {/* 剧本期望线 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>剧本期望线</h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{scriptAnchors.length} 锚点</span>
        </div>
        {scriptAnchors.length === 0 ? (
          <p className="muted-2" style={{fontSize: 12.5, margin: "4px 0 0"}}>此剧本没有预设时间线</p>
        ) : (
          <div className="gp-track">
            {scriptAnchors.map((a, i) => {
              // 状态判断：chapter_min < (currentPhaseIndex 对应的 chapter) = 已过
              // 简化方案：按序列位置和 currentPhaseIndex 比较
              const isDone    = i < currentPhaseIndex;
              const isCurrent = i === currentPhaseIndex;
              const isPending = i > currentPhaseIndex;
              return (
                <div
                  key={i}
                  className={`gp-anchor ${isCurrent ? "current" : ""} ${isDone ? "done" : ""} ${isPending ? "pending" : ""}`}
                >
                  <div className="gp-anchor-dot" style={{
                    background: isDone ? "var(--ok)" : isCurrent ? "var(--accent)" : "var(--panel-3)",
                    border: isCurrent ? "2px solid var(--accent)" : "2px solid var(--line)",
                  }} />
                  <div className="gp-anchor-body">
                    <div className="gp-anchor-label" style={{
                      color: isPending ? "var(--muted-2)" : undefined,
                      fontWeight: isCurrent ? 600 : undefined,
                    }}>
                      {a.phase_label || `第${a.chapter_min}章`}
                      {isCurrent && <span className="pill" style={{marginLeft: 6, fontSize: 10, background: "var(--accent)", color: "#fff"}}>当前</span>}
                      {isDone && <span className="muted-2" style={{marginLeft: 6, fontSize: 10}}>已度过</span>}
                      {isPending && <span className="muted-2" style={{marginLeft: 6, fontSize: 10}}>待解锁</span>}
                    </div>
                    <div className="gp-anchor-phase" style={{color: "var(--muted-2)"}}>
                      {a.story_time_label ? `${a.story_time_label}` : ""}
                      {a.chapter_min != null
                        ? ` · 第 ${a.chapter_min}${a.chapter_max != null && a.chapter_max !== a.chapter_min ? `–${a.chapter_max}` : ""} 章`
                        : ""}
                    </div>
                  </div>
                  {i < scriptAnchors.length - 1 && <div className="gp-anchor-line" />}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 实际足迹线 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>实际足迹线</h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{savePhases.length} 阶段</span>
        </div>
        {savePhases.length === 0 ? (
          <p className="muted-2" style={{fontSize: 12.5, margin: "4px 0 0"}}>
            暂无足迹，开始游玩积累历史
          </p>
        ) : (
          <div className="gp-track">
            {savePhases.map((ph, i) => {
              const isOpen    = ph.status === "open";
              const isCurrent = ph.phase_index === currentPhaseIndex && isOpen;
              const expanded  = !!expandedPhase[ph.phase_index];
              const keyEvents = Array.isArray(ph.key_events) ? ph.key_events : [];
              return (
                <div key={ph.phase_index}
                  className={`gp-anchor ${isCurrent ? "current" : ""}`}
                  style={{cursor: keyEvents.length ? "pointer" : undefined}}
                  onClick={() => keyEvents.length && setExpandedPhase(s => ({...s, [ph.phase_index]: !s[ph.phase_index]}))}
                >
                  <div className="gp-anchor-dot" style={{
                    background: isCurrent ? "var(--accent)" : "var(--ok)",
                    border: isCurrent ? "2px solid var(--accent)" : "2px solid var(--ok)",
                  }} />
                  <div className="gp-anchor-body">
                    <div className="gp-anchor-label" style={{fontWeight: isCurrent ? 600 : undefined}}>
                      <span className="muted-2 mono" style={{fontSize: 11, marginRight: 6}}>
                        Phase {ph.phase_index}
                      </span>
                      {ph.phase_label || `(turn ${ph.turn_start}–${isOpen ? "…" : ph.turn_end})`}
                      {isCurrent && <span className="pill" style={{marginLeft: 6, fontSize: 10, background: "var(--accent)", color: "#fff"}}>进行中</span>}
                    </div>
                    <div className="gp-anchor-phase" style={{color: "var(--muted-2)"}}>
                      {`turn ${ph.turn_start}–${isOpen ? "…" : ph.turn_end}`}
                      {ph.story_time_label ? ` · ${ph.story_time_label}` : ""}
                    </div>
                    {ph.summary ? (
                      <p className="gp-bio" style={{marginTop: 4, fontSize: 12}}>{ph.summary}</p>
                    ) : null}
                    {expanded && keyEvents.length > 0 && (
                      <ul className="gp-flat-list" style={{marginTop: 4}}>
                        {keyEvents.map((ev, ei) => {
                          const evText = typeof ev === "string" ? ev
                            : (ev && (ev.summary || ev.text || ev.label || JSON.stringify(ev)));
                          const evTurn = ev && ev.turn != null ? `turn ${ev.turn}` : "";
                          return (
                            <li key={ei}>
                              <span>{evText}</span>
                              {evTurn && <span className="muted-2 mono" style={{fontSize: 10.5}}>{evTurn}</span>}
                            </li>
                          );
                        })}
                      </ul>
                    )}
                    {keyEvents.length > 0 && (
                      <div className="muted-2" style={{fontSize: 10.5, marginTop: 2, cursor: "pointer"}}>
                        {expanded ? "▲ 收起" : `▼ ${keyEvents.length} 个关键事件`}
                      </div>
                    )}
                  </div>
                  {i < savePhases.length - 1 && <div className="gp-anchor-line" />}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* task 136h: 世界线收束·锚点 */}
      <WorldlineAnchorsSection saveId={saveId} />
    </div>
  );
}

// task 26: 把可能是 string / number / null / { value | text | label | ... } 的字段
// 安全格式化成 React 能渲染的字符串。优先抓常见语义字段，最后兜底 JSON.stringify。
function _renderVarValue(v) {
  if (v == null) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) {
    // 数组里也可能套对象：递归取摘要
    return v.map(_renderVarValue).join("，") || "—";
  }
  if (typeof v === "object") {
    // 富对象常见 schema：{value, locked, source, turn, updated_at}（变量）
    // 或 {text, time, turn, validated, variables}（推演结果）
    if ("value" in v && v.value != null) return _renderVarValue(v.value);
    if ("text" in v && v.text != null) return _renderVarValue(v.text);
    if ("label" in v && v.label != null) return _renderVarValue(v.label);
    if ("name" in v && v.name != null) return _renderVarValue(v.name);
    try { return JSON.stringify(v); } catch (_) { return "[object]"; }
  }
  return String(v);
}

// task 86：DemandLedger（curator 子代理 14 字段输出）右侧面板可视化。
// 数据源：state.last_context_agent.curator_plan（task 79 写入）。
// 真实部署里这个字段嵌在 state.memory.last_context_agent 下；spec 给的 prop
// 顺序是 state.last_context_agent → state.last_context.debug → 兜底 {}。
// 这里组件本身只接收 plan + audit_log，由调用方负责挑路径。
function DemandLedgerPanel({ curator_plan, audit_log }) {
  const plan = (curator_plan && typeof curator_plan === "object") ? curator_plan : {};
  const log = Array.isArray(audit_log) ? audit_log : [];

  const intent = (typeof plan.intent === "string" && plan.intent.trim()) ? plan.intent.trim() : "";
  const activeGoal = (typeof plan.active_goal === "string" && plan.active_goal.trim()) ? plan.active_goal.trim() : "";
  const hardConstraints = Array.isArray(plan.hard_constraints) ? plan.hard_constraints : [];
  const softPreferences = Array.isArray(plan.soft_preferences) ? plan.soft_preferences : [];
  const candidateActions = Array.isArray(plan.candidate_actions) ? plan.candidate_actions : [];
  const acceptance = Array.isArray(plan.acceptance) ? plan.acceptance : [];
  const riskFlags = Array.isArray(plan.risk_flags) ? plan.risk_flags : [];
  const clarifying = (typeof plan.clarifying_question === "string" && plan.clarifying_question.trim()) ? plan.clarifying_question.trim() : "";

  const confidenceRaw = plan.confidence;
  const hasConfidence = typeof confidenceRaw === "number" && isFinite(confidenceRaw);
  const confidence = hasConfidence ? Math.max(0, Math.min(1, confidenceRaw)) : null;
  const confidenceColor = confidence == null
    ? "var(--muted-2)"
    : confidence >= 0.7 ? "var(--ok)"
    : confidence >= 0.5 ? "var(--warn)"
    : "var(--danger)";

  // 判断 plan 是否完全为空：任一关键数组/字符串字段非空就算"有计划"
  const hasAny =
    intent || activeGoal || clarifying ||
    hardConstraints.length || softPreferences.length || candidateActions.length ||
    acceptance.length || riskFlags.length || hasConfidence;

  // task 81：acceptance 验证未通过会写 audit_log kind=acceptance_unmet，
  // hint 形如 "未通过验收：{item[:160]}"。逐条 acceptance 用 substring 匹配。
  // 只看最近 30 条 audit_log 避免上一轮的残留误判当前轮（用户切换面板时尤其要紧）。
  const recentAudit = log.slice(-30);
  const unmetHints = recentAudit
    .filter(a => a && a.kind === "acceptance_unmet" && typeof a.hint === "string")
    .map(a => a.hint);
  const isUnmet = (clause) => {
    if (typeof clause !== "string" || !clause.trim()) return false;
    // hint 截到 160 字符；short clause 完整命中，长 clause 用前 80 字符做子串保险
    const probe = clause.trim().slice(0, 80);
    return unmetHints.some(h => h.indexOf(probe) >= 0 || (probe.length >= 12 && h.indexOf(probe.slice(0, 40)) >= 0));
  };

  // 字段全空的兜底信息
  if (!hasAny) {
    return (
      <div className="gp-section">
        <div className="section-head"><h3>本轮 Curator 决策</h3></div>
        <div className="empty-line">本轮无 curator 决策（未启用子代理或回退到规则）</div>
      </div>
    );
  }

  const renderItem = (v, i, prefix) => {
    const text = typeof v === "string" ? v : (v && (v.text || v.label || v.name)) || JSON.stringify(v);
    return <li key={prefix + ":" + i}><span>{text}</span></li>;
  };

  return (
    <div className="gp-section">
      <div className="section-head">
        <h3>本轮 Curator 决策</h3>
        <span className="muted-2 mono" style={{fontSize: 11}}>demand ledger</span>
      </div>

      {/* 意图 + active_goal */}
      {(intent || activeGoal) && (
        <div className="gp-kv" style={{marginBottom: 4}}>
          <div className="gp-row">
            <span className="gp-label">意图</span>
            <span className="serif">{intent || activeGoal}</span>
          </div>
          {activeGoal && activeGoal !== intent && (
            <div className="gp-row">
              <span className="gp-label">目标</span>
              <span style={{color: "var(--text-quiet)"}}>{activeGoal}</span>
            </div>
          )}
        </div>
      )}

      {/* 置信度进度条 */}
      {hasConfidence && (
        <div className="gp-row" style={{display: "grid", gridTemplateColumns: "64px 1fr auto", gap: 8, alignItems: "center"}}>
          <span className="gp-label">置信度</span>
          <div style={{height: 4, borderRadius: 999, background: "var(--line-soft)", overflow: "hidden"}}>
            <div style={{width: Math.round(confidence * 100) + "%", height: "100%", background: confidenceColor}} />
          </div>
          <span className="mono" style={{fontSize: 11, color: "var(--muted)"}}>{Math.round(confidence * 100)}%</span>
        </div>
      )}

      {/* 澄清问题（confidence 低时常出现，单独提示） */}
      {clarifying && (
        <div className="gp-quote" style={{borderLeftColor: "var(--warn)", fontSize: 12.5}}>
          <strong className="warn" style={{marginRight: 6}}>需澄清：</strong>{clarifying}
        </div>
      )}

      {/* 硬约束 */}
      {hardConstraints.length > 0 && (
        <div style={{display: "grid", gap: 6}}>
          <span className="gp-label">硬约束</span>
          <ul className="gp-flat-list">
            {hardConstraints.map((v, i) => {
              const text = typeof v === "string" ? v : (v && (v.text || v.label)) || JSON.stringify(v);
              return (
                <li key={"hc:" + i}>
                  <span>
                    <Icon name="lock" size={12} style={{verticalAlign: "-2px", marginRight: 6, color: "var(--accent)"}} />
                    {text}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* 软偏好 */}
      {softPreferences.length > 0 && (
        <div style={{display: "grid", gap: 6}}>
          <span className="gp-label">软偏好</span>
          <ul className="gp-flat-list">
            {softPreferences.map((v, i) => {
              const text = typeof v === "string" ? v : (v && (v.text || v.label)) || JSON.stringify(v);
              return (
                <li key={"sp:" + i} style={{borderStyle: "dashed"}}>
                  <span className="muted">{text}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* 候选动作（编号列表，复用 gp-events 序号样式） */}
      {candidateActions.length > 0 && (
        <div style={{display: "grid", gap: 6}}>
          <span className="gp-label">候选动作</span>
          <ol className="gp-events">
            {candidateActions.map((v, i) => {
              const text = typeof v === "string" ? v : (v && (v.text || v.label || v.name)) || JSON.stringify(v);
              return <li key={"ca:" + i}>{text}</li>;
            })}
          </ol>
        </div>
      )}

      {/* 验收（含通过/未通过状态） */}
      {acceptance.length > 0 && (
        <div style={{display: "grid", gap: 6}}>
          <span className="gp-label">验收</span>
          <ul className="gp-flat-list">
            {acceptance.map((v, i) => {
              const text = typeof v === "string" ? v : (v && (v.text || v.label)) || JSON.stringify(v);
              const unmet = isUnmet(text);
              const mark = unmet
                ? <span className="danger mono" style={{marginRight: 6, fontWeight: 600}}>[未]</span>
                : <span className="ok mono" style={{marginRight: 6, fontWeight: 600}}>[过]</span>;
              return (
                <li key={"ac:" + i}>
                  <span>{mark}{text}</span>
                  <span className={`mono ${unmet ? "danger" : "ok"}`} style={{fontSize: 10.5}}>
                    {unmet ? "未通过" : "通过"}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* 风险标记（黄色 chip） */}
      {riskFlags.length > 0 && (
        <div style={{display: "grid", gap: 6}}>
          <span className="gp-label">风险标记</span>
          <div className="gp-chips">
            {riskFlags.map((v, i) => {
              const text = typeof v === "string" ? v : (v && (v.text || v.label)) || JSON.stringify(v);
              return (
                <span
                  key={"rf:" + i}
                  className="gp-chip warn"
                  style={{
                    color: "var(--warn)",
                    borderColor: "rgba(212, 179, 102, 0.32)",
                    background: "var(--warn-soft)",
                  }}
                >
                  <Icon name="warn" size={11} style={{marginRight: 4}} />
                  {text}
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function PanelContext({ state }) {
  // task 33：真实 /api/state 下 memory.last_context 可能是 undefined / {} / 缺字段，
  // 原代码直读 .tokens_used / .retrieval_chunks / .chapter_refs.map 会触发
  // "Cannot read properties of undefined (reading 'map')"，右侧"上下文"tab 整 panel 崩。
  // 全部兜底到安全默认值。
  const memory = (state && state.memory) || {};
  const lastCtx = memory.last_context || {};
  const tokensUsed = lastCtx.tokens_used || 0;
  const retrievalChunks = lastCtx.retrieval_chunks || 0;
  const chapterRefs = Array.isArray(lastCtx.chapter_refs) ? lastCtx.chapter_refs : [];
  // task 86：curator_plan 真实写入路径是 state.memory.last_context_agent.curator_plan；
  // spec prop 顺序是 state.last_context_agent → state.last_context.debug → memory.last_context_agent → {}
  const curatorPlan =
    (state && state.last_context_agent && state.last_context_agent.curator_plan) ||
    (state && state.last_context && state.last_context.debug && state.last_context.debug.curator_plan) ||
    (memory && memory.last_context_agent && memory.last_context_agent.curator_plan) ||
    {};
  const auditLog = (state && state.permissions && Array.isArray(state.permissions.audit_log))
    ? state.permissions.audit_log : [];
  return (
    <div className="gp-stack">
      <div className="gp-section">
        <div className="section-head">
          <h3>本轮上下文<span className="muted-2" style={{marginLeft: 8, fontSize: 11, textTransform: "none"}}>{tokensUsed} tokens</span></h3>
          <span className="pill mono">{retrievalChunks} chunks</span>
        </div>
        <ul className="gp-flat-list">
          {chapterRefs.map((c, i) => (
            <li key={i}><span><Icon name="quote" size={12} style={{verticalAlign: "-2px", marginRight: 6}} />{typeof c === "string" ? c : (c?.title || c?.label || JSON.stringify(c))}</span><span className="muted-2 mono" style={{fontSize: 11}}>0.{84 - i * 7}</span></li>
          ))}
          {chapterRefs.length === 0 && (
            <li><span className="muted-2">（本轮没有检索到章节引用）</span></li>
          )}
          {/* task 48：原硬编码『固定记忆 · 2 段』和『历史摘要 · 最近 8 回合』改为读 state 真值 */}
          <li>
            <span><Icon name="memory" size={12} style={{verticalAlign: "-2px", marginRight: 6}} />固定记忆 · {Array.isArray(memory.pinned) ? memory.pinned.length : 0} 段</span>
            <span className="muted-2 mono" style={{fontSize: 11}}>—</span>
          </li>
          <li>
            <span><Icon name="user" size={12} style={{verticalAlign: "-2px", marginRight: 6}} />历史摘要 · {((lastCtx && lastCtx.history_turns) || (state && Array.isArray(state.history) ? Math.floor(state.history.length / 2) : 0))} 回合</span>
            <span className="muted-2 mono" style={{fontSize: 11}}>—</span>
          </li>
        </ul>
      </div>
      {/* task 86：本轮 Curator 决策（DemandLedger 可视化） */}
      <DemandLedgerPanel curator_plan={curatorPlan} audit_log={auditLog} />
      <div className="gp-section">
        <div className="section-head"><h3>提示词预览 / 最近召回</h3></div>
        {/* task 48：原 pre 硬编码『顾承砚 · 漂流的史官 / 北港码头 / 申时三刻 · 霜降前两日 / 雾港事件第二日清晨』
            完全和当前剧本无关。改为读 state.memory.last_retrieval（context_agent + retrieve_context 后写入）。 */}
        <pre className="gp-quote mono" style={{maxHeight: 280, overflow: "auto", whiteSpace: "pre-wrap"}}>
{(memory.last_retrieval && String(memory.last_retrieval).trim()) || "（暂无召回上下文。发送一轮消息后，这里会显示注入给 GM 的上下文片段。）"}
        </pre>
      </div>
    </div>
  );
}

function PanelDebug({ state }) {
  // task 48：原代码全是硬编码（韩司直.tone / 童氏与南陵同源 / model gpt-4o-mini / latency 7.4s）。
  // 改为读 state.memory.last_context_agent.steps 当 SSE 流；state.permissions.audit_log 当权限日志。
  const memory = (state && state.memory) || {};
  const lastAgent = memory.last_context_agent || {};
  const steps = Array.isArray(lastAgent.steps) ? lastAgent.steps : [];
  const permissions = (state && state.permissions) || {};
  const audit = Array.isArray(permissions.audit_log) ? permissions.audit_log : [];
  return (
    <div className="gp-stack">
      <div className="gp-section">
        <div className="section-head"><h3>子代理步骤</h3><span className="pill mono">最近一轮</span></div>
        <ul className="gp-sse">
          {steps.length === 0 && <li><span className="muted-2">（暂无步骤；发一轮消息后会出现 context_agent 各阶段）</span></li>}
          {steps.map((s, i) => (
            <li key={i}>
              <span className={`mono ${s.status === "done" ? "ok" : s.status === "stopped" ? "danger" : "accent"}`}>{s.phase || "step"}</span>
              <span className="mono muted-2">{(s.message || "").slice(0, 80)} {typeof s.elapsed_ms === "number" ? `· ${(s.elapsed_ms/1000).toFixed(1)}s` : ""}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="gp-section">
        <div className="section-head"><h3>本轮请求</h3></div>
        <div className="gp-kv">
          {(() => {
            const ctx = memory.last_context || {};
            const tokens = `in ${ctx.tokens_used || 0}${ctx.tokens_out ? ` · out ${ctx.tokens_out}` : ""}`;
            return (
              <>
                <div className="gp-row"><span className="gp-label">召回 chunks</span><span className="mono">{ctx.retrieval_chunks || 0}</span></div>
                <div className="gp-row"><span className="gp-label">tokens</span><span className="mono">{tokens}</span></div>
                <div className="gp-row"><span className="gp-label">turn</span><span className="mono">{(state && state.turn) ?? 0}</span></div>
              </>
            );
          })()}
        </div>
      </div>
      <div className="gp-section">
        <div className="section-head"><h3>权限日志</h3><span className="muted-2 mono" style={{fontSize: 11}}>{audit.length}</span></div>
        <ul className="gp-flat-list">
          {audit.length === 0 && <li><span className="muted-2">（暂无写入审计记录）</span></li>}
          {audit.slice(-8).reverse().map((a, i) => (
            <li key={i}>
              <span className={`mono ${a.source === "user:/set" ? "accent" : ""}`}>{a.source || "auto"}</span>
              <span className="muted">{a.path}{a.value != null ? `: ${String(a.value).slice(0, 60)}` : ""}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// ── 5E-compatible 规则面板 ─────────────────────────────────────
// 内部 ruleset id "dnd5e"，对外文案统一使用 "5E compatible / 五版规则兼容"。
// 不引入任何官方 Dungeons & Dragons 商标或非 SRD IP。
function PanelRules({ state }) {
  const ruleset = (state && state.ruleset) || {};
  const pc = (state && state.player_character) || {};
  const scene = (state && state.scene) || {};
  const encounter = (state && state.encounter) || {};
  const diceLog = Array.isArray(state && state.dice_log) ? state.dice_log : [];
  const contentPack = (state && state.content_pack) || {};
  const [busy, setBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  async function runRules(fnName, ...args) {
    if (!window.api?.rules) { setErrorMsg("window.api.rules 未注册"); return null; }
    setBusy(true);
    setErrorMsg("");
    try {
      const data = await window.api.rules[fnName](...args);
      if (!data || !data.ok) throw new Error(data?.error || data?.detail || `请求失败: ${fnName}`);
      window.dispatchEvent(new CustomEvent("game-state-refresh"));
      return data;
    } catch (e) {
      setErrorMsg(String(e?.message || e));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function move(toId) { await runRules("move", toId); }
  async function doAction(body) { await runRules("action", body); }
  async function startEncounter(encId) { await runRules("encounterStart", encId); }
  async function nextTurn() { await runRules("encounterNext"); }
  async function enemyAttack(attackerId) { await runRules("encounterEnemy", attackerId); }

  const moduleLoaded = !!scene.module_id;
  const currentRoom = scene.current_room || {};
  const hpPct = pc.max_hp > 0 ? Math.max(0, Math.min(100, Math.round(100 * (pc.hp || 0) / pc.max_hp))) : 0;

  // 非 module_adventure 剧本（小说 / freeform）显式说明此 tab 不适用，
  // 避免在小说存档里误显示一套不属于该剧本的 5E 默认角色卡 + 模组按钮。
  // 加载模组的入口只在 Platform『冒险模组』页（那里会建新存档，不污染当前剧本）。
  const packKind = contentPack.kind || "freeform";
  if (packKind !== "module_adventure") {
    const packTitle = packKind === "novel_adaptation" ? "小说改编剧本" : "通用 / freeform 剧本";
    return (
      <div className="gp-stack">
        <div className="gp-section">
          <div className="section-head">
            <h3>5E 规则不适用</h3>
            <span className="pill"><span className="dot" /> {packTitle}</span>
          </div>
          <p className="gp-bio" style={{margin: "8px 0 0"}}>
            当前剧本是「<strong>{packTitle}</strong>」，不使用 5E 兼容规则。
            该 tab 仅在加载了 <code>module_adventure</code> 类型的冒险模组（如灰烬矿坑 / Ash Mine）后才会显示
            角色卡 / 房间 / 战斗 / 骰子日志等内容。
          </p>
          <p className="muted-2" style={{fontSize: 12.5, marginTop: 10}}>
            若要尝试 5E 模组，请到平台首页『冒险模组』栏目选择并启动 —— 系统会自动建立一个独立的新存档，
            不会污染当前剧本。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="gp-stack">
      {/* 模组元信息 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>冒险模组 · {ruleset.public_label || "5E compatible / 五版规则兼容"}</h3>
          <span className="pill ok"><span className="dot ok" /> 已加载</span>
        </div>
        <div className="gp-kv">
          <div className="gp-row"><span className="gp-label">模组</span><strong>{(scene.module_manifest||{}).name_cn || (scene.module_manifest||{}).name || scene.module_id}</strong></div>
          <div className="gp-row"><span className="gp-label">tagline</span><span style={{fontStyle:"italic",opacity:0.85}}>{(scene.module_manifest||{}).tagline || "—"}</span></div>
        </div>
        {errorMsg ? <p className="muted-2" style={{color:"var(--danger)",marginTop:6}}>{errorMsg}</p> : null}
      </div>

      {/* 角色卡 */}
      <div className="gp-section">
        <div className="section-head"><h3>角色卡</h3>{pc.level ? <span className="pill"><span className="dot" /> Lv {pc.level}</span> : null}</div>
        <div className="gp-kv">
          <div className="gp-row"><span className="gp-label">姓名</span><strong>{pc.name || "—"}</strong></div>
          <div className="gp-row"><span className="gp-label">职业</span><span>{pc.class_name || "—"}</span></div>
          <div className="gp-row"><span className="gp-label">种族</span><span>{pc.species || "—"}</span></div>
          <div className="gp-row"><span className="gp-label">HP</span><span>{pc.hp || 0} / {pc.max_hp || 0}
            <span style={{display:"inline-block",width:80,height:6,background:"var(--panel-3)",borderRadius:3,marginLeft:8,verticalAlign:"middle"}}>
              <span style={{display:"block",height:"100%",width:`${hpPct}%`,background:hpPct>50?"var(--green)":hpPct>25?"var(--accent)":"var(--danger)",borderRadius:3}} />
            </span>
          </span></div>
          <div className="gp-row"><span className="gp-label">AC</span><span>{pc.ac || "—"}</span></div>
          <div className="gp-row"><span className="gp-label">熟练加值</span><span>+{pc.proficiency_bonus || 0}</span></div>
        </div>
        {pc.abilities && Object.keys(pc.abilities).length > 0 ? (
          <div style={{display:"grid",gridTemplateColumns:"repeat(6,1fr)",gap:4,marginTop:6,fontSize:12}}>
            {["str","dex","con","int","wis","cha"].map(a => {
              const score = pc.abilities[a];
              if (score == null) return null;
              const mod = Math.floor((score - 10) / 2);
              return (
                <div key={a} style={{textAlign:"center",padding:"4px 0",background:"var(--panel-3)",borderRadius:4}}>
                  <div className="muted-2" style={{fontSize:10,textTransform:"uppercase"}}>{a}</div>
                  <strong>{score}</strong>
                  <div className="muted-2" style={{fontSize:10}}>{mod >= 0 ? "+" : ""}{mod}</div>
                </div>
              );
            })}
          </div>
        ) : null}
        {Array.isArray(pc.conditions) && pc.conditions.length ? (
          <div style={{marginTop:6}}>
            <span className="muted-2" style={{fontSize:11,marginRight:6}}>状态：</span>
            {pc.conditions.map((c,i) => <span key={i} className="pill" style={{marginRight:4}}>{c}</span>)}
          </div>
        ) : null}
      </div>

      {/* 当前房间 */}
      {moduleLoaded ? (
        <div className="gp-section">
          <div className="section-head"><h3>当前房间</h3><span className="muted-2 mono" style={{fontSize:11}}>{scene.location_id}</span></div>
          <div className="gp-kv">
            <div className="gp-row"><span className="gp-label">名称</span><strong>{currentRoom.name || "—"}</strong></div>
          </div>
          <p className="gp-bio" style={{whiteSpace:"pre-wrap"}}>{currentRoom.description || ""}</p>
          {Array.isArray(currentRoom.visible_clues) && currentRoom.visible_clues.length ? (
            <div style={{marginTop:6}}>
              <div className="muted-2" style={{fontSize:11,marginBottom:3}}>可见线索</div>
              <ul style={{margin:0,paddingLeft:16}}>
                {currentRoom.visible_clues.map((c,i) => <li key={i} style={{fontSize:12}}>{c.text || c}</li>)}
              </ul>
            </div>
          ) : null}
          {Array.isArray(currentRoom.exits) && currentRoom.exits.length ? (
            <div style={{marginTop:6}}>
              <div className="muted-2" style={{fontSize:11,marginBottom:3}}>出口</div>
              <div style={{display:"flex",gap:4,flexWrap:"wrap"}}>
                {currentRoom.exits.map((e,i) => (
                  <button key={i} disabled={busy} onClick={() => move(e.to)} style={{fontSize:12}}>
                    {e.label || e.to}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {Array.isArray(currentRoom.checks) && currentRoom.checks.length ? (
            <div style={{marginTop:8}}>
              <div className="muted-2" style={{fontSize:11,marginBottom:3}}>可发起检定</div>
              <div style={{display:"flex",gap:4,flexWrap:"wrap"}}>
                {currentRoom.checks.map((c,i) => (
                  <button key={i} disabled={busy} onClick={() => doAction({
                    kind: c.kind || "skill_check",
                    skill: c.skill,
                    ability: c.ability,
                    dc: c.dc,
                    reason: c.fact || c.reveals,
                    sets_flag: c.sets_flag,
                  })} style={{fontSize:12}}>
                    {c.kind === "saving_throw" ? `${(c.ability||"").toUpperCase()} 豁免 DC ${c.dc}` : `${c.skill} DC ${c.dc}`}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {(currentRoom.flags || {}).can_short_rest ? (
            <div style={{marginTop:6}}>
              <button disabled={busy} onClick={() => doAction({kind:"short_rest"})}>短休（花生命骰）</button>
            </div>
          ) : null}
          {Array.isArray(currentRoom.enemies) && currentRoom.enemies.length && !encounter.active ? (
            <div style={{marginTop:8}}>
              <div className="muted-2" style={{fontSize:11,marginBottom:3}}>遭遇</div>
              <button disabled={busy} className="primary" onClick={() => startEncounter(`${scene.location_id}_combat`)} style={{fontSize:12}}>
                开战！
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* 战斗面板 */}
      {encounter.active ? (
        <div className="gp-section">
          <div className="section-head">
            <h3>战斗中 · 第 {encounter.round} 回合</h3>
            <span className="pill ok"><span className="dot ok" /> 回合 {encounter.turn_index + 1}/{(encounter.initiative_order||[]).length}</span>
          </div>
          <div style={{marginTop:6}}>
            <div className="muted-2" style={{fontSize:11,marginBottom:3}}>先攻顺序</div>
            <ol style={{margin:0,paddingLeft:18}}>
              {(encounter.initiative_order||[]).map((o,i) => {
                const isCurrent = i === encounter.turn_index;
                const comb = (encounter.combatants||[]).find(c => c.id === o.id) || {};
                return (
                  <li key={i} style={{fontSize:12,fontWeight:isCurrent?700:400,opacity:comb.defeated?0.5:1}}>
                    {o.name} <span className="muted-2">({o.init}, {comb.side})</span> · HP {comb.hp}/{comb.max_hp}
                    {comb.defeated ? " (倒下)" : ""}
                  </li>
                );
              })}
            </ol>
          </div>
          <div style={{display:"flex",gap:4,flexWrap:"wrap",marginTop:8}}>
            {(encounter.combatants||[]).filter(c => c.side === "enemy" && !c.defeated).map(e => (
              <button key={e.id} disabled={busy} className="primary" onClick={() => doAction({kind:"attack", target: e.id})} style={{fontSize:12}}>
                攻击 {e.name}
              </button>
            ))}
            <button disabled={busy} onClick={nextTurn} style={{fontSize:12}}>下一回合</button>
            {(encounter.combatants||[]).filter(c => c.side === "enemy" && !c.defeated).map(e => (
              <button key={`enemy-${e.id}`} disabled={busy} onClick={() => enemyAttack(e.id)} style={{fontSize:12,background:"var(--panel-3)"}}>
                让 {e.name} 出招
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {/* 骰子日志 */}
      <div className="gp-section">
        <div className="section-head"><h3>骰子日志</h3><span className="muted-2 mono" style={{fontSize:11}}>{diceLog.length} 条</span></div>
        {diceLog.length === 0 ? (
          <p className="muted-2" style={{fontSize:12}}>尚未掷骰。开始模组并执行检定后将在此显示。</p>
        ) : (
          <ul style={{margin:0,paddingLeft:0,listStyle:"none",maxHeight:240,overflowY:"auto"}}>
            {diceLog.slice().reverse().map((d,i) => (
              <li key={d.id || i} style={{padding:"4px 6px",borderBottom:"1px solid var(--line-soft)",fontSize:12}}>
                <div>
                  <strong>{d.kind}</strong>
                  {d.actor ? <span className="muted-2"> · {d.actor}</span> : null}
                  {d.target ? <span className="muted-2"> → {d.target}</span> : null}
                  {d.success === true ? <span className="pill ok" style={{marginLeft:6}}>成功</span>
                    : d.success === false ? <span className="pill" style={{marginLeft:6,background:"var(--danger)",color:"#fff"}}>失败</span>
                    : null}
                </div>
                <div className="muted-2" style={{fontSize:11}}>
                  {d.expression || ""} = [{(d.rolls||[]).join(",")}]{typeof d.modifier === "number" && d.modifier ? ` ${d.modifier>=0?"+":""}${d.modifier}` : ""}
                  {typeof d.total === "number" ? ` → ${d.total}` : ""}
                  {typeof d.dc === "number" ? ` vs DC ${d.dc}` : ""}
                  {d.damage ? ` · 伤害 ${d.damage.total}` : ""}
                  {d.reason ? ` · ${d.reason}` : ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function RightPanel({ state, activeTab, setActiveTab, sidebarWidth, density, collapsed, onToggle, resizeHandle }) {
  const tabs = PANEL_TABS;
  const active = tabs.find(t => t.id === activeTab) || tabs[0];
  let body = null;
  if (activeTab === "status") body = <PanelStatus state={state} panelWidth={sidebarWidth} />;
  else if (activeTab === "rules") body = <PanelRules state={state} />;
  else if (activeTab === "memory") body = <PanelMemory state={state} density={density} panelWidth={sidebarWidth} />;
  else if (activeTab === "worldbook") body = <PanelWorldbook state={state} panelWidth={sidebarWidth} />;
  else if (activeTab === "cards") body = <PanelCharacters state={state} panelWidth={sidebarWidth} />;
  else if (activeTab === "timeline") body = <PanelTimeline state={state} panelWidth={sidebarWidth} />;
  else if (activeTab === "context") body = <PanelContext state={state} />;
  else if (activeTab === "debug") body = <PanelDebug state={state} />;

  return (
    <aside className={`gp-panel ${collapsed ? "collapsed" : ""}`} style={{width: collapsed ? 0 : sidebarWidth}} aria-hidden={collapsed}>
      {!collapsed && resizeHandle}
      <div className="gp-panel-inner" style={{width: sidebarWidth}}>
        <header className="gp-panel-head">
          <div className="gp-tabs">
            <button className="iconbtn gp-collapse-btn" onClick={onToggle} data-tip="折叠面板" data-tip-pos="below">
              <Icon name="chevron_right" size={14} />
            </button>
            <span className="gp-tabs-sep" />
            {tabs.map(t => (
              <button
                key={t.id}
                className={`gp-tab ${activeTab === t.id ? "active" : ""}`}
                onClick={() => setActiveTab(t.id)}
                data-tip={t.label}
                data-tip-pos="below"
                aria-label={t.label}
              >
                <Icon name={t.icon} size={15} />
              </button>
            ))}
          </div>
          <div className="gp-panel-title">
            <h3>{active.label}</h3>
            <span className="muted-2 mono">{active.id}</span>
          </div>
        </header>
        <div className={`gp-panel-body${sidebarWidth < 280 ? " narrow" : ""}`}>{body}</div>
      </div>
    </aside>
  );
}

export { RightPanel, PANEL_TABS, PanelRules, PanelCharacters, PanelStatus, PanelContext, PanelMemory, PanelTimeline, PanelWorldbook, PanelDebug, CharacterCard, WorldlineAnchorsSection };
