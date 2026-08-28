/* 三个角色卡列表视图:用户卡 / NPC 卡 / 在线卡库 —— 从 pages/cards.jsx 拆出,逐字节不变。 */

import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import AvatarImg from '../AvatarImg.jsx';
import { ResizableSplit } from '../../platform-app.jsx';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSHeader from '@cloudscape-design/components/header';
import CSButton from '@cloudscape-design/components/button';
import CSInput from '@cloudscape-design/components/input';
import CSAlert from '@cloudscape-design/components/alert';
import CSBox from '@cloudscape-design/components/box';
import CSBadge from '@cloudscape-design/components/badge';
import CSTable from '@cloudscape-design/components/table';
import CSTextFilter from '@cloudscape-design/components/text-filter';
import CSSegmentedControl from '@cloudscape-design/components/segmented-control';
import CSSelect from '@cloudscape-design/components/select';
import { USER_CARDS, NPC_CARDS, _oneLine, ELLIPSIS_1 } from './helpers.js';
import { CardGrid } from './CardGrid.jsx';
import { CardDetailPanel } from './CardDetailPanel.jsx';
import { CardEditModal } from './CardEditModal.jsx';
import { TavernImportModal } from './TavernImportModal.jsx';
import { runCardImport } from '../../lib/tavern-card-import.js';

/* 在线角色卡库 — 浏览并完整导入其他用户公开分享的 PC 角色卡。
   GET /api/cards/public · POST /api/cards/public/{id}/clone(完整复制进自己卡库,非指针) */
function OnlineCardsView() {
  const { t } = useTranslation();
  const [items, setItems] = React.useState(null);
  const [q, setQ] = React.useState('');
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState('');
  const [importing, setImporting] = React.useState({});

  const load = React.useCallback(async (query) => {
    setLoading(true); setErr('');
    try {
      const r = await window.api.cards.publicList(query ? { q: query } : undefined);
      setItems((r && r.items) || []);
    } catch (e) { setErr(e?.message || t('cards.page.online.load_fail')); setItems([]); }
    finally { setLoading(false); }
  }, [t]);

  React.useEffect(() => { load(''); }, [load]);

  const doImport = async (c) => {
    setImporting((p) => ({ ...p, [c.id]: true }));
    try {
      await window.api.cards.cloneFromPublic(c.id);
      window.__apiToast?.(t('cards.page.online.import_ok'), { kind: 'ok', duration: 2200, detail: t('cards.page.online.import_ok_detail', { name: c.name }) });
      load(q);  // 刷新热度
    } catch (e) {
      window.__apiToast?.(t('cards.page.online.import_fail'), { kind: 'danger', detail: e?.payload?.error || e?.message });
    } finally {
      setImporting((p) => ({ ...p, [c.id]: false }));
    }
  };

  return (
    <CSSpaceBetween size="l">
      <CSHeader
        variant="h1"
        description={t('cards.page.online.header_desc')}
        actions={<CSButton iconName="refresh" loading={loading} onClick={() => load(q)}>{t('common.refresh')}</CSButton>}
      >{t('cards.page.online.title')}</CSHeader>

      <div style={{ display: 'flex', gap: 8, maxWidth: 460 }}>
        <div style={{ flex: 1 }}>
          <CSInput value={q} onChange={({ detail }) => setQ(detail.value)} placeholder={t('cards.page.online.search_placeholder')}
            onKeyDown={(e) => { if (e.detail.key === 'Enter') load(q); }} type="search" />
        </div>
        <CSButton onClick={() => load(q)}>{t('cards.page.online.btn_search')}</CSButton>
      </div>

      {err && <CSAlert type="error" header={t('cards.page.online.load_fail')}>{err}</CSAlert>}
      {loading && items == null ? <CSBox color="text-body-secondary" padding="m">{t('cards.page.online.loading')}</CSBox>
        : (items && items.length === 0) ? <CSBox textAlign="center" color="text-body-secondary" padding={{ vertical: 'xl' }}>{t('cards.page.online.empty')}</CSBox>
        : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 14 }}>
            {(items || []).map((c) => (
              <div key={c.id} style={{ border: '1px solid var(--line, #36322d)', borderRadius: 10, padding: 14, background: 'var(--panel, #211f1d)', display: 'grid', gap: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <AvatarImg src={c.avatar_path || null} name={c.name || '?'} size={40} shape="rounded" />
                    <strong style={{ fontSize: 15 }}>{c.name || t('cards.detail.unnamed')}</strong>
                  </div>
                  <span style={{ fontSize: 11, color: 'var(--text-quiet, #9a948c)' }}>♥ {c.clone_count || 0}</span>
                </div>
                {c.identity && <div style={{ fontSize: 12, color: 'var(--accent, #c96442)' }}>{String(c.identity).slice(0, 40)}</div>}
                <div style={{ fontSize: 12, color: 'var(--text-quiet, #9a948c)', lineHeight: 1.5, minHeight: 36, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                  {(c.personality || c.background || c.appearance || t('cards.page.online.no_bio')).slice(0, 90)}
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {(c.tags || []).slice(0, 3).map((tg) => <CSBadge key={tg}>{tg}</CSBadge>)}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginTop: 2 }}>
                  <span style={{ fontSize: 11, color: 'var(--muted, #b8b2a8)' }}>by {c.owner_name || t('cards.page.online.anon')}</span>
                  <CSButton variant="primary" loading={!!importing[c.id]} onClick={() => doImport(c)}>{t('cards.page.online.btn_import')}</CSButton>
                </div>
              </div>
            ))}
          </div>
        )}
    </CSSpaceBetween>
  );
}

