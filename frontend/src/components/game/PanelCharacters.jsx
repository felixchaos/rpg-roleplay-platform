/* 三层人物系统(人物 tab)—— 纯机械从 game-panels.jsx 搬出,零行为变化。 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import AvatarImg from '../AvatarImg.jsx';
import { InlineEditField } from './InlineEditField.jsx';

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

function _entityTypeLabel(kind, source, t) {
  if (kind === "enemy") return t('game.characters.entity_enemy');
  if (kind === "npc") return t('game.characters.entity_npc');
  if (kind === "ally") return t('game.characters.entity_ally');
  if (kind === "unknown" && source === "gm_provisional") return t('game.characters.entity_unconfirmed');
  return "—";
}

function CharacterCard({ name, info, subtitle, avatarPath, onEditStatus, onDelete, invoked, agenda }) {
  const { t } = useTranslation();
  // info: { tone | disposition, note?, role? }
  // 可选 props: onEditStatus(newValue)/onDelete() — 仅 relationships 区传入,
  //            on-stage/pinned 不传,保持原本只读语义。
  // agenda: state.npc_agendas[name] — {goal, stance, updated_turn},不存在则零渲染。
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
        <AvatarImg src={avatarPath || null} name={name} className="gp-card-avatar serif" />
        <div style={{minWidth: 0, flex: 1}}>
          <div className="gp-card-name">{name}</div>
          <div className="gp-card-tone">
            {onEditStatus ? (
              // 关系区:click-to-edit 状态文本(替代静态 pill)
              <span className={`pill ${toneColor}`} style={{paddingRight: 6}}>
                <span className={`dot ${toneColor}`} />
                <InlineEditField value={dispLabel === "—" ? "" : dispLabel}
                  placeholder={t('game.characters.status_placeholder')}
                  emptyLabel={t('game.characters.set_status')}
                  onSubmit={onEditStatus} />
              </span>
            ) : (
              <span className={`pill ${toneColor}`}><span className={`dot ${toneColor}`} />{dispLabel}</span>
            )}
            {subtitle ? <span className="muted-2 mono" style={{marginLeft: 6, fontSize: 11}}>{subtitle}</span> : null}
            {invoked ? (
              <span className="pill" title={t('game.characters.invoked_tip')}
                style={{marginLeft: 6, color: "var(--accent)", borderColor: "var(--accent)", background: "var(--accent-soft)"}}>
                <span className="dot" style={{background: "var(--accent)"}} />{t('game.characters.invoked_badge')}
              </span>
            ) : null}
          </div>
        </div>
        {/* 仅保留 @mention 插入交互;移除『编辑』『转为用户角色卡』按钮 —
            创建 / 提升只在平台『角色卡』页操作 (Codex 评审硬要求)。 */}
        <button className="iconbtn" data-tip={t('game.characters.mention_tip')} data-tip-pos="below"
          onClick={() => {
            if (typeof window.__rpgInsertMention === "function") window.__rpgInsertMention(name);
            else if (navigator.clipboard) {
              navigator.clipboard.writeText("@" + name);
              window.__apiToast?.(t('game.characters.mention_copied', { name }), { kind: "ok", duration: 1500 });
            }
          }}>
          <Icon name="at" size={14} />
        </button>
        {onDelete ? (
          <button className="iconbtn" data-tip={t('game.characters.delete_relationship_tip')} data-tip-pos="below"
            onClick={onDelete}>
            <Icon name="close" size={12} />
          </button>
        ) : null}
      </div>
      {(info.note || info.role) ? (
        <p className="gp-card-note">{info.note || info.role}</p>
      ) : null}
      {agenda ? (
        <div className="muted-2" style={{fontSize: 11.5, lineHeight: 1.6, marginTop: 4}}>
          {agenda.goal ? <div>{t('game.characters.agenda_goal')}: {agenda.goal}</div> : null}
          {agenda.stance ? <div>{t('game.characters.agenda_stance')}: {agenda.stance}</div> : null}
          {agenda.updated_turn ? <div>{t('game.characters.agenda_turn', { turn: agenda.updated_turn })}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

function PanelCharacters({ state }) {
  const { t } = useTranslation();
  // ── 数据源 ─────────────────────────────────────────────
  // 1. active_entities: 后端在 enter_room / start_encounter 时同步的运行时索引
  // 2. encounter.combatants: 战斗中 enemy/ally combatants (active_entities 里
  //    应该已经有,但兜底:即便 active_entities 还没同步,也能从 combatants 临时构造)
  // 3. relationships: 玩家与角色的明确态度变化(可能是 string 也可能是 dict)
  const activeRaw = Array.isArray(state && state.active_entities) ? state.active_entities : [];
  const encounter = (state && state.encounter) || {};
  const combatants = Array.isArray(encounter.combatants) ? encounter.combatants : [];
  const relationships = (state && state.relationships) || {};
  // NPC 议程(活世界柱子3):后端 state.npc_agendas,可能为空 dict 或(旧后端)undefined。
  const agendas = (state && state.npc_agendas) || {};

  // A#1:本轮注入 GM 上下文的角色卡 —— 后端 _active_character_cards(grep scan_text + anchor 强制)
  // 把命中卡写进 last_context 的 npc_cards 层(core.py 每层保留 items)。从中取卡名 + 命中别名,
  // 给侧栏对应卡加「本轮调用」标记。空/首屏(尚无回合)→ 集合空 → 不显示标记。
  const _normName = (s) => String(s || "").trim().toLowerCase();
  const invokedNames = (() => {
    const set = new Set();
    const layers = (state && state.memory && state.memory.last_context && state.memory.last_context.layers) || [];
    const npc = Array.isArray(layers) ? layers.find((l) => l && l.id === "npc_cards") : null;
    const items = (npc && Array.isArray(npc.items)) ? npc.items : [];
    for (const it of items) {
      if (it && it.name) set.add(_normName(it.name));
      for (const m of (it && Array.isArray(it.matched) ? it.matched : [])) {
        if (m) set.add(_normName(m));
      }
    }
    return set;
  })();
  const isInvoked = (nm) => invokedNames.size > 0 && invokedNames.has(_normName(nm));

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
    if (info && typeof info === "object") return { tone: info.tone || t('game.characters.normalize_neutral'), note: info.note || info.description || "" };
    return { tone: t('game.characters.normalize_neutral'), note: "" };
  };
  const relEntries = Object.entries(relationships).map(([name, info]) => ({ name, info: normalize(info) }));

  // 已固定角色卡:active_entities 里 card_id 不空的
  const pinned = activeRaw.filter(e => e && e.card_id);

  return (
    <div className="gp-stack">
      {/* 当前在场 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>{t('game.characters.on_stage')}<span className="muted-2" style={{marginLeft: 8, fontSize: 11, textTransform: "none"}}>{t('game.characters.on_stage_subtitle')}</span></h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{inScene.length}</span>
        </div>
        {inScene.length === 0 ? (
          <div className="muted-2" style={{padding: "12px 4px", fontSize: 12.5, lineHeight: 1.7}}>
            {t('game.characters.on_stage_empty')}
          </div>
        ) : (
          <div className="gp-cards">
            {inScene.map((e) => {
              const subtitle = _entityTypeLabel(e.kind, e.source, t) +
                (e.hp != null && e.max_hp != null ? ` · HP ${e.hp}/${e.max_hp}` : "");
              return (
                <CharacterCard key={e.id}
                  name={e.name || e.id}
                  info={{ disposition: e.disposition, note: e.role || "", role: e.role }}
                  subtitle={subtitle}
                  avatarPath={e.avatar_path}
                  invoked={isInvoked(e.name || e.id)}
                  agenda={agendas[e.name || e.id]}
                />
              );
            })}
          </div>
        )}
      </div>

      {/* 关系 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>{t('game.characters.relationships')}<span className="muted-2" style={{marginLeft: 8, fontSize: 11, textTransform: "none"}}>{t('game.characters.relationships_subtitle')}</span></h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{relEntries.length}</span>
        </div>
        {relEntries.length === 0 ? (
          <div className="muted-2" style={{padding: "12px 4px", fontSize: 12.5, lineHeight: 1.7}}>
            {t('game.characters.relationships_empty')}
          </div>
        ) : (
          <div className="gp-cards">
            {relEntries.map(({ name, info }) => (
              <CharacterCard key={name} name={name} info={info}
                agenda={agendas[name]}
                onEditStatus={async (status) => {
                  await window.api.game.relationshipSet({ character: name, status });
                  window.__apiToast?.(t('game.characters.relationship_updated', { name, status }), { kind: "ok", duration: 1500 });
                }}
                onDelete={async () => {
                  if (!await window.__confirm({ message: t('game.characters.delete_relationship_confirm', { name }), danger: true })) return;
                  try { await window.api.game.relationshipDelete({ character: name });
                    try { window.dispatchEvent(new CustomEvent('game-state-refresh')); } catch (_) {}
                    window.__apiToast?.(t('game.characters.deleted_ok'), { kind: "ok" }); }
                  catch (e) { window.__apiToast?.(t('game.characters.delete_failed'), { kind: "danger", detail: e?.message }); }
                }}
              />
            ))}
          </div>
        )}
        {/* 手动添加关系入口 */}
        <button className="iconbtn" style={{marginTop: 8, fontSize: 12, padding: "4px 10px", width: "auto"}} aria-label={t('game.characters.add_relationship')}
          onClick={async () => {
            const ch = await window.__prompt({ title: t('game.characters.npc_name_prompt') });
            if (!ch) return;
            const st = await window.__prompt({ title: t('game.characters.relationship_status_prompt', { name: ch }), default: t('game.characters.status_default') });
            if (!st) return;
            try { await window.api.game.relationshipSet({ character: ch.trim(), status: st.trim() });
              window.__apiToast?.(t('game.characters.relationship_updated', { name: ch, status: st }), { kind: "ok" }); }
            catch (e) { window.__apiToast?.(t('game.characters.add_failed'), { kind: "danger", detail: e?.message }); }
          }}>
          <Icon name="plus" size={12} /> {t('game.characters.add_relationship')}
        </button>
      </div>

      {/* 已固定角色卡 — 只在有时显示,避免空区污染 */}
      {pinned.length > 0 ? (
        <div className="gp-section">
          <div className="section-head">
            <h3>{t('game.characters.pinned_cards')}<span className="muted-2" style={{marginLeft: 8, fontSize: 11, textTransform: "none"}}>{t('game.characters.pinned_cards_subtitle')}</span></h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>{pinned.length}</span>
          </div>
          <div className="gp-cards">
            {pinned.map((e) => (
              <CharacterCard key={e.id}
                name={e.name || e.id}
                info={{ disposition: e.disposition, note: e.role || "", role: e.role }}
                subtitle={t('game.characters.pinned_suffix', { card_id: e.card_id })}
                avatarPath={e.avatar_path}
                invoked={isInvoked(e.name || e.id)}
              />
            ))}
          </div>
        </div>
      ) : null}

      {/* 创建 / 提升入口提示 — 引导用户去平台,不在此创建 */}
      <div className="gp-section" style={{background: "transparent", borderTop: "1px dashed var(--line)", marginTop: 4}}>
        <p className="muted-2" style={{fontSize: 12, lineHeight: 1.7, margin: "8px 4px 0"}}>
          {t('game.characters.platform_tip')}<strong>{t('game.characters.platform_link')}</strong>{t('game.characters.platform_tip2')}
        </p>
      </div>
    </div>
  );
}

// CharacterEditModal 已删除 — 创建 / 编辑 / 提升用户角色卡的 UI 只能在
// 平台『角色卡』页 (platform-app.jsx → promoteNpcToUserCard)。
// 游戏内人物侧边栏只展示运行时实体,不创建任何持久化卡片。

export { CharacterCard, PanelCharacters };
