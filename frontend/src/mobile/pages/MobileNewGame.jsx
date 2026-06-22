/* MobileNewGame — 移动端新游戏向导(5 步)
   铁律:
   ① 只用 mobile.css 已有 class 或 .m-ng-* 前缀新 class + inline style。
   ② 逻辑数据复用 window.api.* / window.__createAndEnterSave。
   ③ 出身×身份联动约束严格对齐 saves.jsx ALLOWED_SOURCES 逻辑。
   ④ export function MobileNewGame({ nav, scriptId, onDone }) + export default。
   props:
     nav       — MobileRoot nav 对象(nav.pop / nav.toast 等)
     scriptId  — 传入时锁定剧本跳过步骤 1 的剧本选择
     onDone    — 可选:创建成功回调
*/

import React from 'react';
import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';
import { Icon } from '../icons.jsx';
import { lsGet, lsSet, lsGetJSON, lsSetJSON, lsRemove } from '../../lib/storage.js';

/* ================================================================
   常量 & 工具
   ================================================================ */

// 出身×身份来源约束(与 saves.jsx IdentityStep 保持完全一致)
const ALLOWED_SOURCES = {
  soul:   ['none', 'npc', 'ai', 'manual'],  // 灵魂穿越:占据原住民肉身 → 全开
  body:   ['none'],                          // 整体穿越:彻底外来者无本地身份 → 仅「不挂」
  dual:   ['npc', 'ai', 'manual'],           // 双魂同体:须有本地本体 → 不能不挂
  native: ['none', 'ai', 'manual'],          // 本世界人:你就是该角色 → 不能再选另一个原著人物
};

const ORIGIN_OPTIONS = [
  {
    value: 'soul', icon: '◈', labelKey: 'mobile.new_game.origin.soul.label',
    essenceKey: 'mobile.new_game.origin.soul.essence',
    mappingKey: 'mobile.new_game.origin.soul.mapping',
    hintKey: 'mobile.new_game.origin.soul.hint',
    accentColor: '#8db4e8', accentBg: 'rgba(85,130,200,.14)', accentBorder: 'rgba(85,130,200,.38)',
  },
  {
    value: 'body', icon: '◉', labelKey: 'mobile.new_game.origin.body.label',
    essenceKey: 'mobile.new_game.origin.body.essence',
    mappingKey: 'mobile.new_game.origin.body.mapping',
    hintKey: 'mobile.new_game.origin.body.hint',
    accentColor: '#e8a87c', accentBg: 'rgba(220,140,80,.14)', accentBorder: 'rgba(220,140,80,.38)',
  },
  {
    value: 'dual', icon: '◑', labelKey: 'mobile.new_game.origin.dual.label',
    essenceKey: 'mobile.new_game.origin.dual.essence',
    mappingKey: 'mobile.new_game.origin.dual.mapping',
    hintKey: 'mobile.new_game.origin.dual.hint',
    accentColor: '#b8a0e8', accentBg: 'rgba(160,130,210,.14)', accentBorder: 'rgba(160,130,210,.38)',
  },
  {
    value: 'native', icon: '◎', labelKey: 'mobile.new_game.origin.native.label',
    essenceKey: 'mobile.new_game.origin.native.essence',
    mappingKey: 'mobile.new_game.origin.native.mapping',
    hintKey: 'mobile.new_game.origin.native.hint',
    accentColor: '#b8b0a5', accentBg: 'rgba(150,143,133,.14)', accentBorder: 'rgba(150,143,133,.32)',
  },
];

// SOURCE_LABELS: keys rendered via t() inside StepIdentity

const STEPS = [
  { n: 0, titleKey: 'mobile.new_game.steps.script_birth' },
  { n: 1, titleKey: 'mobile.new_game.steps.role' },
  { n: 2, titleKey: 'mobile.new_game.steps.identity' },
  { n: 3, titleKey: 'mobile.new_game.steps.meta' },
  { n: 4, titleKey: 'mobile.new_game.steps.confirm' },
];

const TOTAL_STEPS = STEPS.length;

const NEWGAME_ACTIVE_IMPORT_STATUSES = new Set(['queued', 'pending', 'running', 'processing', 'importing', 'started']);
const NEWGAME_IMPORT_TERMINAL_STATUSES = new Set(['done', 'done_with_errors', 'partial', 'failed', 'cancelled']);
const NEWGAME_BLOCKING_READINESS_KEYS = new Set(['chunks', 'anchors']);

function scriptBlockReason(script) {
  if (!script) return '';
  const status = String(
    script.import_status || script.job_status ||
    script.active_job?.status || script.readiness?.active_job?.status || ''
  ).trim().toLowerCase();
  if (status && NEWGAME_ACTIVE_IMPORT_STATUSES.has(status) && !NEWGAME_IMPORT_TERMINAL_STATUSES.has(status)) {
    return i18n.t('mobile.new_game.script_block.importing');
  }
  const missing = Array.isArray(script.readiness?.missing) ? script.readiness.missing : [];
  const blocking = missing.filter(k => NEWGAME_BLOCKING_READINESS_KEYS.has(k));
  if (blocking.length > 0) return i18n.t('mobile.new_game.script_block.missing', { keys: blocking.join(', ') });
  if (Number(script.chapter_count || 0) <= 0) return i18n.t('mobile.new_game.script_block.no_chapters');
  return '';
}

/* ================================================================
   Step 进度条
   ================================================================ */
function StepDots({ step, total }) {
  return (
    <div style={{ display: 'flex', gap: 5, alignItems: 'center', flex: 1 }}>
      {Array.from({ length: total }, (_, i) => (
        <div key={i} style={{
          height: 3, flex: 1, borderRadius: 99,
          background: i < step ? 'var(--accent)' : i === step ? 'rgba(201,100,66,.5)' : 'var(--line)',
          transition: 'background .2s',
        }} />
      ))}
      <span style={{ fontSize: 10, color: 'var(--muted-2)', whiteSpace: 'nowrap', marginLeft: 4, fontFamily: 'var(--font-mono)' }}>
        {step + 1}/{total}
      </span>
    </div>
  );
}

/* ================================================================
   错误条
   ================================================================ */
function ErrBar({ msg }) {
  if (!msg) return null;
  return (
    <div style={{
      color: 'var(--danger)', padding: '9px 12px',
      border: '1px solid rgba(200,103,93,.3)', borderRadius: 10,
      fontSize: 12.5, background: 'var(--danger-soft)', lineHeight: 1.5,
    }}>
      {msg}
    </div>
  );
}

/* ================================================================
   加载占位
   ================================================================ */
function Loading({ text }) {
  const { t } = useTranslation();
  return (
    <div className="pl-empty" style={{ padding: '28px 20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, fontSize: 13, color: 'var(--muted)' }}>
        <Icon name="spinner" size={14} className="spin" /> {text || t('common.loading')}
      </div>
    </div>
  );
}

/* ================================================================
   FieldLabel
   ================================================================ */
// 语义统一 #36(保留):FieldLabel 只渲染「标签 + hint」块、不含控件 children,与
// mobile/Field.jsx 的 Field(label+desc+控件)不同形,且为纯内联样式 → 不收口,保留本地实现。
function FieldLabel({ children, hint }) {
  return (
    <div style={{ marginBottom: 7 }}>
      <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--text-quiet)' }}>{children}</div>
      {hint && <div style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 2, lineHeight: 1.5 }}>{hint}</div>}
    </div>
  );
}

/* ================================================================
   STEP 0 — 剧本与出生点
   ================================================================ */