function UserCardsView() {
  const { t } = useTranslation();
  // task 47：登录态零 mock。原 useState(USER_CARDS) 初始就显示 顾承砚/沈知微/阿衡/无名旅人
  // 这套示例卡片，reload 拿到真数据再覆盖。匿名时 reload 失败仍保留 USER_CARDS（designer offline）。
  const IS_ANON = !(window.RPG_AUTH && window.RPG_AUTH.authed);
  const [cards, setCards] = useStatePL(IS_ANON ? USER_CARDS : []);
  const [filter, setFilter] = useStatePL("all");
  const [q, setQ] = useStatePL("");
  const [adding, setAdding] = useStatePL(false);
  const [importing, setImporting] = useStatePL(false);
  const [selectedId, setSelectedId] = useStatePL(null);

  const reload = React.useCallback(async () => {
    try {
      const r = await window.api.cards.myList();
      const list = Array.isArray(r) ? r : (r?.cards || r?.items || []);
      setCards(list.map(c => ({
        id: String(c.id),
        name: c.name,
        role: c.identity || c.role || "—",
        tone: c.tone || "—",
        origin: c.origin || t('cards.list.origin_generic'),
        bio: c.description || c.summary || c.bio || c.personality || c.current_status || c.appearance || "",
        tags: c.tags || [],
        pinned: !!c.pinned,
        is_public: !!c.is_public,
        uses: c.uses || 0,
        updated: window.__fmt?.ago(c.updated_at) || c.updated_at || "—",
        _raw: c,
      })));
    } catch (_) {}
  }, [t]);
  useEffectPL(() => { reload(); }, [reload]);
  // 监听 NPC 迁移事件 → 自动刷新用户角色卡列表，
  // 让用户切到用户卡 tab 就能看到刚迁移过来的卡。
  useEffectPL(() => {
    const onUpd = () => reload();
    window.addEventListener("rpg-user-cards-updated", onUpd);
    return () => window.removeEventListener("rpg-user-cards-updated", onUpd);
  }, [reload]);

  // task 100: modal 现在直接发 DB 字段名 (name/identity/personality/appearance/
  // speech_style/secrets/tags),不再做中间映射,也不再传 tone/pinned 等死字段。
  const onSaveCard = async (vals) => {
    try {
      await window.api.cards.myUpsert(vals);
      window.__apiToast?.(adding ? t('cards.toast.added') : t('cards.toast.saved'), { kind: "ok" });
      setAdding(false);
      reload();
    } catch (e) {
      window.__apiToast?.(t('cards.toast.save_fail'), { kind: "danger", detail: e?.message });
    }
  };

  const onImport = async (payload) => {
    try {
      if (payload?.type === "chat" && payload.jsonl) {
        const title = payload.charName ? t('cards.page.import.chat_title_prefix', { name: payload.charName }) : undefined;
        await window.api.chats.importTavern({ jsonl: payload.jsonl, title });
        window.__apiToast?.(t('cards.toast.chat_imported'), { kind: "ok" });
        setImporting(false);
        return;
      }
      // 卡导入(文件 / 粘贴 JSON)走共享执行段:多选的文件逐张导入,失败逐张统计 ——
      // 此前只导 payload.file 一张,弹窗里列出来的其余文件被静默丢弃。
      const r = await runCardImport(payload, {
        importFile: (f) => window.api.cards.importTavern(f, { aiSplit: payload?.aiSplit }),
        importJson: (body) => window.api.cards.importJson(body),
      });
      if (r.failures.length) {
        window.__apiToast?.(t('cards.toast.import_fail'), {
          kind: r.imported ? "warn" : "danger",
          detail: r.failures.map((f) => (f.name ? `${f.name}: ` : '') + f.message).join(' · '),
        });
      } else {
        window.__apiToast?.(t('cards.toast.imported'), { kind: "ok" });
      }
      setImporting(false);
      reload();
    } catch (e) {
      window.__apiToast?.(t('cards.toast.import_fail'), { kind: "danger", detail: e?.message });
    }
  };

  let filtered = cards;
  if (filter === "pinned") filtered = filtered.filter(c => c.pinned);
  if (q) filtered = filtered.filter(c => (c.name + c.role + c.bio + (c.tags || []).join(" ")).toLowerCase().includes(q.toLowerCase()));

  const selected = cards.find((x) => x.id === selectedId) || null;
  const onDuplicate = async (c) => {
    try {
      const src = c._raw || {};
      const body = { ...src, id: undefined, slug: undefined, name: (src.name || c.name) + t('cards.list.duplicate_suffix') };
      await window.api.cards.myUpsert(body);
      window.__apiToast?.(t('cards.toast.duplicated'), { kind: "ok" });
      reload();
    } catch (e) { window.__apiToast?.(t('cards.toast.duplicate_fail'), { kind: "danger", detail: e?.message }); }
  };
  const onDeleteCard = async (c) => {
    if (!await window.__confirm({ title: t('cards.confirm.delete_title'), message: t('cards.confirm.delete_message', { name: c.name }), danger: true, confirmText: t('cards.confirm.delete_btn') })) return;
    try {
      await window.api.cards.myDelete(c.id);
      window.__apiToast?.(t('cards.toast.deleted', { name: c.name }), { kind: "ok" });
      setSelectedId(null);
      setCards(cs => cs.filter(x => x.id !== c.id)); reload();
    } catch (e) { window.__apiToast?.(t('cards.toast.delete_fail'), { kind: "danger", detail: e?.message }); }
  };

  const detailEl = selected ? (
    <CardDetailPanel
      card={selected}
      kind="user"
      onSave={async (vals) => { await onSaveCard({ ...(selected._raw?.id ? { id: selected._raw.id } : { id: selected.id }), ...vals }); }}
      onDuplicate={() => onDuplicate(selected)}
      onDelete={() => onDeleteCard(selected)}
    />
  ) : null;

  const tableEl = (
    <CSTable
      variant="container"
      trackBy="id"
      selectionType="single"
      items={filtered}
      selectedItems={selected ? [selected] : []}
      onSelectionChange={({ detail }) => { const x = detail.selectedItems[0]; if (x) setSelectedId(x.id); }}
      onRowClick={({ detail }) => setSelectedId(detail.item.id)}
      empty={<CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>{q ? t('cards.empty.no_match') : t('cards.empty.no_user_cards')}</CSBox>}
      columnDefinitions={[
        { id: 'name', header: t('cards.list.col_card'), cell: (c) => (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, maxWidth: 'min(560px, 46vw)' }}>
            <AvatarImg src={(c._raw?.avatar_path) || c.avatar_path || null} name={c.name} size={36} shape="rounded" />
            <div style={{ minWidth: 0 }}>
              <CSBox fontWeight="bold">{c.name}</CSBox>
              <div style={{ ...ELLIPSIS_1, fontSize: 12.5, color: 'var(--text-quiet, #968f85)' }}>
                {_oneLine(c.role !== '—' ? c.role : c.bio, 80)}
              </div>
            </div>
          </div>
        ) },
        { id: 'tags', header: t('cards.list.col_tags'), cell: (c) => (c.tags?.length
          ? <CSSpaceBetween direction="horizontal" size="xxs">{c.tags.slice(0, 4).map((tg) => <CSBadge key={tg}>{tg}</CSBadge>)}</CSSpaceBetween>
          : <CSBox color="text-status-inactive">—</CSBox>) },
        { id: 'uses', header: t('cards.list.col_uses'), cell: (c) => t('cards.list.uses_count', { count: c.uses }) },
        { id: 'updated', header: t('cards.list.col_updated'), cell: (c) => c.updated },
      ]}
    />
  );

  return (
    <>
      <CSSpaceBetween size="l">
        <CSHeader
          variant="h1"
          counter={`(${cards.length})`}
          description={t('cards.list.user_cards_desc')}
          actions={
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSButton iconName="download" onClick={() => setImporting(true)}>{t('cards.import.btn_import')}</CSButton>
              <CSButton variant="primary" iconName="add-plus" onClick={() => setAdding(true)}>{t('cards.list.btn_add')}</CSButton>
            </CSSpaceBetween>
          }
        >{t('cards.list.user_cards_title')}</CSHeader>

        <CSSpaceBetween direction="horizontal" size="xs">
          <div style={{ minWidth: 260 }}>
            <CSTextFilter filteringText={q} filteringPlaceholder={t('cards.list.search_placeholder')}
              onChange={({ detail }) => setQ(detail.filteringText)} />
          </div>
          <CSSegmentedControl selectedId={filter}
            options={[{ id: 'all', text: t('cards.list.filter_all') }, { id: 'pinned', text: t('cards.list.filter_pinned') }]}
            onChange={({ detail }) => setFilter(detail.selectedId)} />
        </CSSpaceBetween>

        {selected
          ? <ResizableSplit storageKey="cards" top={tableEl} bottom={detailEl} />
          : tableEl}

      </CSSpaceBetween>
      {adding && (
        <CardEditModal
          card={null}
          isNew
          kind="user"
          onClose={() => setAdding(false)}
          onSave={onSaveCard}
        />
      )}
      <TavernImportModal open={importing} onClose={() => setImporting(false)} onConfirm={onImport} />
    </>
  );
}

