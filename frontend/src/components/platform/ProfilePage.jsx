// 工作台首页 ProfilePage。纯机械从 platform-app.jsx 搬出,零行为变化。
import React from 'react';
import { useState as useStatePL } from 'react';
import { useTranslation } from 'react-i18next';
import { plNavigate } from '../../router.js';
import { Composer } from '../../game-composer.jsx';
import {
  usePlatformData, useReactiveUser,
} from './shared.jsx';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSTable from '@cloudscape-design/components/table';

/* ---------------------------- PROFILE -------------------------- */
function ProfilePage() {
  const { t } = useTranslation();
  const platform = usePlatformData();  // task 45：响应式 platform，登录后真实数据自动注入
  const { database = {}, stats = {}, scripts = [], saves = [], recent_assets = [] } = platform;
  const user = useReactiveUser();  // task 13: 保存资料后即时同步显示名/简介
  // task 12：以真实数组长度为最权威源；data-loader 已把 stats.* 改为
  // 真实值/null，但这里再做一层兜底，避免设计预览模式 (offline) 残留的 mock 12 漏到 UI。
  // null 安全千分位统一到 window.__fmt.n(注意:本组件局部版,非本文件导出的 K/M 缩写版)。
  const fmtN = (n) => (window.__fmt && window.__fmt.n)
    ? window.__fmt.n(n)
    : (n == null ? "—" : (typeof n === "number" ? n.toLocaleString() : String(n)));
  const realScripts = Array.isArray(scripts) ? scripts : [];
  // 首页「继续《》」+「最近游玩」只列游戏存档;酒馆对话从 #tavern 页(或下方空态输入框)进入,
  // 不混进游戏存档列表(否则点「继续」会离奇进游戏台)。
  const realSaves = (Array.isArray(saves) ? saves : [])
    .filter(s => (s?.save_kind || s?._raw?.save_kind || 'game') !== 'tavern');
  const wordTotal = realScripts.reduce((a, s) => a + (Number(s && s.word_count) || 0), 0);
  const wordWan = wordTotal > 0 ? (wordTotal / 10000).toFixed(0) : "—";
  const branchAgg = realSaves.reduce((a, s) => a + (Number(s && s.branch_count) || 0), 0) || (stats?.branches ?? null);
  // 工作台首页:问候 + 快速操作。身份资料(名字/简介/角色)归「个人主页」,这里不再重复。
  const lastSave = realSaves[0] || null;
  const hour = (() => { try { return new Date().getHours(); } catch (_) { return 12; } })();
  const greeting = hour < 5 ? t('platform.profile.greeting_late', '夜深了') : hour < 11 ? t('platform.profile.greeting_morning', '早上好') : hour < 14 ? t('platform.profile.greeting_noon', '中午好') : hour < 18 ? t('platform.profile.greeting_afternoon', '下午好') : t('platform.profile.greeting_evening', '晚上好');
  const lastScript = lastSave ? realScripts.find(sc => sc && sc.id === lastSave.script_id) : null;

  // 没有游戏存档时,「最近游玩」空态显示一个酒馆风输入框:提交即新建酒馆对话并自动发出第一句。
  const [tavernText, setTavernText] = useStatePL("");
  const [tavernBusy, setTavernBusy] = useStatePL(false);
  const onTavernSend = async () => {
    const first = tavernText.trim();
    if (!first || tavernBusy) return;
    setTavernBusy(true);
    try {
      const r = await window.api.tavern.create({});
      const saveId = r && r.save && r.save.id;
      if (!saveId) throw new Error(r?.error || r?.detail || t('platform.profile.tavern_no_id', '未返回对话 id'));
      // 把首句交给酒馆页:openChat 命中同一 save 时自动发送(失败兜底=预填到输入框)。
      try {
        sessionStorage.setItem('rpg_tavern_pending_first', JSON.stringify({ save_id: saveId, text: first }));
      } catch (_) {}
      setTavernText("");
      plNavigate('tavern');
    } catch (e) {
      window.__apiToast?.(t('platform.profile.tavern_create_failed', '新建对话失败'), { kind: 'danger', detail: e?.message });
    } finally {
      setTavernBusy(false);
    }
  };

  return (
    <CSSpaceBetween size="l">
      {/* 欢迎 Hero + 快速操作 */}
      <div style={{
        background: "linear-gradient(135deg, var(--panel-2,#282623) 0%, var(--panel,#211f1d) 100%)",
        border: "1px solid var(--line-soft,#2a2724)", borderRadius: 14, padding: "26px 28px",
      }}>
        <div style={{ fontSize: 13, color: "var(--accent,#c96442)", fontWeight: 600, letterSpacing: ".04em", marginBottom: 6 }}>
          {greeting}，{user.display_name || t('platform.profile.traveler', '旅行者')}
        </div>
        <div style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 23, fontWeight: 600, color: "var(--text,#ebe7df)", marginBottom: 6 }}>
          {t('platform.profile.hero_tagline', '继续你的故事，或开启新的旅程')}
        </div>
        <div style={{ fontSize: 13.5, color: "var(--text-quiet,#a8a195)", marginBottom: 18, lineHeight: 1.6 }}>
          {realScripts.length === 0
            ? t('platform.profile.no_scripts', '还没有剧本。先去「剧本」页导入一部长篇,平台会自动切章、提取世界书与 NPC 角色卡。')
            : (realSaves.length === 0
                ? t('platform.profile.no_saves', { n: realScripts.length, defaultValue: `已导入 ${realScripts.length} 部剧本。挑一本开启你的第一个存档吧。` })
                : t('platform.profile.has_saves', { saves: realSaves.length, scripts: realScripts.length, defaultValue: `你有 ${realSaves.length} 个存档、${realScripts.length} 部剧本在等你。` }))}
        </div>
        <CSSpaceBetween direction="horizontal" size="xs">
          {lastSave ? (
            <CSButton variant="primary" iconName="caret-right-filled" onClick={() => window.__openContinue?.(lastSave)}>
              {t('platform.profile.continue_save', { title: lastSave.title || lastScript?.title || t('platform.profile.last_save', '上次存档'), defaultValue: `继续《${lastSave.title || lastScript?.title || '上次存档'}》` })}
            </CSButton>
          ) : (
            <CSButton variant="primary" iconName="add-plus" onClick={() => plNavigate('scripts')}>{t('platform.profile.browse_scripts', '浏览剧本')}</CSButton>
          )}
          <CSButton iconName="folder" onClick={() => plNavigate('scripts')}>{t('platform.profile.script_library', '剧本库')}</CSButton>
          <CSButton iconName="user-profile" onClick={() => plNavigate('cards')}>{t('platform.profile.user_cards', '用户角色卡')}</CSButton>
          <CSButton iconName="settings" onClick={() => plNavigate('settings')}>{t('common.settings', '设置')}</CSButton>
        </CSSpaceBetween>
      </div>

      {/* 最近游玩 */}
      <CSContainer header={
        <CSHeader variant="h2" actions={<CSButton onClick={() => plNavigate('saves')} iconName="caret-right-filled">{t('platform.profile.all_saves', '全部存档')}</CSButton>}>
          {t('platform.profile.recent_play', '最近游玩')} <span className="muted-2" style={{fontWeight: "normal"}}>{t('platform.profile.recent_play_sub', '按上次操作时间')}</span>
        </CSHeader>
      }>
        {realSaves.length === 0 ? (
          <CSSpaceBetween size="s">
            <CSBox color="text-body-secondary" fontSize="body-s">
              {t('platform.profile.no_saves_hint', '还没有存档?直接说一句话,马上开始一段酒馆对话。')}
            </CSBox>
            <Composer
              text={tavernText} setText={setTavernText}
              onSend={onTavernSend} onStop={() => {}} running={tavernBusy}
              composerMode="writing"
              placeholder={t('platform.profile.tavern_placeholder', '想和谁聊聊?输入第一句话,直接开始一段对话…')}
              hideSlash hidePermission hideContinue hideAttach hideImageGen
              showSlash={false} showPlus={false} showModel={false} showPerm={false}
              toggleSlash={() => {}} togglePlus={() => {}} toggleModel={() => {}} togglePerm={() => {}}
              attachments={[]} removeAttachment={() => {}}
            />
          </CSSpaceBetween>
        ) : (
          <CSTable
            columnDefinitions={[
              {
                id: "title",
                header: t('platform.profile.col_script_save', '剧本 / 存档'),
                cell: s => {
                  const script = realScripts.find(sc => sc && sc.id === s.script_id);
                  return (
                    <div className="pl-title-cell">
                      <strong>{s.title || t('platform.profile.save_fallback', { id: s.id, defaultValue: `存档 #${s.id}` })}</strong>
                      <span className="muted-2 mono">{script?.title || "—"}</span>
                    </div>
                  );
                },
              },
              {
                id: "progress",
                header: t('platform.profile.col_progress', '进度'),
                cell: s => <span className="mono">{t('platform.profile.branch_nodes', { n: Number(s.branch_count) || 0, defaultValue: `${Number(s.branch_count) || 0} 分支节点` })}</span>,
              },
              {
                id: "last",
                header: t('platform.profile.col_last_play', '上次游玩'),
                cell: s => (
                  <span className="muted">
                    {s.current && <span className="pill accent" style={{marginRight: 6}}><span className="dot accent pulse" /> {t('platform.profile.playing', '在玩')}</span>}
                    {s.updated_at || "—"}
                  </span>
                ),
              },
              {
                id: "action",
                header: "",
                cell: s => (
                  <CSButton variant="primary" iconName="caret-right-filled"
                    onClick={() => window.__openContinue?.(s)}>
                    {t('platform.profile.continue_btn', '继续')}
                  </CSButton>
                ),
              },
            ]}
            items={realSaves}
            trackBy="id"
            empty={<CSBox color="text-body-secondary" textAlign="center">{t('platform.profile.no_saves_empty', '暂无存档')}</CSBox>}
          />
        )}
      </CSContainer>

      {/* task: 文件库已禁用(task #66 测试期),Home 不再展示「最近资源」section。
          mock-data.js 同步清空 recent_assets 假数据(之前新用户登入看到 3 个莫名其妙的
          假文件 — 南陵地图_v2.png / 光绪十三年残页扫描.zip / 雾港人物谱.md)。 */}

    </CSSpaceBetween>
  );
}

export { ProfilePage };
