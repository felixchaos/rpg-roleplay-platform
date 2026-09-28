/* 新游戏向导 (存档名 / 剧本 → 角色卡 → 出生点 → 初始身份 → 故事意图) + 剧本就绪判定纯工具。
   从 pages/saves.jsx 拆出,JSX / props 流逐字节不变;二次拆分后 BirthpointStep/IdentityStep 见同目录同名文件。 */
import React from 'react';
import { createPortal } from 'react-dom';
import { useState as useStatePL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import { plNavigate } from '../../router.js';
import { CardSheet, CardEditFields, cardFormInit, cardFormPayload } from '../../pages/cards.jsx';
import { lsGet, lsSet, lsGetJSON, lsSetJSON, lsRemove } from '../../lib/storage.js';
import CSHeader from '@cloudscape-design/components/header';
import CSContainer from '@cloudscape-design/components/container';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSBox from '@cloudscape-design/components/box';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import CSKeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import CSSelect from '@cloudscape-design/components/select';
import CSModal from '@cloudscape-design/components/modal';
import CSInput from '@cloudscape-design/components/input';
import CSFormField from '@cloudscape-design/components/form-field';
import CSTextarea from '@cloudscape-design/components/textarea';
import CSSegmentedControl from '@cloudscape-design/components/segmented-control';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSAlert from '@cloudscape-design/components/alert';
import { BirthpointStep } from './BirthpointStep.jsx';
import { IdentityStep } from './IdentityStep.jsx';
import { ACTIVE_IMPORT_STATUSES, IMPORT_TERMINAL_STATUSES, scriptPlayBlockKind } from '../../lib/script-play-gate.js';

const NEWGAME_ACTIVE_IMPORT_STATUSES = ACTIVE_IMPORT_STATUSES;
const NEWGAME_IMPORT_TERMINAL_STATUSES = IMPORT_TERMINAL_STATUSES;

function newGameReadinessLabel(key, t) {
  return t(`scripts.my.readiness_label_${key}`, { defaultValue: key });
}

function newGameActiveJobBlockReason(payload, t) {
  const job = payload?.job || payload?.active_job || payload;
  const status = String(job?.status || payload?.status || "").trim().toLowerCase();
  if (status && NEWGAME_ACTIVE_IMPORT_STATUSES.has(status) && !NEWGAME_IMPORT_TERMINAL_STATUSES.has(status)) {
    return t('saves.new_game.script_not_ready_importing');
  }
  if (payload?.active === true && (!status || !NEWGAME_IMPORT_TERMINAL_STATUSES.has(status))) {
    return t('saves.new_game.script_not_ready_importing');
  }
  return "";
}

// 判定在 lib/script-play-gate.js(四端共用),这里只翻文案。readiness.missing 不参与拦截:
// fork 剧本没有切片、空白剧本没有切片和锚点,后端照样能开局。
function newGameScriptBlockReason(script, t) {
  const kind = scriptPlayBlockKind(script);
  if (kind === 'importing') return t('saves.new_game.script_not_ready_importing');
  if (kind === 'no_chapters') {
    return t('saves.new_game.script_not_ready_missing', { items: newGameReadinessLabel('chunks', t) });
  }
  return "";
}

/* ============================================================
   MAIN WIZARD COMPONENT
   ============================================================ */
function NewGameModal({ open, onClose, onConfirm, defaultScriptId = null }) {
  const { t } = useTranslation();
  // ── shared data ──────────────────────────────────────────────
  const [scripts, setScripts] = useStatePL([]);
  const [personas, setPersonas] = useStatePL([]);
  const [userCards, setUserCards] = useStatePL([]);
  // 本剧本 NPC 角色卡:可直接「扮演 NPC」开局(character_kind='script_card')。配合下方「本世界人」
  // 出身即「你就是这个 NPC,GM 守世界观」的闭环。后端早已支持(create_save kind='script_card')。
  const [scriptNpcCards, setScriptNpcCards] = useStatePL([]);
  const [loading, setLoading] = useStatePL(true);

  // ── Step 1 state ─────────────────────────────────────────────
  const [title, setTitle] = useStatePL("");
  const [scriptId, setScriptId] = useStatePL("");

  // ── Step 2 state ─────────────────────────────────────────────
  const [roleMode, setRoleMode] = useStatePL("existing");
  const [pickedCard, setPickedCard] = useStatePL("");
  // 新建角色卡:复用 cards.jsx 的完整字段表单(与「新建用户角色卡」对齐)
  const [newCardForm, setNewCardForm] = useStatePL(() => cardFormInit(null));
  const uNewCard = (k, v) => setNewCardForm(f => ({ ...f, [k]: v }));
  // 角色卡预览(只读 CardSheet)
  const [previewCard, setPreviewCard] = useStatePL(null);

  // ── Step 3 state ─────────────────────────────────────────────
  const [birthpoint, setBirthpoint] = useStatePL(null);

  // ── Step 4 state ─────────────────────────────────────────────
  const [identity, setIdentity] = useStatePL(null);

  // ── Step 5 state ─────────────────────────────────────────────
  const [storyIntent, setStoryIntent] = useStatePL("");

  // 玩家定位类型 (soul/body/dual/native) — 与身份卡 overlay 正交。
  // 提到 NewGameModal 顶层,IdentityStep 通过 prop 读写,
  // payload 独立带上 player_origin 字段(身份卡为 null 时也要带)。
  const [playerOrigin, setPlayerOrigin] = useStatePL('soul');
  // 是否知道身份卡 — body 时置 null(无身份卡); 其余默认 true(知道)。
  const [identityKnown, setIdentityKnown] = useStatePL(true);

  // ── submit ───────────────────────────────────────────────────
  const [submitErr, setSubmitErr] = useStatePL("");
  const [submitting, setSubmitting] = useStatePL(false);
  const [reviewGateBlocked, setReviewGateBlocked] = useStatePL(false);

  // 反馈#4:新游戏表单草稿本地持久化——填到一半切页/关弹窗回来不丢,仅"成功开始游戏"后清空。
  const NEWGAME_DRAFT_KEY = 'newgame.draft.v1';
  const draftReadyRef = React.useRef(false);  // 草稿恢复完成前不回写,避免初始 reset 把草稿覆盖
  // 失败重试去重:roleMode==='new' 时只创建一次角色卡,重试复用已创建的 id,
  // 避免每次 handleSubmit 重试都 myUpsert 落一张新卡。
  const createdCardRef = React.useRef(null);
  const clearNewgameDraft = React.useCallback(() => {
    lsRemove(NEWGAME_DRAFT_KEY);
  }, []);

  // ── load data when opened ────────────────────────────────────
  React.useEffect(() => {
    if (!open) return;
    draftReadyRef.current = false;  // 反馈#4:恢复完成前禁止回写草稿
    createdCardRef.current = null;  // 新一轮开窗清空已创建卡引用
    // reset transient state
    setTitle(""); setSubmitErr(""); setSubmitting(false); setReviewGateBlocked(false); setLoading(true); setPlayerOrigin('soul'); setIdentityKnown(true);
    setNewCardForm(cardFormInit(null)); setPreviewCard(null);
    setBirthpoint(null); setIdentity(null); setStoryIntent("");
    (async () => {
      let scList = [];
      try {
        const r = await window.api.scripts.list();
        scList = Array.isArray(r) ? r : (r?.items || r?.scripts || []);
      } catch (_) {}
      let psList = [];
      try {
        const p = await window.api.account.personas.list();
        psList = (p && (p.items || p.personas)) || [];
      } catch (_) {}
      let ucList = [];
      try {
        const c = await window.api.cards.myList();
        ucList = (c && (c.items || c.cards)) || [];
      } catch (_) {}
      setScripts(scList);
      setPersonas(psList);
      setUserCards(ucList);
      // task 108: script priority: 1) caller defaultScriptId 2) localStorage 3) first
      let pickId = "";
      if (defaultScriptId && scList.some(x => String(x.id) === String(defaultScriptId))) {
        pickId = String(defaultScriptId);
      } else {
        const remembered = lsGet("newgame.lastScriptId") || "";
        if (remembered && scList.some(x => String(x.id) === remembered && !newGameScriptBlockReason(x, t))) {
          pickId = remembered;
        } else {
          const firstPlayable = scList.find(x => !newGameScriptBlockReason(x, t));
          pickId = firstPlayable ? String(firstPlayable.id) : (scList.length ? String(scList[0].id) : "");
        }
      }
      setScriptId(pickId);
      // default character
      if (psList.length) { setRoleMode("existing"); setPickedCard(`persona:${psList[0].id || psList[0].slug}`); }
      else if (ucList.length) { setRoleMode("existing"); setPickedCard(`user:${ucList[0].id || ucList[0].slug}`); }
      else { setRoleMode("new"); setPickedCard(""); }
      // task 127: 默认存档名只用剧本名 — 角色还没选,不要预设角色名
      // (之前用 psList[0].name 但用户还没"选",误导)
      try {
        const sc = scList.find(x => String(x.id) === pickId);
        const scTitle = (sc && (sc.title || "").replace(/^《|》$/g, "")) || "";
        if (scTitle) setTitle(`${scTitle} · ${t('saves.page.new_save_suffix')}`);
        else setTitle(t('saves.new_game.page_title'));
      } catch (_) { setTitle(t('saves.new_game.page_title')); }
      // 反馈#4:在默认值之上覆盖本地草稿——无指定剧本(通用入口)或草稿剧本与本次一致时整体恢复,
      // 避免在 A 剧本开新游戏却恢复了 B 剧本的草稿。
      try {
        const draft = lsGetJSON(NEWGAME_DRAFT_KEY, null);
        const sameScript = !defaultScriptId || (draft && String(draft.scriptId) === String(defaultScriptId));
        if (draft && typeof draft === 'object' && sameScript) {
          if (typeof draft.title === 'string') setTitle(draft.title);
          if (draft.scriptId && scList.some(x => String(x.id) === String(draft.scriptId))) setScriptId(String(draft.scriptId));
          if (draft.roleMode) setRoleMode(draft.roleMode);
          if (typeof draft.pickedCard === 'string') setPickedCard(draft.pickedCard);
          if (draft.newCardForm && typeof draft.newCardForm === 'object') setNewCardForm(draft.newCardForm);
          if ('birthpoint' in draft) setBirthpoint(draft.birthpoint);
          if ('identity' in draft) setIdentity(draft.identity);
          if (draft.playerOrigin) setPlayerOrigin(draft.playerOrigin);
          if ('identityKnown' in draft) setIdentityKnown(draft.identityKnown);
          if (typeof draft.storyIntent === 'string') setStoryIntent(draft.storyIntent);
        }
      } catch (_) {}
      setLoading(false);
      draftReadyRef.current = true;  // 反馈#4:此后字段变化才回写草稿
    })();
  }, [open]);

  // 本剧本 NPC 卡:随所选剧本加载,供「选择角色 → 扮演 NPC」。换剧本即换 NPC 列表。
  React.useEffect(() => {
    if (!open || !scriptId) { setScriptNpcCards([]); return; }
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.cards.scriptList(parseInt(scriptId, 10));
        const list = Array.isArray(r) ? r : (r?.items || r?.cards || []);
        if (!cancelled) setScriptNpcCards(Array.isArray(list) ? list : []);
      } catch (_) { if (!cancelled) setScriptNpcCards([]); }
    })();
    return () => { cancelled = true; };
  }, [open, scriptId]);

  // 反馈#4:任一表单字段变化即写回草稿(恢复完成后才写,避免初始 reset/默认值覆盖已存草稿)
  React.useEffect(() => {
    if (!open || !draftReadyRef.current) return;
    lsSetJSON(NEWGAME_DRAFT_KEY, {
      title, scriptId, roleMode, pickedCard, newCardForm,
      birthpoint, identity, playerOrigin, identityKnown, storyIntent,
    });
  }, [open, title, scriptId, roleMode, pickedCard, newCardForm, birthpoint, identity, playerOrigin, identityKnown, storyIntent]);

  // 用户反馈闭环:选「本剧本 NPC」角色卡(key 前缀 npc:)= 你就是这个 NPC 本人 →
  // 出身自动强制锁「本世界人」(native),不再需要/允许手动选穿越类出身。
  // 切回普通卡时把被强制的 native 还原成默认 soul。必须放在 `if (!open) return null` 之前(Hooks 规则)。
  const isNpcPicked = typeof pickedCard === 'string' && pickedCard.startsWith('npc:');
  const prevNpcPickedRef = React.useRef(isNpcPicked);
  React.useEffect(() => {
    const prev = prevNpcPickedRef.current;
    prevNpcPickedRef.current = isNpcPicked;
    if (isNpcPicked) {
      if (playerOrigin !== 'native') setPlayerOrigin('native');
    } else if (prev && playerOrigin === 'native') {
      setPlayerOrigin('soul');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isNpcPicked]);

  if (!open) return null;

  // 「使用现有」= 用户自己的卡库(persona + 用户创建/手动迁移的 user_card)。不含本剧本 NPC。
  const allRoleOptions = [
    ...personas.map(p => ({
      key: `persona:${p.id || p.slug}`, kind: "persona", id: p.id || null, slug: p.slug || "",
      name: p.name || t('platform.menu.unnamed'), subtitle: p.role || t('saves.new_game.card_kind_persona'), pinned: !!p.is_default,
    })),
    ...userCards.map(c => ({
      key: `user:${c.id || c.slug}`, kind: "user_card", id: c.id || null, slug: c.slug || "",
      name: c.name || t('platform.menu.unnamed'), subtitle: c.identity || t('saves.new_game.card_kind_user'), pinned: false,
    })),
  ];
  // 「本剧本 NPC」= 独立分类:直接扮演原著角色(character_kind='script_card');配合自动锁「本世界人」出身。
  const scriptNpcOptions = scriptNpcCards.map(c => ({
    key: `npc:${c.id}`, kind: "script_card", id: c.id || null, slug: "",
    name: c.name || t('platform.menu.unnamed'),
    subtitle: c.identity || c.role || t('saves.new_game.card_kind_npc', { defaultValue: '本剧本 NPC' }),
    pinned: false,
  }));
  // 解析 pickedCard 用的合集(渲染分两个 tab,但查找统一走这个)。
  const allSelectableRoles = [...allRoleOptions, ...scriptNpcOptions];

  // 各必填模块完成校验(单页:不再按步骤 gating,只用于概要 + 创建按钮)
  const selectedScript = scripts.find(sc => String(sc.id) === String(scriptId)) || null;
  const scriptBlockReason = newGameScriptBlockReason(selectedScript, t);
  const step1Valid = title.trim() && scriptId && !scriptBlockReason;
  const _pickedIsNpc = typeof pickedCard === 'string' && pickedCard.startsWith('npc:');
  const step2Valid = (roleMode === "existing" && pickedCard && !_pickedIsNpc)
    || (roleMode === "script_npc" && _pickedIsNpc)
    || (roleMode === "new" && newCardForm.name.trim());
  const step3Valid = !!birthpoint;
  // 身份卡是 overlay,和玩家出身正交。用户可以只选魂穿/肉穿/双魂/原住民定位,
  // 不挂本地身份卡时直接按角色卡开局。
  const step4Valid = true;

  const handleSubmit = async () => {
    setSubmitErr(""); setReviewGateBlocked(false); setSubmitting(true);
    try {
      const selected = scripts.find(sc => String(sc.id) === String(scriptId)) || null;
      const localBlock = newGameScriptBlockReason(selected, t);
      if (localBlock) throw new Error(localBlock);
      const active = scriptId ? await window.api.scripts.activeJob(parseInt(scriptId, 10)).catch(() => null) : null;
      const liveBlock = newGameActiveJobBlockReason(active, t);
      if (liveBlock) throw new Error(liveBlock);
      // 新建角色卡:走与「新建用户角色卡」完全相同的创建路径(myUpsert),
      // 落库后当作"现有卡"使用,确保所有字段一致持久化。
      // existing(我的卡库)与 script_npc(本剧本 NPC)都是「选已有卡」,只是来源分类不同;
      // 统一从 allSelectableRoles 解析,charKind 直接取卡的 kind(user_card/persona/script_card)。
      let picked = allSelectableRoles.find(o => o.key === pickedCard);
      let charId = roleMode !== "new" && picked ? (picked.id || picked.slug || null) : null;
      let charKind = roleMode !== "new" && picked ? picked.kind : null;
      if (roleMode === "new") {
        // 失败重试去重:首次创建后把卡存进 createdCardRef,重试直接复用已落库的 id,
        // 不再重复 myUpsert 产生重复角色卡。
        let created = createdCardRef.current;
        if (!created || !(created.id || created.slug)) {
          const r = await window.api.cards.myUpsert(cardFormPayload(newCardForm));
          created = r && r.card;
          if (!created || !(created.id || created.slug)) throw new Error(t('saves.new_game.card_create_fail'));
          createdCardRef.current = created;
        }
        charId = created.id || created.slug;
        charKind = "user_card";
      }
      const payload = {
        title: title.trim(),
        script_id: parseInt(scriptId, 10),
        character_id: charId,
        character_kind: charKind,
        new_card: null,
        // 后端只认 existing/new:新建卡已转成 user_card、本剧本 NPC 也是选已有卡 → 统一 existing
        role_mode: roleMode === "new" ? "existing" : "existing",
        birthpoint: birthpoint || null,
        // v29: 透传 source (custom|ai) 给后端落库 identity_cards.source;identity=null 表示不挂 overlay
        identity: identity ? {
          name: identity.name || "",
          role: identity.role || "",
          background: identity.background || "",
          source: identity.source || "custom",
        } : null,
        story_intent: storyIntent.trim() || null,
        // 独立字段,与 identity 解耦:即使没挂身份卡也要带,后端写到 state.player.player_origin
        player_origin: playerOrigin || 'soul',
        // 没挂身份卡时无需传 identity_known;该字段只描述"是否知道这张身份卡"。
        ...(identity && playerOrigin !== 'body' ? { identity_known: identityKnown } : {}),
      };
      const res = onConfirm?.(payload);
      if (res && typeof res.then === "function") await res;
      // 反馈#4:成功开始游戏后才清空本次草稿(关弹窗/切页不清,保证回来能续填)
      clearNewgameDraft();
    } catch (e) {
      const msg = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('saves.new_game.create_fail');
      setSubmitErr(msg);
      // 自动检测 KB 复核 gate, 翻出 inline fast-path 按钮("一键标记并重试")
      if (msg && /KB 复核|review_status|尚未复核|尚未通过/.test(String(msg))) {
        setReviewGateBlocked(true);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const oneClickMarkAndRetry = async () => {
    if (!scriptId) return;
    setSubmitting(true); setSubmitErr("");
    try {
      const r = await fetch(`${window.__API_BASE || ""}/api/scripts/${parseInt(scriptId, 10)}/mark-reviewed`, {
        method: "POST", credentials: "include",
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || data.ok === false) {
        throw new Error((data && (data.error || data.detail)) || t('saves.page.err_mark_reviewed_fail', { status: r.status }));
      }
      setReviewGateBlocked(false);
      // 立刻重试创建
      await handleSubmit();
    } catch (e) {
      setSubmitErr(String(e && (e.message || e)) || t('saves.page.err_mark_reviewed_generic'));
      setSubmitting(false);
    }
  };

  /* ── EC2 式单页:基本信息区块 ── */
  const scriptOpts = scripts.map(sc => {
    const reason = newGameScriptBlockReason(sc, t);
    return {
      value: String(sc.id),
      label: reason ? `${sc.title}（${t('saves.new_game.script_not_ready_short')}）` : sc.title,
      description: reason || undefined,
      disabled: !!reason,
    };
  });

  const sec_basic = (
    // Cloudscape Container 内部 SpaceBetween 包 [header, children],期望 children 顶层有 key
    <CSSpaceBetween key="sec_basic" size="m">
      <CSColumnLayout key="fields" columns={2}>
        <CSFormField label={t('saves.new_game.field_save_name')} constraintText={t('saves.new_game.field_save_name_req')}>
          <CSInput value={title} onChange={({ detail }) => setTitle(detail.value)} autoFocus />
        </CSFormField>
        <CSFormField label={t('saves.new_game.field_script')} constraintText={t('saves.new_game.field_script_req')}>
          <CSSelect
            selectedOption={scriptOpts.find(o => o.value === scriptId) || null}
            options={scriptOpts}
            disabled={!scripts.length}
            placeholder={scripts.length ? t('saves.new_game.field_script_placeholder') : t('saves.new_game.field_script_no_scripts')}
            onChange={({ detail }) => {
              const v = detail.selectedOption.value;
              setScriptId(v);
              setBirthpoint(null);
              if (v) lsSet('newgame.lastScriptId', v);
            }}
          />
        </CSFormField>
      </CSColumnLayout>
      {scriptBlockReason && (
        <CSAlert key="script-block" type="warning" header={t('saves.new_game.script_not_ready_title')}>
          {scriptBlockReason}
        </CSAlert>
      )}
    </CSSpaceBetween>
  );

  // 单卡渲染(「使用现有」与「本剧本 NPC」两个 tab 共用,避免标记发散)。
  const roleCardEl = (c) => (
    <label key={c.key} className={`pl-newgame-card ${pickedCard === c.key ? 'active' : ''}`}>
      <input type="radio" checked={pickedCard === c.key} onChange={() => setPickedCard(c.key)} />
      <div className="pl-newgame-card-avatar serif">{c.name.slice(0, 1)}</div>
      <div className="pl-newgame-card-body">
        <strong>{c.name}</strong>
        <span className="muted-2" style={{ fontSize: 11.5 }}>
          {c.subtitle} · {c.kind === 'persona'
            ? t('saves.new_game.card_kind_persona')
            : c.kind === 'script_card'
              ? t('saves.new_game.card_kind_npc', { defaultValue: '本剧本 NPC' })
              : t('saves.new_game.card_kind_user')}
        </span>
      </div>
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        {c.pinned && <span className="pill accent" style={{ fontSize: 10.5 }}><Icon name="pin" size={9} /> {t('saves.new_game.card_default_pill')}</span>}
        <button type="button" className="btn btn-ghost" style={{ fontSize: 11.5, padding: '4px 10px' }}
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); openPreview(c); }}>
          <Icon name="eye" size={11} /> {t('saves.new_game.card_preview_btn')}
        </button>
      </div>
    </label>
  );

  const step1Content = (
    // Cloudscape SpaceBetween 内部用 React.Children.map 加间距,条件渲染的 children 需要稳定 key
    <CSSpaceBetween key="step1" size="l">
      <CSFormField key="mode" label={t('saves.new_game.role_mode_label')}>
        <CSSegmentedControl
          selectedId={roleMode}
          options={[
            { id: 'existing', text: t('saves.new_game.role_mode_existing'), disabled: allRoleOptions.length === 0 },
            { id: 'script_npc', text: t('saves.new_game.role_mode_script_npc', { defaultValue: '本剧本 NPC' }), disabled: scriptNpcOptions.length === 0 },
            { id: 'new', text: t('saves.new_game.role_mode_new') },
          ]}
          onChange={({ detail }) => {
            const m = detail.selectedId;
            setRoleMode(m);
            // 切 tab 时把选中卡同步到该 tab 的来源,避免「选中卡在另一个 tab 里看不见」。
            if (m === 'script_npc') { if (!_pickedIsNpc && scriptNpcOptions[0]) setPickedCard(scriptNpcOptions[0].key); }
            else if (m === 'existing') { if (_pickedIsNpc && allRoleOptions[0]) setPickedCard(allRoleOptions[0].key); }
          }}
        />
      </CSFormField>
      {roleMode === 'existing' && allRoleOptions.length > 0 && (
        <div key="existing-cards" className="pl-newgame-cards">
          {allRoleOptions.map(roleCardEl)}
          <a className="pl-newgame-card pl-newgame-card-link" href="/cards" onClick={(e) => { e.preventDefault(); onClose && onClose(); plNavigate('cards'); }}>
            <Icon name="folder" size={14} /><span>{t('saves.new_game.card_library_link')}</span>
          </a>
        </div>
      )}
      {roleMode === 'script_npc' && (
        <div key="script-npc-cards">
          <CSBox color="text-body-secondary" fontSize="body-s" padding={{ bottom: 'xs' }}>
            {t('saves.new_game.script_npc_desc', { defaultValue: '直接扮演本剧本里的原著 NPC —— 选定后出身会自动锁为「本世界人」(你就是这个角色本人,GM 严格守世界观)。' })}
          </CSBox>
          {scriptNpcOptions.length > 0 ? (
            <div className="pl-newgame-cards">{scriptNpcOptions.map(roleCardEl)}</div>
          ) : (
            <CSBox color="text-status-inactive" fontSize="body-s">
              {scriptId
                ? t('saves.new_game.script_npc_empty', { defaultValue: '该剧本暂无 NPC 角色卡(可在剧本编辑器里生成 / 提取人物)。' })
                : t('saves.new_game.script_npc_pick_script', { defaultValue: '请先在上方选择剧本。' })}
            </CSBox>
          )}
        </div>
      )}
      {roleMode === 'new' && (
        <div key="new-card">
          <CSBox color="text-body-secondary" fontSize="body-s" padding={{ bottom: 's' }}>
            {t('saves.new_game.new_card_desc')}
          </CSBox>
          <CardEditFields form={newCardForm} u={uNewCard} kind="user" />
        </div>
      )}
    </CSSpaceBetween>
  );

  const step4Content = (
    // Cloudscape InternalSpaceBetween 用 flattenChildren+map(child=>createElement('div',{key},child)),
    // 子元素没 key 时 wrapper div 的 key 全是 undefined → React 报「Each child should have a unique key」
    <CSSpaceBetween key="step4" size="m">
      <CSBox key="intro" color="text-body-secondary" fontSize="body-s">
        {t('saves.new_game.intent_desc').split('\n').map((line, i) => (
          <div key={`l${i}`}>{line}</div>
        ))}
      </CSBox>
      <CSFormField key="textarea" label={t('saves.new_game.intent_label')}>
        <CSTextarea
          rows={6}
          value={storyIntent}
          onChange={({ detail }) => setStoryIntent(detail.value)}
          placeholder={t('saves.page.intent_placeholder')}
        />
      </CSFormField>
    </CSSpaceBetween>
  );

  // 区块标题:h2 + 说明,可选项加「· 可选」标
  const secHeader = (text, desc, optional) => (
    <CSHeader variant="h2" description={desc}>
      {text}{optional ? <CSBox variant="span" color="text-status-inactive" fontSize="body-s">{t('saves.new_game.sec_optional')}</CSBox> : null}
    </CSHeader>
  );

  // 右侧概要:必填项完成度 + 已选摘要 + 创建按钮
  const reqRows = [
    { label: t('saves.new_game.req_save_script'), ok: step1Valid },
    { label: t('saves.new_game.req_role'), ok: step2Valid },
    { label: t('saves.new_game.req_birthpoint'), ok: step3Valid },
    { label: t('saves.new_game.req_identity'), ok: step4Valid },
  ];
  const allValid = step1Valid && step2Valid && step3Valid && step4Valid;
  const pickedRoleName = roleMode === 'new'
    ? (newCardForm.name.trim() || t('saves.new_game.new_role_default'))
    : (allSelectableRoles.find(o => o.key === pickedCard)?.name || '—');

  // 角色卡预览:从原始 personas / userCards 取完整对象供 CardSheet 渲染
  const openPreview = (opt) => {
    const full = opt.kind === 'persona'
      ? personas.find(p => String(p.id || p.slug) === String(opt.id || opt.slug))
      : opt.kind === 'script_card'
        ? scriptNpcCards.find(c => String(c.id) === String(opt.id))
        : userCards.find(c => String(c.id || c.slug) === String(opt.id || opt.slug));
    const card = full || { name: opt.name, identity: opt.subtitle };
    // 剧本 NPC 卡 user_id=NULL,不能当 kind="user" 渲染(CardSheet 内 SkillContentSection
    // 会调用户归属端点 myGet → 404)。按来源记 kind,script_card → 'npc'。
    const previewKind = opt.kind === 'script_card' ? 'npc' : 'user';
    setPreviewCard({ card: { ...card, identity: card.identity || card.role || opt.subtitle }, name: opt.name, kind: previewKind });
  };

  const node = (
    <div style={{ position: 'fixed', top: 'var(--nav-h, 53px)', left: 0, right: 0, bottom: 0, zIndex: 1000, background: 'var(--bg, #1a1817)', overflow: 'auto' }}>
      {/* 顶部栏:标题 + 取消(位于平台顶栏下方,保留平台导航) */}
      <div style={{ position: 'sticky', top: 0, zIndex: 3, background: '#131211', borderBottom: '1px solid #36322d' }}>
        <div style={{ maxWidth: 1240, margin: '0 auto', padding: '13px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
          <div style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 18, fontWeight: 600, color: '#ebe7df' }}>{t('saves.new_game.page_title')}</div>
          <CSButton iconName="close" variant="link" onClick={onClose}>{t('saves.new_game.btn_cancel')}</CSButton>
        </div>
      </div>

      {/* 响应式:窄屏两栏变单栏 */}
      <style>{'.ng-modal-cols{display:flex;gap:20px;align-items:flex-start}.ng-modal-summary{width:320px;flex-shrink:0;position:sticky;top:72px}@media(max-width:768px){.ng-modal-cols{flex-direction:column}.ng-modal-summary{width:100%;position:static;top:auto}}'}</style>
      <div style={{ maxWidth: 1240, margin: '0 auto', padding: '20px 24px 80px' }}>
        <div className="ng-modal-cols">
          {/* 左:各模块平铺 */}
          <div style={{ flex: 1, minWidth: 0 }}>
            <CSSpaceBetween size="l">
              {loading && (
                <CSBox key="loading" color="text-body-secondary"><Icon name="spinner" size={13} className="spin" /> {t('saves.new_game.loading')}</CSBox>
              )}
              {!loading && scripts.length === 0 && (
                <CSAlert key="no-scripts" type="warning" header={t('saves.new_game.no_scripts_title')}>
                  {t('saves.new_game.no_scripts_body')} <a href="/scripts-import" onClick={(e) => { e.preventDefault(); onClose && onClose(); plNavigate('scripts-import'); }}>{t('saves.new_game.no_scripts_link')}</a> {t('saves.new_game.no_scripts_suffix')}
                </CSAlert>
              )}
              {/* Cloudscape SpaceBetween 内部用 React.Children.map 加间距,需要 child 显式 key */}
              <CSContainer key="basic" header={secHeader(t('saves.new_game.sec_basic_title'), t('saves.new_game.sec_basic_desc'))}>{sec_basic}</CSContainer>
              <CSContainer key="role" header={secHeader(t('saves.new_game.sec_role_title'), t('saves.new_game.sec_role_desc'))}>{step1Content}</CSContainer>
              <CSContainer key="birthpoint" header={secHeader(t('saves.new_game.sec_birthpoint_title'), scriptId ? t('saves.new_game.sec_birthpoint_desc_ready') : t('saves.new_game.sec_birthpoint_desc_wait'))}>
                {scriptBlockReason
                  ? <CSAlert key="birthpoint-block" type="warning" header={t('saves.new_game.script_not_ready_title')}>{scriptBlockReason}</CSAlert>
                  : scriptId
                  ? <BirthpointStep key="birthpoint-step" scriptId={scriptId} birthpoint={birthpoint} setBirthpoint={setBirthpoint} />
                  : <CSBox key="birthpoint-empty" color="text-body-secondary" fontSize="body-s">{t('saves.new_game.sec_birthpoint_empty')}</CSBox>}
              </CSContainer>
              <CSContainer key="identity" header={secHeader(t('saves.new_game.sec_identity_title'), t('saves.new_game.sec_identity_desc'))}>
                <IdentityStep key="identity-step" scriptId={scriptId} birthpoint={birthpoint} pickedCard={pickedCard} allRoleOptions={allSelectableRoles} identity={identity} setIdentity={(id) => setIdentity(id)} playerOrigin={playerOrigin} setPlayerOrigin={(o) => { setPlayerOrigin(o); if (o === 'body') { /* body 无身份卡,identityKnown 设 null */ } else if (identityKnown === null || identityKnown === undefined) { setIdentityKnown(true); } }} identityKnown={identityKnown} setIdentityKnown={setIdentityKnown} />
              </CSContainer>
              <CSContainer key="intent" header={secHeader(t('saves.new_game.sec_intent_title'), t('saves.new_game.sec_intent_desc'), true)}>{step4Content}</CSContainer>
            </CSSpaceBetween>
          </div>

          {/* 右:概要 + 创建(sticky → 窄屏 static)
              CSSpaceBetween 内部 flattenChildren+map, 每个 child 需要 key 否则 wrapper key 为 undefined */}
          <div className="ng-modal-summary">
            <CSContainer header={<CSHeader variant="h2">{t('saves.new_game.summary_title')}</CSHeader>}>
              <CSSpaceBetween size="m">
                <CSSpaceBetween key="status" size="xs">
                  {reqRows.map(r => (
                    <CSStatusIndicator key={r.label} type={r.ok ? 'success' : 'pending'}>{r.label}</CSStatusIndicator>
                  ))}
                </CSSpaceBetween>
                <CSKeyValuePairs key="kv" columns={1} items={[
                  { label: t('saves.new_game.summary_save_name'), value: title.trim() || '—' },
                  { label: t('saves.new_game.summary_script'), value: scriptOpts.find(o => o.value === scriptId)?.label || '—' },
                  { label: t('saves.new_game.summary_role'), value: pickedRoleName },
                  { label: t('saves.new_game.summary_birthpoint'), value: birthpoint?.story_time_label || '—' },
                  { label: t('saves.new_game.summary_identity'), value: identity?.name || identity?.role || '—' },
                ]} />
                {submitErr && (
                  <CSAlert
                    key="err"
                    type={reviewGateBlocked ? 'warning' : 'error'}
                    action={reviewGateBlocked ? (
                      <CSButton onClick={oneClickMarkAndRetry} loading={submitting} disabled={submitting}>
                        {t('saves.new_game.mark_reviewed_and_retry')}
                      </CSButton>
                    ) : undefined}
                  >
                    {submitErr}
                  </CSAlert>
                )}
                <div key="btns" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <CSButton variant="primary" disabled={!allValid || submitting} loading={submitting}
                    onClick={() => { if (allValid) handleSubmit(); }}>
                    {submitting ? t('saves.new_game.btn_creating') : t('saves.new_game.btn_create')}
                  </CSButton>
                  <CSButton variant="link" onClick={onClose}>{t('saves.new_game.btn_cancel_link')}</CSButton>
                </div>
              </CSSpaceBetween>
            </CSContainer>
          </div>
        </div>
      </div>

      {/* 角色卡预览(只读) */}
      <CSModal
        visible={!!previewCard}
        onDismiss={() => setPreviewCard(null)}
        header={t('saves.new_game.preview_title', { name: previewCard?.name || '' })}
        size="medium"
        footer={<div style={{ textAlign: 'right' }}><CSButton variant="primary" onClick={() => setPreviewCard(null)}>{t('saves.new_game.preview_close')}</CSButton></div>}
      >
        {previewCard && <CardSheet card={previewCard.card} kind={previewCard.kind || 'user'} />}
      </CSModal>
    </div>
  );
  return createPortal(node, document.body);
}

export { NewGameModal, newGameScriptBlockReason };