function StepScriptBirth({ scripts, lockedScriptId, scriptId, setScriptId, birthpoint, setBirthpoint }) {
  const { t } = useTranslation();
  const [phases, setPhases] = useState([]);
  const [bpLoading, setBpLoading] = useState(false);
  const [bpErr, setBpErr] = useState('');
  const [openPhase, setOpenPhase] = useState(null);

  const fetchBp = useCallback(() => {
    if (!scriptId) { setPhases([]); return; }
    setBpLoading(true); setBpErr('');
    (async () => {
      try {
        const r = await window.api.scripts.birthpoints(parseInt(scriptId, 10));
        const data = r || {};
        if (Array.isArray(data.phases) && data.phases.length > 0) {
          setPhases(data.phases);
          setOpenPhase(prev => prev || (data.phases[0]?.phase_label ?? null));
        } else {
          setPhases([]);
        }
      } catch (_) {
        setBpErr(t('mobile.new_game.birthpoint.load_error'));
        setPhases([]);
      } finally {
        setBpLoading(false);
      }
    })();
  }, [scriptId]);

  useEffect(() => { fetchBp(); }, [fetchBp]);

  // 剧本切换时清空出生点
  const prevScriptRef = useRef(scriptId);
  useEffect(() => {
    if (prevScriptRef.current !== scriptId) {
      setBirthpoint(null);
      prevScriptRef.current = scriptId;
    }
  }, [scriptId, setBirthpoint]);

  const selScript = scripts.find(s => String(s.id) === String(scriptId)) || null;
  const blockReason = scriptBlockReason(selScript);

  return (
    <div style={{ display: 'grid', gap: 20 }}>
      {/* 剧本选择 */}
      {!lockedScriptId && (
        <div>
          <FieldLabel hint={t('mobile.new_game.script.hint')}>{t('mobile.new_game.script.label')}</FieldLabel>
          {scripts.length === 0 ? (
            <div style={{ fontSize: 12.5, color: 'var(--muted)', padding: '10px 0' }}>
              {t('mobile.new_game.script.empty')}
            </div>
          ) : (
            <div style={{ display: 'grid', gap: 7 }}>
              {scripts.map(sc => {
                const reason = scriptBlockReason(sc);
                const sel = String(sc.id) === String(scriptId);
                return (
                  <button
                    key={sc.id}
                    disabled={!!reason}
                    onClick={() => { setScriptId(String(sc.id)); setBirthpoint(null); }}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 12, width: '100%',
                      padding: '12px 13px', border: sel ? '1px solid var(--accent-edge)' : '1px solid var(--line-soft)',
                      borderRadius: 12, background: sel ? 'var(--accent-soft)' : 'var(--panel)',
                      textAlign: 'left', transition: 'border-color .12s, background .12s',
                      opacity: reason ? 0.5 : 1, cursor: reason ? 'not-allowed' : 'pointer',
                    }}
                  >
                    <span style={{ width: 8, height: 8, borderRadius: 99, flexShrink: 0, background: sel ? 'var(--accent)' : 'var(--muted-3)' }} />
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: 'block', fontFamily: 'var(--font-serif)', fontSize: 14, color: sel ? 'var(--accent)' : 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{sc.title}</span>
                      {reason && <span style={{ display: 'block', fontSize: 11, color: 'var(--warn)', marginTop: 2 }}>{reason}</span>}
                      {!reason && sc.chapter_count != null && <span style={{ display: 'block', fontSize: 10.5, color: 'var(--muted-2)', fontFamily: 'var(--font-mono)', marginTop: 2 }}>{t('mobile.new_game.script.chapter_count', { count: sc.chapter_count })}</span>}
                    </span>
                    {sel && <Icon name="check" size={14} style={{ color: 'var(--accent)', flexShrink: 0 }} />}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {lockedScriptId && selScript && (
        <div style={{ padding: '10px 13px', border: '1px solid var(--accent-edge)', borderRadius: 12, background: 'var(--accent-soft)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <Icon name="book_open" size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
          <span style={{ fontFamily: 'var(--font-serif)', fontSize: 14, color: 'var(--accent)' }}>{selScript.title}</span>
        </div>
      )}

      {blockReason && (
        <div style={{ padding: '9px 12px', border: '1px solid rgba(212,179,102,.3)', borderRadius: 10, background: 'var(--warn-soft)', fontSize: 12.5, color: 'var(--warn)' }}>
          {blockReason}
        </div>
      )}

      {/* 出生点 */}
      {scriptId && !blockReason && (
        <div>
          <FieldLabel hint={t('mobile.new_game.birthpoint.hint')}>{t('mobile.new_game.birthpoint.label')}</FieldLabel>
          <ErrBar msg={bpErr} />
          {bpLoading && <Loading text={t('mobile.new_game.birthpoint.loading')} />}
          {!bpLoading && phases.length === 0 && !bpErr && (
            <div style={{ fontSize: 12, color: 'var(--muted)', padding: '8px 0' }}>
              {t('mobile.new_game.birthpoint.empty')}
              <button onClick={fetchBp} style={{ marginLeft: 8, fontSize: 12, color: 'var(--accent)' }}>{t('common.refresh')}</button>
            </div>
          )}
          {!bpLoading && phases.length > 0 && (
            <div style={{ display: 'grid', gap: 6 }}>
              {phases.map(phase => {
                const isOpen = openPhase === phase.phase_label;
                return (
                  <div key={phase.phase_label} style={{ border: '1px solid var(--line-soft)', borderRadius: 10, overflow: 'hidden' }}>
                    <button
                      onClick={() => setOpenPhase(isOpen ? null : phase.phase_label)}
                      style={{
                        width: '100%', textAlign: 'left', display: 'flex', alignItems: 'center',
                        justifyContent: 'space-between', gap: 10, padding: '10px 13px',
                        background: isOpen ? 'var(--panel-2)' : 'transparent',
                        borderBottom: isOpen ? '1px solid var(--line-soft)' : 'none',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Icon name={isOpen ? 'chevron_down' : 'chevron_right'} size={11} style={{ color: 'var(--muted)', flexShrink: 0 }} />
                        <span style={{ fontFamily: 'var(--font-serif)', fontSize: 13.5 }}>{phase.phase_label}</span>
                      </div>
                      <span style={{ fontSize: 10.5, color: 'var(--muted-2)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>
                        {t('mobile.new_game.birthpoint.chapter_range', { min: phase.chapter_min, max: phase.chapter_max })}
                      </span>
                    </button>
                    {isOpen && (
                      <div style={{ display: 'grid', gap: 4, padding: '8px 10px' }}>
                        {(phase.anchors || []).map(anchor => {
                          const isSel = birthpoint && birthpoint.anchor_id === anchor.anchor_id;
                          return (
                            <label key={anchor.anchor_id} style={{
                              display: 'grid', gridTemplateColumns: '16px 1fr auto', gap: 10,
                              padding: '10px 11px', borderRadius: 9, cursor: 'pointer',
                              border: isSel ? '1px solid var(--accent-edge)' : '1px solid var(--line-soft)',
                              background: isSel ? 'var(--accent-soft)' : 'var(--panel)',
                              alignItems: 'start', transition: 'border-color .12s, background .12s',
                            }}>
                              <input type="radio" checked={!!isSel} onChange={() => setBirthpoint({
                                phase_label: phase.phase_label,
                                anchor_id: anchor.anchor_id,
                                chapter_min: anchor.chapter_min,
                                chapter_max: anchor.chapter_max,
                                story_time_label: anchor.story_time_label,
                              })} style={{ marginTop: 2, accentColor: 'var(--accent)' }} />
                              <div>
                                <div style={{ fontFamily: 'var(--font-serif)', fontSize: 13, color: isSel ? 'var(--accent)' : 'var(--text)' }}>{anchor.story_time_label}</div>
                                {anchor.sample_summary && <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 2, lineHeight: 1.5 }}>{anchor.sample_summary}</div>}
                              </div>
                              <span style={{ fontSize: 10.5, color: 'var(--muted-2)', whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)' }}>
                                {anchor.chapter_max !== anchor.chapter_min
                                  ? t('mobile.new_game.birthpoint.chapter_range', { min: anchor.chapter_min, max: anchor.chapter_max })
                                  : t('mobile.new_game.birthpoint.chapter_single', { n: anchor.chapter_min })}
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ================================================================
   STEP 1 — 角色卡
   ================================================================ */
function StepRole({ personas, userCards, roleMode, setRoleMode, pickedCard, setPickedCard, newCardName, setNewCardName, newCardRole, setNewCardRole, newCardBg, setNewCardBg }) {
  const { t } = useTranslation();
  const allOpts = [
    ...personas.map(p => ({ key: `persona:${p.id || p.slug}`, kind: 'persona', name: p.name || t('mobile.new_game.role.unnamed'), subtitle: p.role || t('mobile.new_game.role.kind_persona'), id: p.id, slug: p.slug, pinned: !!p.is_default })),
    ...userCards.map(c => ({ key: `user:${c.id || c.slug}`, kind: 'user_card', name: c.name || t('mobile.new_game.role.unnamed'), subtitle: c.identity || c.role || t('mobile.new_game.role.kind_card'), id: c.id, slug: c.slug, pinned: false })),
  ];

  return (
    <div style={{ display: 'grid', gap: 20 }}>
      {/* 模式切换 */}
      <div>
        <FieldLabel>{t('mobile.new_game.role.source_label')}</FieldLabel>
        <div className="pl-seg2" style={{ marginBottom: 16 }}>
          <button className={roleMode === 'existing' ? 'active' : ''} disabled={allOpts.length === 0} onClick={() => setRoleMode('existing')}>
            {t('mobile.new_game.role.pick_existing')}
          </button>
          <button className={roleMode === 'new' ? 'active' : ''} onClick={() => setRoleMode('new')}>
            {t('mobile.new_game.role.create_new')}
          </button>
        </div>
      </div>

      {/* 现有卡列表 */}
      {roleMode === 'existing' && (
        allOpts.length === 0 ? (
          <div style={{ fontSize: 12.5, color: 'var(--muted)', padding: '10px 0', lineHeight: 1.6 }}>
            {t('mobile.new_game.role.existing_empty')}
          </div>
        ) : (
          <div style={{ display: 'grid', gap: 7 }}>
            {allOpts.map(opt => {
              const sel = pickedCard === opt.key;
              return (
                <button
                  key={opt.key}
                  onClick={() => setPickedCard(opt.key)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 12, padding: '12px 13px',
                    border: sel ? '1px solid var(--accent-edge)' : '1px solid var(--line-soft)',
                    borderRadius: 12, background: sel ? 'var(--accent-soft)' : 'var(--panel)',
                    textAlign: 'left', transition: 'border-color .12s, background .12s', width: '100%',
                  }}
                >
                  <div style={{
                    width: 38, height: 38, borderRadius: 11, flexShrink: 0,
                    display: 'grid', placeItems: 'center',
                    background: sel ? 'var(--accent)' : 'var(--panel-3)',
                    border: '1px solid var(--line)',
                    fontFamily: 'var(--font-serif)', fontSize: 17,
                    color: sel ? '#fff8f3' : 'var(--text)',
                  }}>
                    {opt.name.slice(0, 1)}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                      <span style={{ fontSize: 14, fontWeight: 500, color: sel ? 'var(--accent)' : 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{opt.name}</span>
                      {opt.pinned && <span className="pill accent" style={{ fontSize: 10 }}>{t('mobile.new_game.role.default_badge')}</span>}
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 2 }}>
                      {opt.subtitle} · {opt.kind === 'persona' ? t('mobile.new_game.role.kind_persona') : t('mobile.new_game.role.kind_card')}
                    </div>
                  </div>
                  {sel && <Icon name="check" size={15} style={{ color: 'var(--accent)', flexShrink: 0 }} />}
                </button>
              );
            })}
          </div>
        )
      )}

      {/* 新建角色 */}
      {roleMode === 'new' && (
        <div style={{ display: 'grid', gap: 14 }}>
          <div className="pl-field">
            <label>{t('mobile.new_game.role.new_name_label')} <span style={{ color: 'var(--danger)' }}>*</span></label>
            <input
              className="pl-input"
              placeholder={t('mobile.new_game.role.new_name_placeholder')}
              value={newCardName}
              onChange={e => setNewCardName(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="pl-field">
            <label>{t('mobile.new_game.role.new_role_label')}</label>
            <input
              className="pl-input"
              placeholder={t('mobile.new_game.role.new_role_placeholder')}
              value={newCardRole}
              onChange={e => setNewCardRole(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="pl-field">
            <label>{t('mobile.new_game.role.new_bg_label')}</label>
            <textarea
              className="pl-input"
              placeholder={t('mobile.new_game.role.new_bg_placeholder')}
              value={newCardBg}
              onChange={e => setNewCardBg(e.target.value)}
              rows={3}
            />
          </div>
        </div>
      )}
    </div>
  );
}

/* ================================================================
   STEP 2 — 出身与身份
   ================================================================ */
function StepIdentity({ scriptId, birthpoint, pickedCard, allRoleOptions, playerOrigin, setPlayerOrigin, identity, setIdentity, identityKnown, setIdentityKnown }) {
  const { t } = useTranslation();
  // 允许的身份来源
  const allowedSources = ALLOWED_SOURCES[playerOrigin] || ['none', 'npc', 'ai', 'manual'];

  // 当前选中的来源
  const srcOf = id => !id ? 'none' : (id._from === 'npc_card' ? 'npc' : id._from === 'ai' ? 'ai' : 'manual');
  const [idSrc, setIdSrc] = useState(() => srcOf(identity));

  // NPC 卡列表(当 idSrc === 'npc' 时加载)
  const [npcCards, setNpcCards] = useState([]);
  const [npcLoading, setNpcLoading] = useState(false);
  // AI 推荐
  const [recs, setRecs] = useState([]);
  const [recsLoading, setRecsLoading] = useState(false);
  const [recsErr, setRecsErr] = useState('');
  // 手动填写
  const [manualName, setManualName] = useState('');
  const [manualRole, setManualRole] = useState('');
  const [manualBg, setManualBg] = useState('');

  // 出身变化时校验来源兼容性
  useEffect(() => {
    const allowed = ALLOWED_SOURCES[playerOrigin] || ['none', 'npc', 'ai', 'manual'];
    if (!allowed.includes(idSrc)) {
      setIdSrc(allowed[0]);
      setIdentity(null);
    } else if (identity && identity.player_origin !== playerOrigin) {
      setIdentity({ ...identity, player_origin: playerOrigin });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playerOrigin]);

  // identity 从外部更新时同步 tab
  useEffect(() => {
    if (identity) setIdSrc(srcOf(identity));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity ? `${identity._from || ''}:${identity.npc_card_id || ''}:${identity.name || ''}` : null]);

  const allowedNow = ALLOWED_SOURCES[playerOrigin] || ['none', 'npc', 'ai', 'manual'];

  // 加载 NPC 卡
  useEffect(() => {
    if (idSrc !== 'npc' || !scriptId) { setNpcCards([]); return; }
    let alive = true;
    setNpcLoading(true);
    (async () => {
      try {
        const r = await window.api.cards.scriptList(parseInt(scriptId, 10));
        const list = (r && (r.items || r.cards)) || (Array.isArray(r) ? r : []);
        if (alive) setNpcCards(Array.isArray(list) ? list : []);
      } catch (_) { if (alive) setNpcCards([]); }
      if (alive) setNpcLoading(false);
    })();
    return () => { alive = false; };
  }, [idSrc, scriptId]);

  const pickRec = rec => setIdentity({ name: rec.name || '', role: rec.role || '', background: rec.background || '', source: 'ai', _from: 'ai', player_origin: playerOrigin });
  const pickNpc = card => {
    const nm = card.name || card.title || '';
    const role = card.identity || card.role || card.archetype || '';
    const bg = card.background || card.persona || card.summary || card.description || card.bio || '';
    setIdentity({ name: nm, role, background: bg, source: 'npc_card', _from: 'npc_card', npc_card_id: card.id || card.slug || null, player_origin: playerOrigin });
    setIdentityKnown(false);
  };
  const applyManual = () => {
    const role = manualRole.trim(); const bg = manualBg.trim();
    if (!role && !bg) return;
    setIdentity({ name: manualName.trim(), role, background: bg, source: 'custom', _from: 'custom', player_origin: playerOrigin });
  };
  const clearIdentity = () => {
    setIdentity(null);
    setIdSrc('none');
  };
  const chooseSource = sid => {
    setIdSrc(sid);
    if (sid === 'none') clearIdentity();
  };

  const fetchAiRecs = useCallback(async () => {
    if (!scriptId) return;
    setRecsLoading(true); setRecsErr(''); setRecs([]);
    const pickedRole = allRoleOptions ? allRoleOptions.find(o => o.key === pickedCard) : null;
    try {
      const r = await fetch(`${window.__API_BASE || ''}/api/scripts/${parseInt(scriptId, 10)}/recommend-identity`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          birthpoint_phase: birthpoint?.phase_label || '',
          birthpoint_label: birthpoint?.story_time_label || '',
          character_card_id: pickedRole ? (pickedRole.id || null) : null,
          character_card_kind: pickedRole ? pickedRole.kind : null,
          player_origin: playerOrigin,
          n: 4,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || data.ok === false) {
        setRecsErr((data && data.error) || t('mobile.new_game.identity.ai_request_failed', { status: r.status }));
      } else if (data && Array.isArray(data.recommendations) && data.recommendations.length > 0) {
        setRecs(data.recommendations);
      } else {
        setRecsErr(t('mobile.new_game.identity.ai_empty'));
      }
    } catch (e) { setRecsErr(String(e?.message || e)); }
    setRecsLoading(false);
  }, [scriptId, birthpoint, pickedCard, allRoleOptions, playerOrigin]);

  return (
    <div style={{ display: 'grid', gap: 22 }}>

      {/* ── 出身来源 ── */}
      <div>
        <FieldLabel hint={t('mobile.new_game.identity.origin_hint')}>{t('mobile.new_game.identity.origin_step')}</FieldLabel>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {ORIGIN_OPTIONS.map(orig => {
            const sel = playerOrigin === orig.value;
            return (
              <button
                key={orig.value}
                onClick={() => setPlayerOrigin(orig.value)}
                style={{
                  textAlign: 'left', padding: '11px 12px', borderRadius: 10, cursor: 'pointer',
                  border: sel ? `1px solid ${orig.accentBorder}` : '1px solid var(--line-soft)',
                  background: sel ? orig.accentBg : 'var(--panel)',
                  display: 'grid', gap: 5, transition: 'border-color .12s, background .12s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <span style={{ fontSize: 17, lineHeight: 1, flexShrink: 0, color: sel ? orig.accentColor : 'var(--muted-2)', fontFamily: 'var(--font-serif)' }}>{orig.icon}</span>
                  <span style={{ fontFamily: 'var(--font-serif)', fontSize: 13.5, fontWeight: 700, color: sel ? orig.accentColor : 'var(--text)', lineHeight: 1.2 }}>{t(orig.labelKey)}</span>
                </div>
                <span style={{ fontSize: 11, fontWeight: 600, color: sel ? orig.accentColor : 'var(--muted)', lineHeight: 1.3 }}>{t(orig.essenceKey)}</span>
                <span style={{ fontSize: 10.5, color: 'var(--muted-2)', lineHeight: 1.5 }}>{t(orig.mappingKey)}</span>
                {sel && <span style={{ fontSize: 10.5, color: 'var(--muted)', lineHeight: 1.5, borderTop: `1px solid ${orig.accentBorder}`, paddingTop: 5, marginTop: 2 }}>{t(orig.hintKey)}</span>}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── 身份来源 ── */}
      <div>
        <FieldLabel hint={t('mobile.new_game.identity.src_hint')}>{t('mobile.new_game.identity.src_step')}</FieldLabel>

        {/* 来源选择器 */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
          {[
            ['none', t('mobile.new_game.identity.src_none')],
            ['npc', t('mobile.new_game.identity.src_npc')],
            ['ai', t('mobile.new_game.identity.src_ai')],
            ['manual', t('mobile.new_game.identity.src_manual')],
          ].filter(([sid]) => allowedNow.includes(sid)).map(([sid, lbl]) => {
            const sel = idSrc === sid;
            return (
              <button
                key={sid}
                onClick={() => chooseSource(sid)}
                style={{
                  padding: '7px 14px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
                  border: sel ? '1px solid var(--accent-edge)' : '1px solid var(--line-soft)',
                  background: sel ? 'var(--accent-soft)' : 'var(--panel)',
                  color: sel ? 'var(--accent)' : 'var(--text)', transition: 'all .12s',
                }}
              >
                {lbl}
              </button>
            );
          })}
        </div>

        {/* 已选预览 */}
        {identity && (
          <div style={{
            display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10,
            padding: '11px 13px', border: '1px solid var(--accent-edge)', borderRadius: 11,
            background: 'var(--accent-soft)', marginBottom: 12,
          }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center', marginBottom: 3 }}>
                <span className="pill accent" style={{ fontSize: 10 }}>
                  {identity._from === 'ai' ? 'AI' : identity._from === 'npc_card' ? 'NPC' : t('mobile.new_game.identity.badge_manual')}
                </span>
                {identity.name && <strong style={{ fontFamily: 'var(--font-serif)', fontSize: 14, color: 'var(--text)' }}>{identity.name}</strong>}
                {identity.role && <span style={{ fontSize: 12.5, color: 'var(--text-quiet)' }}>{identity.role}</span>}
              </div>
              {identity.background && <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6 }}>{identity.background}</div>}
            </div>
            <button onClick={() => chooseSource('none')} style={{ flexShrink: 0, fontSize: 12, color: 'var(--muted-2)', padding: '2px 6px' }}>{t('mobile.new_game.identity.clear')}</button>
          </div>
        )}

        {/* 从原著角色 */}
        {idSrc === 'npc' && (
          npcLoading ? <Loading text={t('mobile.new_game.identity.npc_loading')} /> :
          npcCards.length === 0 ? (
            <div style={{ fontSize: 12.5, color: 'var(--muted)', padding: '8px 0' }}>{t('mobile.new_game.identity.npc_empty')}</div>
          ) : (
            <div style={{ display: 'grid', gap: 6 }}>
              {npcCards.map((card, i) => {
                const cid = card.id || card.slug || i;
                const isSel = identity && identity._from === 'npc_card' && String(identity.npc_card_id) === String(card.id || card.slug);
                const nm = card.name || card.title || '';
                const role = card.identity || card.role || card.archetype || '';
                const bg = card.background || card.persona || card.summary || card.description || card.bio || '';
                return (
                  <button key={cid} onClick={() => pickNpc(card)} style={{
                    textAlign: 'left', padding: '11px 13px', borderRadius: 11,
                    border: isSel ? '1px solid var(--accent-edge)' : '1px solid var(--line-soft)',
                    background: isSel ? 'var(--accent-soft)' : 'var(--panel)',
                    display: 'grid', gap: 4, transition: 'border-color .12s, background .12s',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      {nm && <strong style={{ fontFamily: 'var(--font-serif)', fontSize: 14 }}>{nm}</strong>}
                      {role && <span className="pill" style={{ fontSize: 10.5 }}>{role}</span>}
                      {isSel && <span className="pill accent" style={{ fontSize: 10, marginLeft: 'auto' }}>{t('mobile.new_game.identity.selected_badge')}</span>}
                    </div>
                    {bg && <span style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.55, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{bg}</span>}
                  </button>
                );
              })}
            </div>
          )
        )}

        {/* AI 生成 */}
        {idSrc === 'ai' && (
          <div style={{ display: 'grid', gap: 10 }}>
            <button className="pl-btn-ghost" onClick={fetchAiRecs} disabled={recsLoading} style={{ height: 40, fontSize: 13 }}>
              {recsLoading ? <><Icon name="spinner" size={13} className="spin" /> {t('mobile.new_game.identity.ai_generating')}</> : recs.length > 0 ? t('mobile.new_game.identity.ai_regenerate') : t('mobile.new_game.identity.ai_generate_btn')}
            </button>
            <ErrBar msg={recsErr} />
            {recs.length > 0 && (
              <div style={{ display: 'grid', gap: 6 }}>
                {recs.map((rec, i) => {
                  const isSel = identity && identity._from === 'ai' && identity.name === rec.name && identity.role === rec.role;
                  return (
                    <button key={i} onClick={() => pickRec(rec)} style={{
                      textAlign: 'left', padding: '11px 13px', borderRadius: 11,
                      border: isSel ? '1px solid var(--accent-edge)' : '1px solid var(--line-soft)',
                      background: isSel ? 'var(--accent-soft)' : 'var(--panel)',
                      display: 'grid', gap: 4, transition: 'border-color .12s, background .12s',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        {rec.name && <strong style={{ fontFamily: 'var(--font-serif)', fontSize: 14 }}>{rec.name}</strong>}
                        {rec.role && <span className="pill" style={{ fontSize: 10.5 }}>{rec.role}</span>}
                        {isSel && <span className="pill accent" style={{ fontSize: 10, marginLeft: 'auto' }}>{t('mobile.new_game.identity.selected_badge')}</span>}
                      </div>
                      {rec.background && <span style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.55 }}>{rec.background}</span>}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* 手动填写 */}
        {idSrc === 'manual' && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div className="pl-field" style={{ marginBottom: 0 }}>
              <label>{t('mobile.new_game.identity.manual_alias_label')}</label>
              <input className="pl-input" placeholder={t('mobile.new_game.identity.manual_alias_placeholder')} value={manualName} onChange={e => setManualName(e.target.value)} />
            </div>
            <div className="pl-field" style={{ marginBottom: 0 }}>
              <label>{t('mobile.new_game.identity.manual_role_label')} <span style={{ color: 'var(--danger)' }}>*</span></label>
              <input className="pl-input" placeholder={t('mobile.new_game.identity.manual_role_placeholder')} value={manualRole} onChange={e => setManualRole(e.target.value)} />
            </div>
            <div className="pl-field" style={{ marginBottom: 0 }}>
              <label>{t('mobile.new_game.identity.manual_bg_label')}</label>
              <textarea className="pl-input" rows={3} placeholder={t('mobile.new_game.identity.manual_bg_placeholder')} value={manualBg} onChange={e => setManualBg(e.target.value)} />
            </div>
            <button className="pl-btn-primary" onClick={applyManual} disabled={!manualRole.trim() && !manualBg.trim()} style={{ height: 42, fontSize: 13 }}>
              <Icon name="check" size={14} /> {t('mobile.new_game.identity.manual_confirm_btn')}
            </button>
          </div>
        )}
      </div>

      {/* ── 是否知道这个身份 ── */}
      {identity && playerOrigin !== 'body' && (
        <div>
          <FieldLabel hint={t('mobile.new_game.identity.known_hint')}>{t('mobile.new_game.identity.known_step')}</FieldLabel>
          <div style={{ display: 'flex', gap: 8 }}>
            {[
              { val: true, label: t('mobile.new_game.identity.known_yes'), desc: t('mobile.new_game.identity.known_yes_desc') },
              { val: false, label: t('mobile.new_game.identity.known_no'), desc: t('mobile.new_game.identity.known_no_desc') },
            ].map(({ val, label, desc }) => {
              const sel = identityKnown === val;
              return (
                <button key={String(val)} onClick={() => setIdentityKnown(val)} style={{
                  flex: '1 1 0', textAlign: 'left', padding: '10px 12px', cursor: 'pointer',
                  border: sel ? '1px solid var(--accent-edge)' : '1px solid var(--line-soft)',
                  borderRadius: 10, background: sel ? 'var(--accent-soft)' : 'var(--panel)',
                  display: 'grid', gap: 3, transition: 'border-color .12s, background .12s',
                }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: sel ? 'var(--accent)' : 'var(--text)' }}>{label}</span>
                  <span style={{ fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.5 }}>{desc}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/* ================================================================
   STEP 3 — 引导与防剧透 + 故事意图
   ================================================================ */
function StepMeta({ foreknowledge, setForeknowledge, npcAwareness, setNpcAwareness, steering, setSteering, spoiler, setSpoiler, storyIntent, setStoryIntent }) {
  const { t } = useTranslation();
  const segOpts = (opts, cur, set) => (
    <div className="pl-seg2">
      {opts.map(([v, lbl]) => (
        <button key={v} className={cur === v ? 'active' : ''} onClick={() => set(v)}>{lbl}</button>
      ))}
    </div>
  );

  return (
    <div style={{ display: 'grid', gap: 22 }}>
      {/* 元知识 */}
      <div>
        <FieldLabel hint={t('mobile.new_game.meta.foreknowledge_hint')}>{t('mobile.new_game.meta.foreknowledge_label')}</FieldLabel>
        {segOpts([
          ['none', t('mobile.new_game.meta.foreknowledge_none')],
          ['partial', t('mobile.new_game.meta.foreknowledge_partial')],
          ['omniscient', t('mobile.new_game.meta.foreknowledge_omniscient')],
        ], foreknowledge, setForeknowledge)}
      </div>

      {/* NPC 起疑 */}
      <div>
        <FieldLabel hint={t('mobile.new_game.meta.npc_hint')}>{t('mobile.new_game.meta.npc_label')}</FieldLabel>
        {segOpts([
          ['oblivious', t('mobile.new_game.meta.npc_oblivious')],
          ['suspicious', t('mobile.new_game.meta.npc_suspicious')],
        ], npcAwareness, setNpcAwareness)}
      </div>

      {/* 引导强度 */}
      <div>
        <FieldLabel hint={t('mobile.new_game.meta.steering_hint')}>{t('mobile.new_game.meta.steering_label')}</FieldLabel>
        {segOpts([
          ['rail', t('mobile.new_game.meta.steering_rail')],
          ['guided', t('mobile.new_game.meta.steering_guided')],
          ['free', t('mobile.new_game.meta.steering_free')],
        ], steering, setSteering)}
      </div>

      {/* 防剧透 */}
      <div>
        <FieldLabel hint={t('mobile.new_game.meta.spoiler_hint')}>{t('mobile.new_game.meta.spoiler_label')}</FieldLabel>
        {segOpts([
          ['strict', t('mobile.new_game.meta.spoiler_strict')],
          ['loose', t('mobile.new_game.meta.spoiler_loose')],
        ], spoiler, setSpoiler)}
      </div>

      {/* 故事意图 */}
      <div>
        <FieldLabel hint={t('mobile.new_game.meta.intent_hint')}>{t('mobile.new_game.meta.intent_label')}</FieldLabel>
        <textarea
          className="pl-input"
          rows={4}
          value={storyIntent}
          onChange={e => setStoryIntent(e.target.value)}
          placeholder={t('mobile.new_game.meta.intent_placeholder')}
        />
      </div>
    </div>
  );
}

/* ================================================================
   STEP 4 — 确认
   ================================================================ */
function StepConfirm({ title, setTitle, scripts, scriptId, birthpoint, roleMode, pickedCard, newCardName, allRoleOptions, playerOrigin, identity, foreknowledge, npcAwareness, steering, spoiler, submitErr, submitting }) {
  const { t } = useTranslation();
  const selScript = scripts.find(s => String(s.id) === String(scriptId)) || null;
  const pickedOpt = allRoleOptions.find(o => o.key === pickedCard);
  const roleName = roleMode === 'new' ? (newCardName.trim() || t('mobile.new_game.confirm.new_role_fallback')) : (pickedOpt?.name || '—');
  const origLabel = (() => { const o = ORIGIN_OPTIONS.find(x => x.value === playerOrigin); return o ? t(o.labelKey) : playerOrigin; })();

  const rows = [
    { k: t('mobile.new_game.confirm.row_save_name'), v: title.trim() || '—', highlight: !title.trim() },
    { k: t('mobile.new_game.confirm.row_script'), v: selScript?.title || '—', highlight: !selScript },
    { k: t('mobile.new_game.confirm.row_birthpoint'), v: birthpoint?.story_time_label || t('mobile.new_game.confirm.from_start') },
    { k: t('mobile.new_game.confirm.row_role'), v: roleName, highlight: !roleName || roleName === '—' },
    { k: t('mobile.new_game.confirm.row_origin'), v: origLabel },
    { k: t('mobile.new_game.confirm.row_identity'), v: identity ? `${identity.name || ''} ${identity.role || ''}`.trim() || t('mobile.new_game.confirm.identity_set') : t('mobile.new_game.confirm.identity_none') },
    { k: t('mobile.new_game.confirm.row_foreknowledge'), v: { none: t('mobile.new_game.meta.foreknowledge_none'), partial: t('mobile.new_game.meta.foreknowledge_partial'), omniscient: t('mobile.new_game.meta.foreknowledge_omniscient') }[foreknowledge] || foreknowledge },
    { k: t('mobile.new_game.confirm.row_steering'), v: { rail: t('mobile.new_game.meta.steering_rail'), guided: t('mobile.new_game.meta.steering_guided'), free: t('mobile.new_game.meta.steering_free') }[steering] || steering },
    { k: t('mobile.new_game.confirm.row_spoiler'), v: { strict: t('mobile.new_game.meta.spoiler_strict'), loose: t('mobile.new_game.meta.spoiler_loose') }[spoiler] || spoiler },
  ];

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="pl-field">
        <label>{t('mobile.new_game.confirm.save_name_label')} <span style={{ color: 'var(--danger)' }}>*</span></label>
        <input
          className="pl-input"
          value={title}
          onChange={e => setTitle(e.target.value)}
          placeholder={t('mobile.new_game.confirm.save_name_placeholder')}
          autoFocus
        />
      </div>

      <div style={{ border: '1px solid var(--line-soft)', borderRadius: 12, overflow: 'hidden' }}>
        {rows.map((row, i) => (
          <div key={row.k} style={{
            display: 'grid', gridTemplateColumns: '80px 1fr', gap: 12, alignItems: 'baseline',
            padding: '10px 13px', borderTop: i > 0 ? '1px solid var(--line-soft)' : 'none',
          }}>
            <span style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '.1em', color: 'var(--muted-2)' }}>{row.k}</span>
            <span style={{ fontSize: 13.5, color: row.highlight ? 'var(--danger)' : 'var(--text)', fontFamily: 'var(--font-serif)' }}>{row.v}</span>
          </div>
        ))}
      </div>

      <ErrBar msg={submitErr} />

      {submitting && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--muted)', justifyContent: 'center', padding: '6px 0' }}>
          <Icon name="spinner" size={13} className="spin" /> {t('mobile.new_game.confirm.creating')}
        </div>
      )}
    </div>
  );
}

/* ================================================================
   主组件
   ================================================================ */
export function MobileNewGame({ nav, scriptId: propScriptId, onDone }) {
  const { t } = useTranslation();
  const lockedScriptId = propScriptId ? String(propScriptId) : null;

  // ── 数据加载 ──
  const [scripts, setScripts] = useState([]);
  const [personas, setPersonas] = useState([]);
  const [userCards, setUserCards] = useState([]);
  const [dataLoading, setDataLoading] = useState(true);
  const [dataErr, setDataErr] = useState('');

  // ── Step 0 state ──
  const [scriptId, setScriptId] = useState(lockedScriptId || '');
  const [birthpoint, setBirthpoint] = useState(null);

  // ── Step 1 state ──
  const [roleMode, setRoleMode] = useState('existing');
  const [pickedCard, setPickedCard] = useState('');
  const [newCardName, setNewCardName] = useState('');
  const [newCardRole, setNewCardRole] = useState('');
  const [newCardBg, setNewCardBg] = useState('');

  // ── Step 2 state ──
  const [playerOrigin, setPlayerOrigin] = useState('soul');
  const [identity, setIdentity] = useState(null);
  const [identityKnown, setIdentityKnown] = useState(true);

  // ── Step 3 state ──
  const [foreknowledge, setForeknowledge] = useState('none');
  const [npcAwareness, setNpcAwareness] = useState('oblivious');
  const [steering, setSteering] = useState('guided');
  const [spoiler, setSpoiler] = useState('loose');
  const [storyIntent, setStoryIntent] = useState('');

  // ── Step 4 state ──
  const [title, setTitle] = useState('');

  // ── 向导控制 ──
  const [step, setStep] = useState(0);
  const [submitErr, setSubmitErr] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // ── 草稿恢复 ──
  const DRAFT_KEY = 'mobile_newgame.draft.v1';
  const draftReadyRef = useRef(false);

  // ── 加载数据 ──
  useEffect(() => {
    draftReadyRef.current = false;
    setDataLoading(true); setDataErr('');
    (async () => {
      let scList = []; let psList = []; let ucList = [];
      try { const r = await window.api.scripts.list(); scList = Array.isArray(r) ? r : (r?.items || r?.scripts || []); } catch (_) {}
      try { const p = await window.api.account.personas.list(); psList = (p && (p.items || p.personas)) || []; } catch (_) {}
      try { const c = await window.api.cards.myList(); ucList = (c && (c.items || c.cards)) || []; } catch (_) {}
      setScripts(scList);
      setPersonas(psList);
      setUserCards(ucList);

      // 默认剧本
      if (!lockedScriptId) {
        let pickId = lsGet('newgame.lastScriptId') || '';
        if (!pickId || !scList.some(x => String(x.id) === pickId && !scriptBlockReason(x))) {
          const first = scList.find(x => !scriptBlockReason(x));
          pickId = first ? String(first.id) : (scList.length ? String(scList[0].id) : '');
        }
        setScriptId(pickId);
        // 默认存档名
        const sc = scList.find(x => String(x.id) === pickId);
        const scTitle = (sc && (sc.title || '').replace(/^《|》$/g, '')) || '';
        setTitle(scTitle ? `${scTitle} ${t('mobile.new_game.default_save_suffix')}` : '');
      } else {
        const sc = scList.find(x => String(x.id) === lockedScriptId);
        const scTitle = (sc && (sc.title || '').replace(/^《|》$/g, '')) || '';
        setTitle(scTitle ? `${scTitle} ${t('mobile.new_game.default_save_suffix')}` : '');
      }

      // 默认角色
      if (psList.length) { setRoleMode('existing'); setPickedCard(`persona:${psList[0].id || psList[0].slug}`); }
      else if (ucList.length) { setRoleMode('existing'); setPickedCard(`user:${ucList[0].id || ucList[0].slug}`); }
      else { setRoleMode('new'); setPickedCard(''); }

      // 草稿恢复
      try {
        const draft = lsGetJSON(DRAFT_KEY, null);
        if (draft && typeof draft === 'object') {
          const sameScript = !lockedScriptId || String(draft.scriptId) === lockedScriptId;
          if (sameScript) {
            if (typeof draft.title === 'string') setTitle(draft.title);
            if (draft.scriptId && scList.some(x => String(x.id) === String(draft.scriptId))) setScriptId(String(draft.scriptId));
            if (draft.roleMode) setRoleMode(draft.roleMode);
            if (typeof draft.pickedCard === 'string') setPickedCard(draft.pickedCard);
            if (typeof draft.newCardName === 'string') setNewCardName(draft.newCardName);
            if (typeof draft.newCardRole === 'string') setNewCardRole(draft.newCardRole);
            if (typeof draft.newCardBg === 'string') setNewCardBg(draft.newCardBg);
            if ('birthpoint' in draft) setBirthpoint(draft.birthpoint);
            if (draft.playerOrigin) setPlayerOrigin(draft.playerOrigin);
            if ('identity' in draft) setIdentity(draft.identity);
            if ('identityKnown' in draft) setIdentityKnown(draft.identityKnown);
            if (draft.foreknowledge) setForeknowledge(draft.foreknowledge);
            if (draft.npcAwareness) setNpcAwareness(draft.npcAwareness);
            if (draft.steering) setSteering(draft.steering);
            if (draft.spoiler) setSpoiler(draft.spoiler);
            if (typeof draft.storyIntent === 'string') setStoryIntent(draft.storyIntent);
            if (typeof draft.step === 'number' && draft.step < TOTAL_STEPS) setStep(draft.step);
          }
        }
      } catch (_) {}

      setDataLoading(false);
      draftReadyRef.current = true;
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 草稿回写
  useEffect(() => {
    if (!draftReadyRef.current) return;
    lsSetJSON(DRAFT_KEY, {
      scriptId, title, roleMode, pickedCard, newCardName, newCardRole, newCardBg,
      birthpoint, playerOrigin, identity, identityKnown,
      foreknowledge, npcAwareness, steering, spoiler, storyIntent, step,
    });
  }, [scriptId, title, roleMode, pickedCard, newCardName, newCardRole, newCardBg,
      birthpoint, playerOrigin, identity, identityKnown,
      foreknowledge, npcAwareness, steering, spoiler, storyIntent, step]);

  // ── 各步骤校验 ──
  const allRoleOptions = [
    ...personas.map(p => ({ key: `persona:${p.id || p.slug}`, kind: 'persona', id: p.id || null, slug: p.slug || '', name: p.name || t('mobile.new_game.role.unnamed'), subtitle: p.role || t('mobile.new_game.role.kind_persona'), pinned: !!p.is_default })),
    ...userCards.map(c => ({ key: `user:${c.id || c.slug}`, kind: 'user_card', id: c.id || null, slug: c.slug || '', name: c.name || t('mobile.new_game.role.unnamed'), subtitle: c.identity || c.role || t('mobile.new_game.role.kind_card'), pinned: false })),
  ];

  const selScript = scripts.find(s => String(s.id) === String(scriptId)) || null;
  const step0Valid = !!scriptId && !scriptBlockReason(selScript);
  const step1Valid = (roleMode === 'existing' && !!pickedCard) || (roleMode === 'new' && !!newCardName.trim());
  const step2Valid = true; // 身份是可选项
  const step3Valid = true; // meta 都有默认值
  const step4Valid = !!title.trim() && step0Valid && step1Valid;

  const canNext = [step0Valid, step1Valid, step2Valid, step3Valid][step] ?? true;

  // ── 提交 ──
  const handleCreate = async () => {
    setSubmitErr(''); setSubmitting(true);
    try {
      // 有效性最终检查
      const sc = scripts.find(s => String(s.id) === String(scriptId));
      const blockRsn = scriptBlockReason(sc);
      if (blockRsn) throw new Error(blockRsn);

      // 有活跃 job 时再 check 一次
      const activeJob = scriptId ? await window.api.scripts.activeJob(parseInt(scriptId, 10)).catch(() => null) : null;
      if (activeJob) {
        const ajStatus = String(activeJob?.status || activeJob?.active_job?.status || '').toLowerCase();
        if (ajStatus && NEWGAME_ACTIVE_IMPORT_STATUSES.has(ajStatus) && !NEWGAME_IMPORT_TERMINAL_STATUSES.has(ajStatus)) {
          throw new Error(t('mobile.new_game.script_block.importing_retry'));
        }
      }

      // 新建角色卡
      let charId = null; let charKind = null;
      let finalRoleMode = roleMode;
      if (roleMode === 'existing') {
        const opt = allRoleOptions.find(o => o.key === pickedCard);
        charId = opt ? (opt.id || opt.slug || null) : null;
        charKind = opt ? opt.kind : null;
      } else {
        const r = await window.api.cards.myUpsert({
          name: newCardName.trim(),
          identity: newCardRole.trim() || undefined,
          background: newCardBg.trim() || undefined,
          kind: 'user',
        });
        const created = r && r.card;
        if (!created || !(created.id || created.slug)) throw new Error(t('mobile.new_game.role.create_failed'));
        charId = created.id || created.slug;
        charKind = 'user_card';
        finalRoleMode = 'existing';
      }

      const payload = {
        title: title.trim(),
        script_id: parseInt(scriptId, 10),
        character_id: charId,
        character_kind: charKind,
        new_card: null,
        role_mode: finalRoleMode,
        birthpoint: birthpoint || null,
        identity: identity ? {
          name: identity.name || '',
          role: identity.role || '',
          background: identity.background || '',
          source: identity.source || 'custom',
        } : null,
        story_intent: storyIntent.trim() || null,
        player_origin: playerOrigin || 'soul',
        ...(identity && playerOrigin !== 'body' ? { identity_known: identityKnown } : {}),
        // 注意:foreknowledge_mode/npc_awareness/steering_strength/spoiler_guard 是
        // 游戏设置字段,saves.create 的 payload 后端不消费(由 updateSettings 写入),
        // 已删除以避免后端无效字段警告。
      };

      // window.__createAndEnterSave 仅在桌面 PlatformShellCS 注册;移动外壳(MobileRoot)下它 undefined,
      // 调用即 TypeError → 移动端新游戏永远失败。改为直接 saves.create + 写设置 + nav.openGame(=launchSave 激活并跳游戏台)。
      const created = await window.api.saves.create(payload);
      if (created && created.ok === false) throw new Error(created.error || created.detail || t('mobile.new_game.create_failed'));
      const save = (created && (created.save || created)) || null;
      const newSaveId = save && save.id;
      if (newSaveId) {
        try {
          await window.api.saves.updateSettings(newSaveId, {
            foreknowledge_mode: foreknowledge,
            npc_awareness: npcAwareness,
            steering_strength: steering,
            spoiler_guard: spoiler,
          }, true);
        } catch (settingsErr) {
          // 设置写失败不阻断进入游戏,但给用户非阻塞提示。
          window.__apiToast?.(
            `游戏设置未能保存(${settingsErr?.message || '写入失败'}),进入游戏后可在设置中手动调整。`,
            { kind: 'warn', duration: 5000 }
          );
        }
      }
      lsRemove(DRAFT_KEY);
      onDone?.();
      if (newSaveId && nav.openGame) { nav.openGame(save); }   // 激活存档 + 跳转游戏台
      else { nav.pop(); }
    } catch (e) {
      const msg = e?.message || (e?.payload && (e.payload.error || e.payload.detail)) || t('mobile.new_game.create_failed');
      setSubmitErr(msg);
    }
    setSubmitting(false);
  };

  // ── 渲染 ──
  return (
    <>
      {/* 顶栏 */}
      <div className="pl-head">
        <button className="pl-back" onClick={() => step > 0 ? setStep(s => s - 1) : nav.pop()} aria-label={t('mobile.new_game.back_label')}>
          <Icon name="chevron_left" size={17} />
        </button>
        <div className="pl-head-title">
          <strong>{t(STEPS[step].titleKey)}</strong>
          <StepDots step={step} total={TOTAL_STEPS} />
        </div>
      </div>

      {/* 内容区 */}
      <div className="pl-body" style={{ paddingBottom: 100 }}>
        <div className="pl-pad">
          {dataLoading ? (
            <Loading text={t('mobile.new_game.loading_wizard')} />
          ) : dataErr ? (
            <ErrBar msg={dataErr} />
          ) : (
            <>
              {step === 0 && (
                <StepScriptBirth
                  scripts={scripts}
                  lockedScriptId={lockedScriptId}
                  scriptId={scriptId}
                  setScriptId={v => { setScriptId(v); lsSet('newgame.lastScriptId', v); }}
                  birthpoint={birthpoint}
                  setBirthpoint={setBirthpoint}
                />
              )}
              {step === 1 && (
                <StepRole
                  personas={personas}
                  userCards={userCards}
                  roleMode={roleMode}
                  setRoleMode={setRoleMode}
                  pickedCard={pickedCard}
                  setPickedCard={setPickedCard}
                  newCardName={newCardName}
                  setNewCardName={setNewCardName}
                  newCardRole={newCardRole}
                  setNewCardRole={setNewCardRole}
                  newCardBg={newCardBg}
                  setNewCardBg={setNewCardBg}
                />
              )}
              {step === 2 && (
                <StepIdentity
                  scriptId={scriptId}
                  birthpoint={birthpoint}
                  pickedCard={pickedCard}
                  allRoleOptions={allRoleOptions}
                  playerOrigin={playerOrigin}
                  setPlayerOrigin={setPlayerOrigin}
                  identity={identity}
                  setIdentity={setIdentity}
                  identityKnown={identityKnown}
                  setIdentityKnown={setIdentityKnown}
                />
              )}
              {step === 3 && (
                <StepMeta
                  foreknowledge={foreknowledge}
                  setForeknowledge={setForeknowledge}
                  npcAwareness={npcAwareness}
                  setNpcAwareness={setNpcAwareness}
                  steering={steering}
                  setSteering={setSteering}
                  spoiler={spoiler}
                  setSpoiler={setSpoiler}
                  storyIntent={storyIntent}
                  setStoryIntent={setStoryIntent}
                />
              )}
              {step === 4 && (
                <StepConfirm
                  title={title}
                  setTitle={setTitle}
                  scripts={scripts}
                  scriptId={scriptId}
                  birthpoint={birthpoint}
                  roleMode={roleMode}
                  pickedCard={pickedCard}
                  newCardName={newCardName}
                  allRoleOptions={allRoleOptions}
                  playerOrigin={playerOrigin}
                  identity={identity}
                  foreknowledge={foreknowledge}
                  npcAwareness={npcAwareness}
                  steering={steering}
                  spoiler={spoiler}
                  submitErr={submitErr}
                  submitting={submitting}
                />
              )}
            </>
          )}
        </div>
      </div>

      {/* 底部按钮栏 */}
      {!dataLoading && (
        <div style={{
          position: 'absolute', bottom: 0, left: 0, right: 0,
          padding: '12px 16px calc(var(--safe-bottom) + 12px)',
          background: 'linear-gradient(to bottom, transparent, var(--bg) 30%)',
          display: 'flex', flexDirection: 'column', gap: 8,
        }}>
          {/* 快速开始:剧本+角色就绪后,跳过可选设定(身份/元知识)直接创建 —— 对齐酒馆「直接开聊」,
              身份/元知识保持各自默认值。仅在已看过角色步骤(step≥1)且未在最后一步时出现。 */}
          {step >= 1 && step < TOTAL_STEPS - 1 && step0Valid && step1Valid && (
            <button
              className="pl-btn-ghost"
              style={{ width: '100%', fontSize: 13, opacity: submitting ? 0.45 : 1 }}
              disabled={submitting}
              onClick={handleCreate}
            >
              {submitting
                ? <><Icon name="spinner" size={14} className="spin" /> {t('mobile.new_game.nav.creating')}</>
                : <><Icon name="play" size={14} /> {t('mobile.new_game.nav.quick_start')}</>}
            </button>
          )}
          <div style={{ display: 'flex', gap: 10 }}>
          {step > 0 && (
            <button className="pl-btn-ghost" style={{ flex: 1 }} onClick={() => setStep(s => s - 1)}>
              <Icon name="chevron_left" size={15} /> {t('mobile.new_game.nav.prev')}
            </button>
          )}
          {step < TOTAL_STEPS - 1 ? (
            <button
              className="pl-btn-primary"
              style={{ flex: 2, opacity: (canNext && !submitting) ? 1 : 0.45 }}
              disabled={!canNext || dataLoading || submitting}
              onClick={() => { if (canNext) setStep(s => s + 1); }}
            >
              {t('mobile.new_game.nav.next')} <Icon name="chevron_right" size={15} />
            </button>
          ) : (
            <button
              className="pl-btn-primary"
              style={{ flex: 2, opacity: (step4Valid && !submitting) ? 1 : 0.45 }}
              disabled={!step4Valid || submitting}
              onClick={handleCreate}
            >
              {submitting ? <><Icon name="spinner" size={15} className="spin" /> {t('mobile.new_game.nav.creating')}</> : <><Icon name="play" size={15} /> {t('mobile.new_game.nav.start')}</>}
            </button>
          )}
          </div>
        </div>
      )}
    </>
  );
}

export default MobileNewGame;