function NpcCardsView() {
  const { t } = useTranslation();
  // task 47：之前完全用硬编码 NPC_CARDS（韩司直/童守人/税吏甲/陈渡海/尚书令），
  // 跟登录用户的真实剧本毫无关系。改成跨所有用户剧本聚合
  // /api/scripts/{id}/character-cards，按真实存档分组。
  // 用户的真实"NPC 角色卡"= 后端每个 script 下的 character_cards 表。
  const [cards, setCards] = useStatePL([]);
  const [loading, setLoading] = useStatePL(true);
  const [error, setError] = useStatePL("");
  const [saveFilter, setSaveFilter] = useStatePL("all");
  const [q, setQ] = useStatePL("");
  const [edit, setEdit] = useStatePL(null);
  const [adding, setAdding] = useStatePL(false);
  const [scripts, setScripts] = useStatePL([]);
  const [newNpcScriptId, setNewNpcScriptId] = useStatePL("");

  const reload = React.useCallback(async () => {
    setLoading(true); setError("");
    try {
      // 1) 拉所有 scripts；2) 对每个 script 并行拉 character-cards
      const sr = await window.api.scripts.list();
      const scripts = Array.isArray(sr) ? sr : (sr?.items || sr?.scripts || []);
      setScripts(scripts);
      if (!scripts.length) { setCards([]); setLoading(false); return; }
      const lists = await Promise.all(scripts.map(async (s) => {
        try {
          const r = await window.api.cards.scriptList(s.id);
          const arr = Array.isArray(r) ? r : (r?.items || r?.cards || []);
          return arr.map(c => ({
            id: String(c.id),
            name: c.name || t('cards.detail.unnamed'),
            role: c.identity || c.role || "—",
            tone: c.tone || t('cards.list.tone_neutral'),
            save: s.title || t('cards.list.script_n', { id: s.id }),
            script_id: s.id,
            bio: c.appearance || c.personality || c.summary || c.description || "",
            tags: Array.isArray(c.tags) ? c.tags : [],
            uses: c.uses || 0,
            updated: window.__fmt?.ago(c.updated_at) || c.updated_at || "—",
            pinned: !!c.pinned,
            _raw: c,
          }));
        } catch (_) { return []; }
      }));
      setCards(lists.flat());
    } catch (e) {
      setError(e?.message || t('cards.toast.npc_load_fail'));
      // 匿名 / API 不可达 → 兜底到 mock（designer offline preview）
      if (!(window.RPG_AUTH && window.RPG_AUTH.authed)) {
        setCards((NPC_CARDS || []).map(c => ({ ...c, script_id: null })));
      } else {
        setCards([]);
      }
    } finally { setLoading(false); }
  }, [t]);
  React.useEffect(() => { reload(); }, [reload]);

  // 按 script_id 筛选(不能用 c.save=剧本标题——同名剧本「未命名/新档」会互相串台,
  // 且 selectedScriptId 反查命中第一个同名剧本 → 新增 NPC 落到错误剧本)。
  const scriptKeys = [...new Set(cards.map((c) => String(c.script_id)))].filter((k) => k && k !== 'null' && k !== 'undefined');
  const titleOfScript = (sid) => {
    const s = scripts.find((x) => String(x.id) === String(sid));
    return (s && s.title) || cards.find((c) => String(c.script_id) === String(sid))?.save || t('cards.list.script_n', { id: sid });
  };
  let filtered = cards;
  if (saveFilter !== "all") filtered = filtered.filter((c) => String(c.script_id) === saveFilter);
  if (q) filtered = filtered.filter(c =>
    (String(c.name) + String(c.role) + String(c.bio) + (c.tags || []).join(" "))
      .toLowerCase().includes(q.toLowerCase())
  );

  const saveOpts = [{ value: "all", label: t('cards.list.all_scripts') }, ...scriptKeys.map((k) => ({ value: k, label: titleOfScript(k) }))];
  const selectedScriptId = saveFilter !== "all" ? saveFilter : null;
  const scriptOptions = scripts.map((s) => ({
    value: String(s.id),
    label: s.title || t('cards.list.script_n', { id: s.id }),
  }));
  useEffectPL(() => {
    const fallback = selectedScriptId || scripts[0]?.id || "";
    setNewNpcScriptId((prev) => (
      prev && scripts.some((s) => String(s.id) === String(prev))
        ? String(prev)
        : String(fallback || "")
    ));
  }, [scripts, selectedScriptId]);
  const onSaveNpc = async (payload) => {
    // #10 编辑/删除补全: 编辑用卡自身 script_id;新增时 filter=all 下无 selectedScriptId,
    // 退到 _raw.script_id / 唯一剧本(常见单档场景),避免"filter=all 新增 NPC 卡报
    // script_required 卡死"。仍无法确定(多剧本且未选)才提示用户先选剧本。
    const sid = edit?.script_id || edit?._raw?.script_id || selectedScriptId || (adding ? newNpcScriptId : null) || (scripts.length === 1 ? scripts[0].id : null);
    if (!sid) {
      window.__apiToast?.(t('cards.toast.npc_script_required'), { kind: "warn", duration: 2600 });
      throw new Error("script_id required");
    }
    const body = {
      ...payload,
      id: edit?._raw?.id ?? edit?.id ?? payload?.id,
    };
    try {
      const r = await window.api.cards.scriptUpsert(sid, body);
      if (r && r.ok === false) throw new Error(r.error || r.detail || t('cards.toast.save_fail'));
      window.__apiToast?.(adding ? t('cards.toast.added') : t('cards.toast.saved'), { kind: "ok" });
      setEdit(null); setAdding(false);
      await reload();
    } catch (e) {
      window.__apiToast?.(t('cards.toast.save_fail'), { kind: "danger", detail: e?.message || String(e) });
      throw e;
    }
  };
  return (
    <>
      <CSSpaceBetween size="l">
        <CSHeader
          variant="h1"
          counter={`(${cards.length})`}
          description={`${t('cards.list.npc_cards_desc')}${loading ? ' ' + t('cards.list.loading') : ''}`}
          actions={<CSButton variant="primary" iconName="add-plus" onClick={() => {
            const fallback = selectedScriptId || newNpcScriptId || scripts[0]?.id || "";
            if (fallback) setNewNpcScriptId(String(fallback));
            setAdding(true);
          }}>{t('cards.list.btn_add_npc')}</CSButton>}
        >{t('cards.list.npc_cards_title')}</CSHeader>
        {error && <CSAlert type="error" header={t('cards.toast.load_fail_header')}>{error}</CSAlert>}
        <CardGrid cards={filtered} onEdit={setEdit} kind="npc"
          empty={
            <CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>
              {loading ? t('cards.list.loading') : <>{t('cards.empty.no_npc_cards')}<br />{t('cards.empty.no_npc_hint')}</>}
            </CSBox>
          }
          filter={
            <CSSpaceBetween direction="horizontal" size="xs">
              <div style={{ minWidth: 240 }}>
                <CSTextFilter filteringText={q} filteringPlaceholder={t('cards.list.search_npc_placeholder')}
                  onChange={({ detail }) => setQ(detail.filteringText)} />
              </div>
              <CSSelect selectedOption={saveOpts.find((o) => o.value === saveFilter)}
                options={saveOpts} disabled={loading}
                onChange={({ detail }) => setSaveFilter(detail.selectedOption.value)} />
            </CSSpaceBetween>
          }
          onPromoteToUser={() => {
            // 迁移到 user_card 后通知用户角色卡列表刷新(如果当前 mounted)
            try { window.dispatchEvent(new CustomEvent("rpg-user-cards-updated")); } catch (_) {}
          }}
          onDeleted={() => reload()} />
      </CSSpaceBetween>
      {(edit || adding) && (
        <CardEditModal
          card={edit?._raw || edit}
          isNew={adding}
          kind="npc"
          targetScriptOptions={adding ? scriptOptions : []}
          targetScriptId={adding ? newNpcScriptId : ""}
          onTargetScriptChange={setNewNpcScriptId}
          onClose={() => { setEdit(null); setAdding(false); }}
          onSave={onSaveNpc}
        />
      )}
    </>
  );
}

export { OnlineCardsView, UserCardsView, NpcCardsView };
