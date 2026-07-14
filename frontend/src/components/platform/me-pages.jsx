// 个人中心(概览 / 编辑资料 / 隐私与安全)。纯机械从 platform-app.jsx 搬出,零行为变化。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import { plNavigate } from '../../router.js';
import Modal from '../Modal.jsx';
import AvatarImg from '../AvatarImg.jsx';
import MediaStudio from '../MediaStudio.jsx';
import {
  _FORM_KEYS, PromptModal, ConfirmModal, useAutoSave, usePlatformData, publishUser, useReactiveUser, Field, SettingRow, SettingsToggle,
} from './shared.jsx';
import { flushAchievementToasts, AchievementWall, AchvShareModal } from './achievements.jsx';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSInput from '@cloudscape-design/components/input';
import CSSelect from '@cloudscape-design/components/select';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSTextarea from '@cloudscape-design/components/textarea';

/* ---------------------------- ME (personal home) ----------- */
const ME_ACTIVITY = [
  { ts: "刚刚",       icon: "play",     text: "在 雾港·主线·顾承砚 进行了第 312 回合", tag: "回合" },
  { ts: "12 分钟前",  icon: "branch",   text: "从节点 #07 新建分支 旅店线·阿衡视角", tag: "分支" },
  { ts: "今天 14:08", icon: "memory",   text: "把 黑铁怀表停在三时四十二分 加入固定记忆", tag: "记忆" },
  { ts: "今天 12:30", icon: "save",     text: "导入剧本 雾港异闻录·外卷", tag: "剧本" },
  { ts: "昨天",       icon: "edit",     text: "编辑了 角色卡·沈知微 的语气", tag: "NPC 角色卡" },
  { ts: "昨天",       icon: "world",    text: "调整世界线变量 顾承砚.身份暴露度 = 37%", tag: "世界线" },
  { ts: "上周",       icon: "upload",   text: "上传 光绪十三年残页扫描.zip 到库", tag: "库" },
  { ts: "上周",       icon: "spark",    text: "部署了 Skill·时间线推演 v1.4", tag: "Skill" },
  { ts: "上月",       icon: "user",     text: "完成注册 · 成为首个管理员", tag: "账号" },
];

function MePage({ subPage = "overview" }) {
  // 顶部 概览/编辑资料/用户设置 子导航已移除 —— 与侧栏「设置 & 账户」的
  // 个人主页 / 编辑资料 / 隐私与安全 完全重复,统一交给侧栏。
  return (
    <CSSpaceBetween size="l">
      {subPage === "overview" && <MeOverview />}
      {subPage === "edit" && <MeEditProfile />}
      {subPage === "settings" && <MeUserSettings />}
    </CSSpaceBetween>
  );
}
function MeOverview() {
  const { t } = useTranslation();
  const { stats: platStats = {}, saves = [] } = usePlatformData();  // task 45：响应式 platform
  const user = useReactiveUser();  // task 13: MePage 切换 / 保存后即时更新
  const [filter, setFilter] = useStatePL("all");
  const [shareOpen, setShareOpen] = useStatePL(false);
  // task 48：原使用 ME_ACTIVITY / ME_ACHIEVEMENTS 硬编码示例（『在 雾港·主线·顾承砚
  // 进行了第 312 回合』『破雾之刻』『千言不渝』等）。后端暂无活动/成就接口，改成空态文案。
  // 匿名访客可见 mock 用作 designer offline preview。
  const IS_ANON = !(window.RPG_AUTH && window.RPG_AUTH.authed);
  // 最近活动:登录态拉真实 /api/me/activity(回合/分支/剧本),匿名用 mock 作 designer preview
  const [meActivity, setMeActivity] = useStatePL(null);
  useEffectPL(() => {
    if (IS_ANON) return;
    let cancelled = false;
    (async () => {
      try { const r = await window.api.account.activity(); if (!cancelled) setMeActivity((r && r.activity) || []); }
      catch (_) { if (!cancelled) setMeActivity([]); }
    })();
    return () => { cancelled = true; };
  }, [IS_ANON, saves.length]);
  const ACTIVITY = IS_ANON ? ME_ACTIVITY : (meActivity || []);
  // 成就在 meStats 拉到后派生(见下方 ACHIEVEMENTS)
  // task 49：之前 totalRounds = saves.reduce(* 7)、playHours = totalRounds*1.2/60 等
  // 全是凭空乘的伪派生；现在拉真后端 /api/me/stats。后端没真数据的字段（playMinutes）
  // 显式为 null，UI 显示 "—"。
  const [meStats, setMeStats] = useStatePL(null);
  useEffectPL(() => {
    if (IS_ANON) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.stats();
        if (!cancelled) setMeStats(r || null);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, [IS_ANON, saves.length]);
  const filteredActivity = filter === "all" ? ACTIVITY : ACTIVITY.filter(a => a.tag === filter);
  const fmtCN = (n) => {
    if (n == null) return "—";
    if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, "") + " 万";
    return n.toLocaleString();
  };
  // fmtDate / fmtAgo 统一到 window.__fmt(data-loader.js)。fmtAgo 原本就在
  // window.__fmt.ago 存在时直接委派(运行时总成立),此处去掉死的本地兜底。
  const fmtDate = (iso) => {
    if (window.__fmt && window.__fmt.date) return window.__fmt.date(iso);
    if (!iso) return "—";
    try { return new Date(iso).toISOString().slice(0, 10); } catch { return "—"; }
  };
  const fmtAgo = (iso) => (window.__fmt && window.__fmt.ago) ? window.__fmt.ago(iso) : "—";
  const regAt = fmtDate(user.created_at);
  const lastLoginAgo = fmtAgo(meStats?.last_login_at);
  const totalRounds = meStats?.total_rounds;
  const branchesCount = meStats?.branches ?? platStats.branches;
  const maxDepth = meStats?.max_branch_depth;
  const importedScripts = meStats?.imported?.scripts ?? platStats.scripts;
  const importedWords = meStats?.imported?.words;
  const loginStreak = meStats?.login_streak;
  const longestStreak = meStats?.longest_login_streak;
  const playMinutesTotal = meStats?.play_minutes_total;
  const playMinutesWeek = meStats?.play_minutes_week;
  const playHoursLabel = (playMinutesTotal == null) ? "—" : (playMinutesTotal / 60).toFixed(1);

  // 成就:服务端权威(见 docs/design/I_achievements.md)。
  // 登录态拉 /api/me/achievements(含进度 + 解锁时间 + newly_unlocked);
  // 匿名态拉公开目录 /api/achievements 作全锁预览。客户端不再派生。
  const [achv, setAchv] = useStatePL(null);
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        if (IS_ANON) {
          const r = await window.api.account.achievementsCatalog();
          if (!cancelled) setAchv((r && r.items) || []);
          return;
        }
        const r = await window.api.account.achievements();
        if (cancelled) return;
        const items = (r && r.items) || [];
        setAchv(items);
        flushAchievementToasts(items);  // 弹未看过的解锁(会话内去重)
      } catch (_) { if (!cancelled) setAchv([]); }
    })();
    return () => { cancelled = true; };
  }, [IS_ANON, saves.length]);
  const ACHIEVEMENTS = achv || [];
  const unlockedCount = ACHIEVEMENTS.filter(a => a.unlocked).length;
  const [overviewAvatarStudioOpen, setOverviewAvatarStudioOpen] = useStatePL(false);
  const [overviewAvatarUrl, setOverviewAvatarUrl] = useStatePL(null);
  // 实际展示 URL:MediaStudio 更新后用 overviewAvatarUrl,否则回落 user._raw?.avatar_url
  const displayAvatarUrl = overviewAvatarUrl || user._raw?.avatar_url || null;

  return (
    <CSSpaceBetween size="l">
      {/* Hero section */}
      <CSContainer>
        <CSSpaceBetween size="m">
          <CSSpaceBetween direction="horizontal" size="m">
            {overviewAvatarStudioOpen && (
              <MediaStudio
                open={overviewAvatarStudioOpen}
                onClose={() => setOverviewAvatarStudioOpen(false)}
                target={{ type: 'user_avatar' }}
                name={user.display_name || '用户'}
                defaultPrompt={user.display_name ? `${user.display_name} 的用户头像` : '用户头像'}
                onApplied={(url) => {
                  setOverviewAvatarUrl(url + '?t=' + Date.now());
                  setOverviewAvatarStudioOpen(false);
                }}
              />
            )}
            <div style={{ position: 'relative', display: 'inline-block' }}>
              <AvatarImg src={displayAvatarUrl} name={user.display_name || '?'} size={88} shape="circle" className="pl-me-avatar" zoomable />
              {!IS_ANON && (
                <button
                  onClick={() => setOverviewAvatarStudioOpen(true)}
                  title={t('platform.me.change_avatar', '更换头像')}
                  style={{
                    position: 'absolute', bottom: 0, right: 0,
                    background: 'var(--color-background-dropdown-item-default, #2a2927)',
                    border: '1px solid var(--color-border-divider-default, #444)',
                    borderRadius: '50%', width: 26, height: 26,
                    cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 13, color: 'var(--color-text-interactive-default, #e8c97a)',
                    padding: 0,
                  }}
                >✦</button>
              )}
            </div>
            <div style={{flex: 1}}>
              <CSSpaceBetween size="xs">
                <CSBox variant="h2">
                  {user.display_name}
                  <span className="pill" style={{marginLeft: 8}}><span className="dot ok pulse" /> {t('platform.me.online', '在线')}</span>
                  <span className="pill accent" style={{marginLeft: 6}}>{user.role === "admin" ? t('platform.me.role_admin', '管理员') : user.role}</span>
                </CSBox>
                <CSBox color="text-body-secondary" fontSize="body-s">
                  <span><Icon name="user" size={11} /> @{user.username}</span>
                  <span className="mono" style={{marginLeft: 12}}>uid {user.uid}</span>
                  <span style={{marginLeft: 12}}><Icon name="history" size={11} /> {t('platform.me.registered_at', { date: regAt, defaultValue: `注册于 ${regAt}` })} · {t('platform.me.last_login', { ago: lastLoginAgo, defaultValue: `上次登录 ${lastLoginAgo}` })}</span>
                </CSBox>
                <CSBox>{user.bio || t('platform.me.no_bio', '暂无简介。')}</CSBox>
              </CSSpaceBetween>
            </div>
          </CSSpaceBetween>
        </CSSpaceBetween>
      </CSContainer>

      {/* Stat row */}
      <CSContainer>
        <CSColumnLayout columns={5} variant="text-grid">
          <div>
            <CSBox variant="awsui-key-label">{t('platform.me.stat_playtime', '游玩时长')}</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">
              {playHoursLabel}{playMinutesTotal != null && <span style={{fontSize: 14, color: "var(--muted)", marginLeft: 4}}>h</span>}
            </CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{playMinutesWeek != null ? t('platform.me.stat_playtime_week', { h: (playMinutesWeek / 60).toFixed(1), defaultValue: `本周 +${(playMinutesWeek / 60).toFixed(1)}h` }) : t('platform.me.stat_no_data', '暂无统计')}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">{t('platform.me.stat_rounds', '回合数')}</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{totalRounds != null ? totalRounds.toLocaleString() : "—"}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{t('platform.me.stat_saves_count', { n: saves.length, defaultValue: `分布在 ${saves.length} 个存档` })}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">{t('platform.me.stat_branches', '创建分支')}</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{branchesCount != null ? branchesCount : "—"}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{maxDepth ? t('platform.me.stat_max_depth', { n: maxDepth, defaultValue: `最深 ${maxDepth} 层` }) : "—"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">{t('platform.me.stat_scripts', '导入剧本')}</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{importedScripts != null ? importedScripts : "—"}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{importedWords ? t('platform.me.stat_words', { n: fmtCN(importedWords), defaultValue: `共 ${fmtCN(importedWords)}字` }) : "—"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">{t('platform.me.stat_streak', '连续登录')}</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">
              {loginStreak != null ? loginStreak : "—"}<span style={{fontSize: 14, color: "var(--muted)", marginLeft: 4}}>{t('platform.me.stat_streak_unit', '天')}</span>
            </CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{longestStreak ? t('platform.me.stat_streak_longest', { n: longestStreak, defaultValue: `最长 ${longestStreak} 天` }) : "—"}</CSBox>
          </div>
        </CSColumnLayout>
      </CSContainer>

      {/* 成就(服务端权威,按类目分组) */}
      <CSContainer header={<CSHeader variant="h2"
        actions={!IS_ANON && unlockedCount > 0 && <CSButton iconName="share" onClick={() => setShareOpen(true)}>{t('platform.achv.share_btn', '分享成就')}</CSButton>}
      >{t('platform.achv.heading', '成就')} <span className="muted-2">{unlockedCount} / {ACHIEVEMENTS.length} {t('platform.achv.unlocked_label', '已解锁')}</span></CSHeader>}>
        {ACHIEVEMENTS.length === 0 ? (
          <CSBox color="text-body-secondary" textAlign="center" padding="l">
            {achv === null ? t('common.loading', '加载中…') : t('platform.achv.empty', '暂无成就。')}
          </CSBox>
        ) : (
          <AchievementWall items={ACHIEVEMENTS} />
        )}
      </CSContainer>

      {shareOpen && (
        <AchvShareModal
          user={user}
          items={ACHIEVEMENTS}
          unlockedCount={unlockedCount}
          total={ACHIEVEMENTS.length}
          publicProfile={!!(meStats && meStats.public_profile)}
          onClose={() => setShareOpen(false)}
        />
      )}

      {/* 最近活动 */}
      <CSContainer header={
        <CSHeader variant="h2" actions={
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant={filter === "all" ? "primary" : "normal"} onClick={() => setFilter("all")}>{t('common.all', '全部')}</CSButton>
            <CSButton variant={filter === "回合" ? "primary" : "normal"} onClick={() => setFilter("回合")}>{t('platform.me.activity_tag_round', '回合')}</CSButton>
            <CSButton variant={filter === "分支" ? "primary" : "normal"} onClick={() => setFilter("分支")}>{t('platform.me.activity_tag_branch', '分支')}</CSButton>
            <CSButton variant={filter === "剧本" ? "primary" : "normal"} onClick={() => setFilter("剧本")}>{t('platform.me.activity_tag_script', '剧本')}</CSButton>
          </CSSpaceBetween>
        }>{t('platform.me.recent_activity', '最近活动')}</CSHeader>
      }>
        <ol className="pl-activity">
          {filteredActivity.map((a, i) => (
            <li key={i}>
              <div className="pl-activity-rail">
                <span className="pl-activity-dot"><Icon name={a.icon} size={11} /></span>
                {i < filteredActivity.length - 1 && <span className="pl-activity-line" />}
              </div>
              <div className="pl-activity-body">
                <div className="pl-activity-text">{a.text}</div>
                {a.sub ? <div className="pl-activity-sub muted-2" style={{fontSize: 12, marginTop: 2}}>{a.sub}</div> : null}
                <div className="pl-activity-meta">
                  <span className="pill" style={{fontSize: 10.5}}>{a.tag}</span>
                  <span className="muted-2 mono" style={{fontSize: 11}}>{/^\d{4}-\d{2}-\d{2}T/.test(a.ts || "") ? fmtAgo(a.ts) : a.ts}</span>
                </div>
              </div>
            </li>
          ))}
          {filteredActivity.length === 0 && (
            <CSBox color="text-body-secondary" textAlign="center" padding="l">
              {meActivity === null && !IS_ANON
                ? t('platform.me.activity_loading', '正在加载活动…')
                : (ACTIVITY.length === 0
                    ? t('platform.me.activity_empty', '暂无活动。开始游戏、开辟分支或导入剧本后,这里会显示真实记录。')
                    : t('platform.me.activity_no_filter', '未找到此分类的活动'))}
            </CSBox>
          )}
        </ol>
      </CSContainer>
    </CSSpaceBetween>
  );
}
function MeEditProfile() {
  const { t } = useTranslation();
  // task 45：改读 reactive user（publishUser 写到 __USER_STATE，登录后是真用户）
  const user = useReactiveUser();
  const [form, setForm] = useStatePL({
    display_name: user.display_name || "",
    username: user.username || "",
    email: user._raw?.email || "",
    phone: user._raw?.phone || "",
    real_name: user._raw?.real_name || "",
    gender: user._raw?.gender || "unspecified",
    birthday: user._raw?.birthday || "",
    location: user._raw?.location || "",
    website: user._raw?.website || "",
    bio: user.bio || "",
    pronouns: user._raw?.pronouns || "",
    language: user._raw?.language || "zh-CN",
    timezone: user._raw?.timezone || "Asia/Shanghai",
  });
  // task 57: 表单输入标记 dirty,保存/重置后清掉。
  const u = (k, v) => {
    setForm(f => ({ ...f, [k]: v }));
    try { window.__capMarkDirty && window.__capMarkDirty("settings.profile"); } catch (_) {}
  };
  const [uploadOpen, setUploadOpen] = useStatePL(false);
  const [resetAvatarOpen, setResetAvatarOpen] = useStatePL(false);
  const [saving, setSaving] = useStatePL(false);
  const avatarInputRef = React.useRef(null);
  const [mediaStudioOpen, setMediaStudioOpen] = useStatePL(false);
  const [avatarUrl, setAvatarUrl] = useStatePL(user._raw?.avatar_url || null);

  // 从 /api/me/profile 拉真实资料(后端合并了 profile_extras:邮箱/手机/真名/性别/
  // 生日/所在地/网站/代词/语言/时区)。只取表单已知字段,避免把 stats 等无关键污染进 form。
  // _FORM_KEYS 已提升到模块顶层
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const p = await window.api.account.profile();
        if (cancelled) return;
        const src = (p && (p.profile || p.user)) || p || {};
        const picked = {};
        for (const k of _FORM_KEYS) if (src[k] != null) picked[k] = src[k];
        if (Object.keys(picked).length) setForm(f => ({ ...f, ...picked }));
      } catch (e) {
        if (!cancelled) window.__apiToast?.("加载资料失败,请检查网络后重试", { kind: "danger", detail: e?.message, duration: 3000 });
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // [round-4-P2] reactive user 可能在 mount 之后才就绪;form 的 useStatePL 初值只取一次,
  //   会把 display_name/username/email/bio 锁成 mount 时的空值。这里在 user 就绪后【仅填空字段】,
  //   不覆盖用户已输入或上面 profile() 已合并的值(用 `f.x || user.x` 幂等回填)。
  useEffectPL(() => {
    setForm(f => ({
      ...f,
      display_name: f.display_name || user.display_name || "",
      username: f.username || user.username || "",
      email: f.email || user._raw?.email || "",
      bio: f.bio || user.bio || "",
    }));
    if (user._raw?.avatar_url && !avatarUrl) setAvatarUrl(user._raw.avatar_url);
  }, [user.id, user.username, user.display_name]);

  const onSave = async () => {
    setSaving(true);
    try {
      await window.api.account.saveProfile(form);
      try { window.__capClearDirty && window.__capClearDirty("settings.profile"); } catch (_) {}
      // task 13: 拉一次权威源（/api/auth/me），用回包的 user 字段更新全局并广播事件，
      // 让 PlatformShell 左侧栏立即同步。失败也兜底先按本地 form 写一次（视觉上立即看到改动）。
      try {
        const me = await window.api?.auth?.me?.();
        if (me && me.user) {
          publishUser({
            id: me.user.id,
            username: me.user.username,
            display_name: me.user.display_name || form.display_name,
            role: me.user.role,
            bio: me.user.bio ?? form.bio,
          });
        } else {
          publishUser({ ...form });
        }
      } catch (_) {
        publishUser({ ...form });
      }
      window.__apiToast?.(t('platform.me.edit.saved', '已保存资料'), { kind: "ok", duration: 1600 });
    } catch (e) {
      window.__apiToast?.(t('platform.me.edit.save_failed', '保存失败'), { kind: "danger", detail: e?.message, duration: 3000 });
    } finally {
      setSaving(false);
    }
  };

  const onAvatarPick = async (file) => {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      window.__apiToast?.(t('platform.me.edit.file_too_large', '文件过大'), { kind: "danger", detail: t('platform.me.edit.max_size', '最大 2 MB') });
      return;
    }
    try {
      const res = await window.api.account.avatar(file);
      window.__apiToast?.(t('platform.me.edit.avatar_updated', '头像已更新'), { kind: "ok" });
      if (res && res.avatar_url) {
        // 更新本地 state（AvatarImg 响应式）
        setAvatarUrl(res.avatar_url + '?t=' + Date.now());
        // bust page-level avatar cache（保留兼容老代码）
        document.querySelectorAll(".pl-me-avatar.large, .pl-user-avatar").forEach(el => {
          el.style.backgroundImage = `url(${res.avatar_url}?t=${Date.now()})`;
        });
      }
      setUploadOpen(false);
    } catch (e) {
      window.__apiToast?.(t('platform.me.edit.upload_failed', '上传失败'), { kind: "danger", detail: e?.message });
    }
  };

  const onResetAvatar = async () => {
    try {
      await window.api.account.avatarReset();
      window.__apiToast?.(t('platform.me.edit.avatar_reset', '已恢复默认头像'), { kind: "ok" });
      setResetAvatarOpen(false);
    } catch (e) {
      window.__apiToast?.(t('platform.me.edit.op_failed', '操作失败'), { kind: "danger", detail: e?.message });
    }
  };

  return (
    <CSSpaceBetween size="l">
      {/* 头像 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.edit.section_avatar', '头像')}</CSHeader>}>
        <CSSpaceBetween size="m">
          {mediaStudioOpen && (
            <MediaStudio
              open={mediaStudioOpen}
              onClose={() => setMediaStudioOpen(false)}
              target={{ type: 'user_avatar' }}
              name={form.display_name || user.display_name || t('platform.me.edit.default_user', '用户')}
              defaultPrompt={form.display_name ? `${form.display_name} ${t('platform.me.edit.avatar_prompt_suffix', '的用户头像')}` : t('platform.me.edit.avatar_prompt_default', '用户头像')}
              onApplied={(url) => {
                setAvatarUrl(url + '?t=' + Date.now());
                setMediaStudioOpen(false);
              }}
            />
          )}
          <div className="pl-me-avatar-row">
            <AvatarImg
              src={avatarUrl}
              name={form.display_name || user.display_name || '?'}
              size={null}
              shape="circle"
              className="pl-me-avatar large"
            />
            <div className="pl-me-avatar-actions">
              <CSBox color="text-body-secondary" fontSize="body-s">{t('platform.me.edit.avatar_hint', '支持 PNG / JPG / WEBP，建议 512×512。最大 2 MB。')}</CSBox>
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton iconName="gen-ai" onClick={() => setMediaStudioOpen(true)}>✦ {t('platform.me.edit.change_avatar', '更换头像')}</CSButton>
                <CSButton iconName="remove" onClick={() => setResetAvatarOpen(true)}>{t('platform.me.edit.use_default', '使用默认')}</CSButton>
              </CSSpaceBetween>
            </div>
          </div>
        </CSSpaceBetween>
      </CSContainer>

      {/* 基本资料 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.edit.section_basic', '基本资料')}</CSHeader>} data-cap-anchor="settings.profile">
        <CSSpaceBetween size="l">
          <div className="pl-form-grid-2">
            <Field label={t('platform.me.edit.field_display_name', '显示名')} hint={t('platform.me.edit.field_display_name_hint', '出现在游戏和评论里')}>
              <CSInput value={form.display_name} onChange={({ detail }) => u("display_name", detail.value)} />
            </Field>
            <Field label={t('platform.me.edit.field_pronouns', '代词')}>
              <CSSelect
                selectedOption={[{value:"她/她",label:"她/她"},{value:"他/他",label:"他/他"},{value:"TA/TA",label:"TA/TA"},{value:"不公开",label:t('platform.me.edit.pronouns_private','不公开')}].find(o => o.value === form.pronouns) || null}
                options={[{value:"她/她",label:"她/她"},{value:"他/他",label:"他/他"},{value:"TA/TA",label:"TA/TA"},{value:"不公开",label:t('platform.me.edit.pronouns_private','不公开')}]}
                onChange={({ detail }) => u("pronouns", detail.selectedOption.value)}
              />
            </Field>
            <Field label={t('platform.me.edit.field_username', '用户名')} hint={t('platform.me.edit.field_username_hint', '登录用，6 个月可改一次')} required>
              <CSInput value={form.username} onChange={({ detail }) => u("username", detail.value)} />
            </Field>
            <Field label={t('platform.me.edit.field_real_name', '真实姓名')} hint={t('platform.me.edit.field_real_name_hint', '仅自己可见')}>
              <CSInput value={form.real_name} onChange={({ detail }) => u("real_name", detail.value)} />
            </Field>
            <Field label={t('platform.me.edit.field_gender', '性别')}>
              <CSSpaceBetween direction="horizontal" size="xs">
                {[{v: "female", l: t('platform.me.edit.gender_female','女')}, {v: "male", l: t('platform.me.edit.gender_male','男')}, {v: "other", l: t('platform.me.edit.gender_other','其他')}, {v: "unspecified", l: t('platform.me.edit.gender_private','不公开')}].map(o => (
                  <CSButton key={o.v} variant={form.gender === o.v ? "primary" : "normal"} onClick={() => u("gender", o.v)}>{o.l}</CSButton>
                ))}
              </CSSpaceBetween>
            </Field>
            <Field label={t('platform.me.edit.field_birthday', '生日')}>
              <CSInput type="date" value={form.birthday} onChange={({ detail }) => u("birthday", detail.value)} />
            </Field>
            <Field label={t('platform.me.edit.field_location', '所在地')}>
              <CSInput value={form.location} onChange={({ detail }) => u("location", detail.value)} placeholder={t('platform.me.edit.field_location_ph', '例：上海')} />
            </Field>
            <Field label={t('platform.me.edit.field_website', '个人网站')}>
              <CSInput value={form.website} onChange={({ detail }) => u("website", detail.value)} placeholder="https://..." />
            </Field>
          </div>
          <Field label={t('platform.me.edit.field_bio', '简介')} hint={t('platform.me.edit.field_bio_hint', '280 字以内')}>
            <CSTextarea
              rows={3}
              value={form.bio}
              onChange={({ detail }) => u("bio", detail.value)}
            />
            <CSBox color="text-body-secondary" fontSize="body-s" textAlign="right">{form.bio.length} / 280</CSBox>
          </Field>
        </CSSpaceBetween>
      </CSContainer>

      {/* 联系方式 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.edit.section_contact', '联系方式')}</CSHeader>}>
        <div className="pl-form-grid-2">
          <Field label={t('platform.me.edit.field_email', '邮箱')} hint={t('platform.me.edit.field_email_hint', '用于通知与找回密码')}>
            <CSInput value={form.email} onChange={({ detail }) => u("email", detail.value)} placeholder="you@example.com" />
          </Field>
          <Field label={t('platform.me.edit.field_phone', '手机')} hint={t('platform.me.edit.field_phone_hint', '选填，仅自己可见')}>
            <CSInput value={form.phone} onChange={({ detail }) => u("phone", detail.value)} placeholder={t('platform.me.edit.field_optional', '选填')} />
          </Field>
        </div>
      </CSContainer>

      {/* 本地化 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.edit.section_locale', '本地化')}</CSHeader>}>
        <div className="pl-form-grid-2">
          <Field label={t('platform.me.edit.field_language', '界面语言')}>
            <CSSelect
              selectedOption={[{value:"zh-CN",label:"简体中文"},{value:"zh-TW",label:"繁體中文"},{value:"en",label:"English (Beta)"},{value:"ja",label:"日本語"}].find(o => o.value === form.language) || null}
              options={[{value:"zh-CN",label:"简体中文"},{value:"zh-TW",label:"繁體中文"},{value:"en",label:"English (Beta)"},{value:"ja",label:"日本語"}]}
              onChange={({ detail }) => u("language", detail.selectedOption.value)}
            />
          </Field>
          <Field label={t('platform.me.edit.field_timezone', '时区')}>
            <CSSelect
              selectedOption={[{value:"Asia/Shanghai",label:"UTC+8 · 上海"},{value:"Asia/Tokyo",label:"UTC+9 · 东京"},{value:"UTC",label:"UTC"},{value:"America/Los_Angeles",label:"UTC-8 · 洛杉矶"}].find(o => o.value === form.timezone) || null}
              options={[{value:"Asia/Shanghai",label:"UTC+8 · 上海"},{value:"Asia/Tokyo",label:"UTC+9 · 东京"},{value:"UTC",label:"UTC"},{value:"America/Los_Angeles",label:"UTC-8 · 洛杉矶"}]}
              onChange={({ detail }) => u("timezone", detail.selectedOption.value)}
            />
          </Field>
        </div>
      </CSContainer>

      {/* 保存按钮行 */}
      <CSSpaceBetween direction="horizontal" size="xs">
        <CSButton onClick={() => plNavigate('me')}>{t('common.cancel')}</CSButton>
        <CSButton variant="primary" onClick={onSave} loading={saving}>
          {saving ? t('platform.me.edit.saving', '保存中…') : t('platform.me.edit.save_btn', '保存资料')}
        </CSButton>
      </CSSpaceBetween>

      <input ref={avatarInputRef} type="file" accept="image/png,image/jpeg,image/webp"
        style={{display: "none"}} onChange={(e) => onAvatarPick(e.target.files?.[0])} />
      <ConfirmModal
        open={uploadOpen}
        title={t('platform.me.edit.upload_title', '上传新头像')}
        body={<>{t('platform.me.edit.avatar_hint', '支持 PNG / JPG / WEBP，建议 512×512。最大 2 MB。')}</>}
        confirmLabel={t('platform.me.edit.choose_file', '选择文件')}
        onClose={() => setUploadOpen(false)}
        onConfirm={() => { avatarInputRef.current?.click(); setUploadOpen(false); }}
      />
      <ConfirmModal
        open={resetAvatarOpen}
        title={t('platform.me.edit.reset_avatar_title', '恢复为默认头像？')}
        body={<>{t('platform.me.edit.reset_avatar_body', '将删除当前头像，使用由显示名首字生成的占位头像。')}</>}
        confirmLabel={t('platform.me.edit.reset_avatar_confirm', '恢复默认')}
        onClose={() => setResetAvatarOpen(false)} onConfirm={onResetAvatar}
      />
    </CSSpaceBetween>
  );
}
function MeUserSettings() {
  const { t } = useTranslation();
  const user = useReactiveUser();
  const hasPassword = user.has_password !== false;
  // [round-3-P2] 原 useAutoSave(scope="me") + tog 只调 save(label):label 被当成 field、无 val
  //  → 走 useAutoSave 的「仅 toast 不落库」兼容分支,这些隐私开关全部只弹"已保存"却从不持久化。
  //  且 scope="me" 会把键写成 me.two_fa,与下面 loader 读取的扁平 p.two_fa 不符 → 双重失效。
  //  修:scope=null 写扁平键 + tog 传 (field, value) 真正落库。
  const save = useAutoSave(t('platform.me.settings.label', '用户设置'), null);
  const tog = (setter, field) => (v) => { setter(v); save(field, v); };
  // 初始值为 null，等后端拉取完成后再用真实值初始化，防止 mount 时以硬编码默认值覆盖已存设置
  const [twofa, setTwofa] = useStatePL(null);
  const [emailNotif, setEmailNotif] = useStatePL(null);
  const [publicProfile, setPublicProfile] = useStatePL(null);
  const [searchable, setSearchable] = useStatePL(null);
  const [shareUsage, setShareUsage] = useStatePL(null);
  const [shareCrash, setShareCrash] = useStatePL(null);
  const [adsTrack, setAdsTrack] = useStatePL(null);
  const [prefLoaded, setPrefLoaded] = useStatePL(false);

  // mount 时先从后端拉真实偏好值，再初始化各开关
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.getPreferences();
        if (cancelled) return;
        const p = r?.preferences || r || {};
        if (p.two_fa != null) setTwofa(!!p.two_fa);
        else setTwofa(true);
        if (p.email_notif != null) setEmailNotif(!!p.email_notif);
        else setEmailNotif(true);
        if (p.public_profile != null) setPublicProfile(!!p.public_profile);
        else setPublicProfile(false);
        if (p.searchable != null) setSearchable(!!p.searchable);
        else setSearchable(true);
        if (p.share_usage != null) setShareUsage(!!p.share_usage);
        else setShareUsage(false);
        if (p.share_crash != null) setShareCrash(!!p.share_crash);
        else setShareCrash(true);
        if (p.ads_track != null) setAdsTrack(!!p.ads_track);
        else setAdsTrack(false);
      } catch (_) {
        // 拉取失败：使用安全默认值
        if (!cancelled) {
          setTwofa(true); setEmailNotif(true); setPublicProfile(false);
          setSearchable(true); setShareUsage(false); setShareCrash(true); setAdsTrack(false);
        }
      } finally {
        if (!cancelled) setPrefLoaded(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);
  const [confirmDelete, setConfirmDelete] = useStatePL(false);
  const [confirmDeact, setConfirmDeact] = useStatePL(false);
  const [busyDelete, setBusyDelete] = useStatePL(false);
  const [busyDeact, setBusyDeact] = useStatePL(false);
  const [busyRevokeAll, setBusyRevokeAll] = useStatePL(false);
  const [pwOpen, setPwOpen] = useStatePL(false);
  const [sessionsOpen, setSessionsOpen] = useStatePL(false);
  const [historyOpen, setHistoryOpen] = useStatePL(false);
  const [exportOpen, setExportOpen] = useStatePL(false);
  const [visibilityOpen, setVisibilityOpen] = useStatePL(false);
  const [policyOpen, setPolicyOpen] = useStatePL(false);

  // task 49：sessions 初始值原是硬编码假行 [{device:"macOS·Chrome 134", ip:"127.0.0.1"}]，
  // 即使后端返回空也永远显示这条假记录。改为空数组 + mount 即拉真后端。
  const [sessions, setSessions] = useStatePL([]);
  const [loginHistory, setLoginHistory] = useStatePL([]);
  const [visibilitySettings, setVisibilitySettings] = useStatePL({});
  const [savesCount, setSavesCount] = useStatePL(null);

  // mount 即拉 sessions/login-history/saves count，供描述行使用真实数字
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.auth.sessionsList();
        const list = r?.sessions || r?.items || [];
        if (cancelled) return;
        setSessions(list.map(s => ({
          id: s.id || s.session_id,
          device: s.device || s.user_agent || "—",
          loc: s.location || s.loc || "—",
          ip: s.ip || s.remote_ip || "—",
          ts: window.__fmt?.ago(s.last_seen_at || s.created_at) || "—",
          last_seen_at: s.last_seen_at || s.created_at,
          current: !!s.current,
        })));
      } catch (_) {}
      try {
        const r = await window.api.auth.loginHistory();
        const list = r?.entries || r?.items || [];
        if (cancelled) return;
        setLoginHistory(list.map(s => ({
          ts: window.__fmt?.ago(s.at) || s.at,
          at: s.at,
          dev: s.user_agent || s.device || "—",
          ip: s.ip || "—",
          result: s.result || (s.ok ? "ok" : "blocked"),
        })));
      } catch (_) {}
      try {
        const r = await window.api.saves.list();
        const list = r?.items || r?.saves || [];
        if (!cancelled) setSavesCount(Array.isArray(list) ? list.length : 0);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const onChangePassword = async (vals) => {
    if (!vals?.next || vals.next !== vals.confirm) {
      window.__apiToast?.(t('platform.me.settings.pw_mismatch', '两次密码不一致'), { kind: "danger" });
      return;
    }
    try {
      await window.api.auth.changePassword({ current: vals.current, next: vals.next });
      window.__apiToast?.(t('platform.me.settings.pw_changed', '密码已修改'), { kind: "ok" });
      setPwOpen(false);
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.pw_change_failed', '修改失败'), { kind: "danger", detail: e?.message });
    }
  };

  const onRevokeSession = async (sid) => {
    try {
      await window.api.auth.sessionsRevoke(sid);
      window.__apiToast?.(t('platform.me.settings.session_revoked', '已下线'), { kind: "ok" });
      setSessions(s => s.filter(x => x.id !== sid));
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.session_revoke_failed', '下线失败'), { kind: "danger", detail: e?.message });
    }
  };

  const onRevokeAll = async () => {
    setBusyRevokeAll(true);
    try {
      await window.api.auth.revokeAllSessions();
      window.__apiToast?.(t('platform.me.settings.all_revoked', '已全部下线'), { kind: "ok" });
      setSessions(s => s.filter(x => x.current));
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.session_revoke_failed', '下线失败'), { kind: "danger", detail: e?.message });
    } finally {
      setBusyRevokeAll(false);
    }
  };

  const onExportData = async (vals) => {
    try {
      const r = await window.api.account.exportData(vals);
      window.__apiToast?.(t('platform.me.settings.export_requested', '已申请导出'), { kind: "ok", detail: r?.message || t('platform.me.settings.export_email_notice', '完成后会邮件通知') });
      setExportOpen(false);
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.export_failed', '申请失败'), { kind: "danger", detail: e?.message });
    }
  };

  const onSaveVisibility = async (vals) => {
    try {
      await window.api.account.visibility(vals || {});
      setVisibilitySettings(vals || {});
      window.__apiToast?.(t('platform.me.settings.visibility_saved', '已保存可见性'), { kind: "ok" });
      setVisibilityOpen(false);
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.save_failed', '保存失败'), { kind: "danger", detail: e?.message });
    }
  };

  const onDeactivate = async () => {
    setBusyDeact(true);
    try {
      await window.api.account.deactivate();
      window.__apiToast?.(t('platform.me.settings.deactivated', '账号已停用'), { kind: "ok" });
      setConfirmDeact(false);
      setTimeout(() => location.replace("Login.html"), 800);
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.deactivate_failed', '停用失败'), { kind: "danger", detail: e?.message });
      setBusyDeact(false);
    }
  };

  const onDeleteAccount = async () => {
    setBusyDelete(true);
    try {
      await window.api.account.deleteAccount({});
      window.__apiToast?.(t('platform.me.settings.account_deleted', '账号已删除'), { kind: "ok" });
      setConfirmDelete(false);
      setTimeout(() => location.replace("Login.html"), 800);
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.delete_failed', '删除失败'), { kind: "danger", detail: e?.message });
      setBusyDelete(false);
    }
  };

  // [round-4-P2] 移除原 7 个 useEffectPL「值变即 onSavePreference」持久化副作用:
  //   ① 与 round-3 起 tog 走 save(field,v) 真正落库形成【双写】(每次切换写两次后端);
  //   ② prefLoaded 翻 true 时 7 个 effect 全触发 → 每次进页都把 7 项偏好回写一遍(#39);
  //   ③ load() 拉取失败的 catch 设安全默认值后,effect 会把这些默认值写回后端、覆盖真实偏好(#7)。
  //   现单一持久化路径 = tog 内 save(field,v)(useAutoSave 防抖 + toast),仅用户实际切换才写。

  return (
    <CSSpaceBetween size="l" data-cap-anchor="me.settings">
      {/* 隐私 · 公开范围 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.settings.section_privacy', '隐私 · 公开范围')}</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title={t('platform.me.settings.public_profile', '公开个人主页')}
            desc={t('platform.me.settings.public_profile_desc', '开启后，其他用户可以通过 @用户名 查看你的成就墙和最近活动。')}
            control={<SettingsToggle on={publicProfile} set={tog(setPublicProfile, "public_profile")} />}
          />
          <SettingRow
            title={t('platform.me.settings.searchable', '允许搜索')}
            desc={t('platform.me.settings.searchable_desc', '允许通过显示名或用户名在平台内搜索找到你。')}
            control={<SettingsToggle on={searchable} set={tog(setSearchable, "searchable")} />}
          />
          <SettingRow
            title={t('platform.me.settings.visibility', '资料字段可见性')}
            desc={t('platform.me.settings.visibility_desc', '逐项控制谁能看到你的真实姓名、所在地、生日等。')}
            control={<CSButton onClick={() => setVisibilityOpen(true)}>{t('platform.me.settings.visibility_btn', '逐项配置')}</CSButton>}
          />
        </CSSpaceBetween>
      </CSContainer>

      {/* 数据共享 · 合规 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.settings.section_data', '数据共享 · 合规')}</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title={t('platform.me.settings.share_usage', '匿名用量统计')}
            desc={t('platform.me.settings.share_usage_desc', '把按钮点击 / 页面停留时长（不含剧本内容）匿名上报给团队，用于改进体验。')}
            control={<SettingsToggle on={shareUsage} set={tog(setShareUsage, "share_usage")} />}
          />
          <SettingRow
            title={t('platform.me.settings.share_crash', '崩溃 / 错误报告')}
            desc={t('platform.me.settings.share_crash_desc', '出现错误时上传堆栈信息和最近一次操作。剧本内容不会被上传。')}
            control={<SettingsToggle on={shareCrash} set={tog(setShareCrash, "share_crash")} />}
          />
          <SettingRow
            title={t('platform.me.settings.personalized', '个性化推荐')}
            desc={t('platform.me.settings.personalized_desc', '基于你的剧本与角色卡向你推荐 Skill 和 MCP。')}
            control={<SettingsToggle on={adsTrack} set={tog(setAdsTrack, "ads_track")} />}
          />
          <SettingRow
            title={t('platform.me.settings.gdpr', 'GDPR / 个人信息保护合规')}
            desc={t('platform.me.settings.gdpr_desc', '本平台不向第三方分享你的剧本内容、玩家变量或私聊。详见隐私政策。')}
            control={<CSButton iconName="file-open" onClick={(e) => { e.preventDefault(); setPolicyOpen(true); }}>{t('platform.me.settings.privacy_policy', '隐私政策')}</CSButton>}
          />
        </CSSpaceBetween>
      </CSContainer>

      {/* 账号 · 安全 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.settings.section_security', '账号 · 安全')}</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title={hasPassword ? t('platform.me.settings.change_password', '修改密码') : t('platform.me.settings.set_password', '设置密码')}
            desc={hasPassword ? t('platform.me.settings.change_password_desc', '建议每 90 天更换一次，至少 12 位字符 + 大小写 + 数字。') : t('platform.me.settings.set_password_desc', '当前账号通过邮箱链接登录，尚未设置密码；可直接设置一组新密码。')}
            control={<CSButton iconName="lock-private" onClick={() => setPwOpen(true)}>{hasPassword ? t('platform.me.settings.change_password', '修改密码') : t('platform.me.settings.set_password', '设置密码')}</CSButton>}
          />
          <SettingRow
            title={t('platform.me.settings.two_fa', '二次验证（2FA）')}
            desc={t('platform.me.settings.two_fa_desc', '通过 Authenticator App 或手机短信进行二次验证。')}
            control={
              <CSSpaceBetween direction="horizontal" size="xs">
                {twofa && <span className="pill ok"><span className="dot ok" /> Authenticator</span>}
                <SettingsToggle on={twofa} set={tog(setTwofa, "two_fa")} />
              </CSSpaceBetween>
            }
          />
          {(() => {
            const nSess = sessions.length;
            const cur = sessions.find(s => s.current) || sessions[0];
            const sessDesc = nSess === 0
              ? t('platform.me.settings.sessions_none', '尚未拉取活跃会话。')
              : t('platform.me.settings.sessions_desc', { n: nSess, device: cur?.device, ts: cur?.ts, defaultValue: `当前 ${nSess} 个登录会话${cur ? ` · 最近：${cur.device}${cur.ts ? " · " + cur.ts : ""}` : ""}。` });
            const cutoff = Date.now() - 30 * 86400_000;
            const okIn30d = loginHistory.filter(h => {
              if (h.result !== "ok") return false;
              try { return new Date(h.at).getTime() >= cutoff; } catch { return false; }
            }).length;
            const blocked = loginHistory.filter(h => h.result !== "ok").length;
            const histDesc = loginHistory.length === 0
              ? t('platform.me.settings.history_none', '尚未拉取登录历史。')
              : t('platform.me.settings.history_desc', { ok: okIn30d, blocked, defaultValue: `最近 30 天 ${okIn30d} 次成功登录${blocked ? `，${blocked} 次被拦截` : "，无异常 IP"}。` });
            return <>
              <SettingRow
                title={t('platform.me.settings.active_sessions', '活跃会话')}
                desc={sessDesc}
                control={<CSButton iconName="visibility-on" onClick={() => setSessionsOpen(true)}>{t('platform.me.settings.view_sessions', '查看会话')}</CSButton>}
              />
              <SettingRow
                title={t('platform.me.settings.login_history', '登录历史')}
                desc={histDesc}
                control={<CSButton iconName="status-info" onClick={() => setHistoryOpen(true)}>{t('platform.me.settings.view_history', '查看日志')}</CSButton>}
              />
            </>;
          })()}
        </CSSpaceBetween>
      </CSContainer>

      {/* 通知 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.settings.section_notif', '通知')}</CSHeader>}>
        <SettingRow
          title={t('platform.me.settings.email_notif', '邮件通知')}
          desc={t('platform.me.settings.email_notif_desc', '重要安全事件、订阅变更、长时间未登录提醒。')}
          control={<SettingsToggle on={emailNotif} set={tog(setEmailNotif, "email_notif")} />}
        />
      </CSContainer>

      {/* 数据所有权 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.settings.section_ownership', '数据所有权')}</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title={t('platform.me.settings.export_data', '导出我的数据')}
            desc={t('platform.me.settings.export_data_desc', '打包导出全部剧本、存档、记忆、库资产、用量记录。生成后通过邮件发送下载链接。')}
            control={<CSButton iconName="download" onClick={() => setExportOpen(true)}>{t('platform.me.settings.export_btn', '申请导出')}</CSButton>}
          />
          <SettingRow
            title={t('platform.me.settings.deactivate', '停用账号')}
            desc={t('platform.me.settings.deactivate_desc', '停用后无法登录，剧本和存档保留 90 天，期间可随时恢复。')}
            control={<CSButton variant="normal" onClick={() => setConfirmDeact(true)}>{t('platform.me.settings.deactivate', '停用账号')}</CSButton>}
          />
          <SettingRow
            title={t('platform.me.settings.delete_account', '永久删除账号')}
            desc={t('platform.me.settings.delete_account_desc', '立刻删除全部账号信息、剧本、存档、库资产，无法恢复。')}
            control={<CSButton variant="normal" iconName="remove" onClick={() => setConfirmDelete(true)}>{t('platform.me.settings.delete_btn', '删除账号')}</CSButton>}
          />
        </CSSpaceBetween>
      </CSContainer>

      <ConfirmModal
        open={confirmDeact}
        title={t('platform.me.settings.deactivate_confirm_title', '停用账号？')}
        body={<>{t('platform.me.settings.deactivate_confirm_body', '账号停用 90 天内可登录恢复。期间剧本与存档保留但不可访问。')}</>}
        confirmLabel={t('platform.me.settings.deactivate_btn', '停用')}
        busy={busyDeact}
        onClose={() => setConfirmDeact(false)} onConfirm={onDeactivate}
      />
      <ConfirmModal
        open={confirmDelete}
        title={t('platform.me.settings.delete_confirm_title', '永久删除账号？')}
        body={<>{t('platform.me.settings.delete_confirm_body_pre', '这会')}<strong>{t('platform.me.settings.delete_confirm_now', '立刻')}</strong>{t('platform.me.settings.delete_confirm_body_mid', '删除你的账号、剧本、存档、库资产，')}<strong>{t('platform.me.settings.delete_confirm_irreversible', '无法恢复')}</strong>{t('platform.me.settings.delete_confirm_body_post', '。删除后无法用同一邮箱再注册（30 天冷冻期）。')}</>}
        danger confirmLabel={t('platform.me.settings.delete_confirm_btn', '确认删除')}
        busy={busyDelete}
        onClose={() => setConfirmDelete(false)} onConfirm={onDeleteAccount}
      />
      <PromptModal
        open={pwOpen}
        eyebrow={t('platform.me.settings.pw_eyebrow', '修改密码')}
        title={hasPassword ? t('platform.me.settings.pw_title_change', '设置新密码') : t('platform.me.settings.pw_title_set', '设置登录密码')}
        hint="POST /api/auth/password"
        fields={[
          ...(hasPassword ? [{ key: "current", label: t('platform.me.settings.pw_current', '当前密码'), required: true, type: "password" }] : []),
          { key: "next", label: t('platform.me.settings.pw_new', '新密码'), required: true, type: "password", hint: t('platform.me.settings.pw_hint', '至少 12 位 · 大小写 + 数字') },
          { key: "confirm", label: t('platform.me.settings.pw_confirm', '确认新密码'), required: true, type: "password" },
        ]}
        submitLabel={hasPassword ? t('platform.me.settings.change_password', '修改密码') : t('platform.me.settings.set_password', '设置密码')}
        onClose={() => setPwOpen(false)}
        onConfirm={onChangePassword}
      />
      <PromptModal
        open={visibilityOpen}
        eyebrow={t('platform.me.settings.visibility', '资料字段可见性')}
        title={t('platform.me.settings.visibility_title', '逐项控制谁能看到')}
        hint="POST /api/profile/visibility · 仅影响他人查看"
        fields={[
          { key: "real_name", label: t('platform.me.edit.field_real_name', '真实姓名'), type: "select", default: "self",
            options: [{value: "self", label: t('platform.me.settings.vis_self','仅自己')}, {value: "friends", label: t('platform.me.settings.vis_friends','好友')}, {value: "public", label: t('platform.me.settings.vis_public','所有人')}] },
          { key: "gender", label: t('platform.me.edit.field_gender', '性别'), type: "select", default: "friends",
            options: [{value: "self", label: t('platform.me.settings.vis_self','仅自己')}, {value: "friends", label: t('platform.me.settings.vis_friends','好友')}, {value: "public", label: t('platform.me.settings.vis_public','所有人')}] },
          { key: "birthday", label: t('platform.me.edit.field_birthday', '生日'), type: "select", default: "self",
            options: [{value: "self", label: t('platform.me.settings.vis_self','仅自己')}, {value: "friends", label: t('platform.me.settings.vis_friends','好友')}, {value: "public", label: t('platform.me.settings.vis_public','所有人')}] },
          { key: "location", label: t('platform.me.edit.field_location', '所在地'), type: "select", default: "public",
            options: [{value: "self", label: t('platform.me.settings.vis_self','仅自己')}, {value: "friends", label: t('platform.me.settings.vis_friends','好友')}, {value: "public", label: t('platform.me.settings.vis_public','所有人')}] },
          { key: "email", label: t('platform.me.edit.field_email', '邮箱'), type: "select", default: "self",
            options: [{value: "self", label: t('platform.me.settings.vis_self','仅自己')}, {value: "friends", label: t('platform.me.settings.vis_friends','好友')}, {value: "public", label: t('platform.me.settings.vis_public','所有人')}] },
          { key: "phone", label: t('platform.me.edit.field_phone', '手机'), type: "select", default: "self",
            options: [{value: "self", label: t('platform.me.settings.vis_self','仅自己')}, {value: "friends", label: t('platform.me.settings.vis_friends','好友')}, {value: "public", label: t('platform.me.settings.vis_public','所有人')}] },
        ]}
        submitLabel={t('platform.me.settings.visibility_save', '保存可见性')}
        onClose={() => setVisibilityOpen(false)}
        onConfirm={onSaveVisibility}
      />
      <PromptModal
        open={exportOpen}
        eyebrow={t('platform.me.settings.export_eyebrow', '导出数据')}
        title={t('platform.me.settings.export_title', '选择要导出的内容')}
        hint="POST /api/account/export · 生成后通过邮件发送下载链接（链接 7 天有效）"
        fields={[
          { key: "scope", label: t('platform.me.settings.export_scope', '范围'), type: "select", default: "all",
            options: [
              { value: "all",      label: t('platform.me.settings.export_scope_all', '全部 · 剧本 · 存档 · 库 · 用量') },
              { value: "scripts",  label: t('platform.me.settings.export_scope_scripts', '仅剧本与章节') },
              { value: "saves",    label: t('platform.me.settings.export_scope_saves', '仅存档与分支') },
              { value: "library",  label: t('platform.me.settings.export_scope_library', '仅库资产') },
              { value: "usage",    label: t('platform.me.settings.export_scope_usage', '仅用量日志') },
            ] },
          { key: "format", label: t('platform.me.settings.export_format', '格式'), type: "select", default: "zip",
            options: [
              { value: "zip", label: t('platform.me.settings.export_format_zip', 'ZIP · 含 JSON + 附件') },
              { value: "json", label: t('platform.me.settings.export_format_json', 'JSON · 仅元数据') },
            ] },
          { key: "email", label: t('platform.me.settings.export_email', '接收邮箱'), required: true, default: "" },
        ]}
        submitLabel={t('platform.me.settings.export_btn', '申请导出')}
        onClose={() => setExportOpen(false)}
        onConfirm={onExportData}
      />
      {sessionsOpen && (
        <Modal
          open
          eyebrow={t('platform.me.settings.active_sessions', '活跃会话')}
          title={sessions.length === 0 ? t('platform.me.settings.sessions_empty', '暂无活跃会话') : t('platform.me.settings.sessions_title', { n: sessions.length, defaultValue: `${sessions.length} 个登录中` })}
          width={620}
          onClose={() => setSessionsOpen(false)}
          footer={<>
            <span className="muted-2" style={{fontSize: 11.5}}>POST /api/auth/sessions/revoke</span>
            <div style={{display: "flex", gap: 8}}>
              <button className="btn ghost" onClick={() => setSessionsOpen(false)}>{t('common.close', '关闭')}</button>
              <button className="btn danger" onClick={onRevokeAll} disabled={busyRevokeAll}><Icon name="close" size={12} /> {t('platform.me.settings.revoke_all', '全部下线（保留当前）')}</button>
            </div>
          </>}
        >
            <ul className="pl-session-list">
              {sessions.map((s, i) => (
                <li key={s.id || i}>
                  <div className="pl-session-dot"><Icon name={(s.device || "").includes("iOS") ? "user" : (s.device || "").includes("mac") ? "logo" : "world"} size={12} /></div>
                  <div className="pl-session-body">
                    <div>
                      <strong>{s.device}</strong>
                      {s.current && <span className="pill ok" style={{marginLeft: 6}}><span className="dot ok pulse" /> {t('platform.me.settings.session_current', '当前')}</span>}
                    </div>
                    <span className="muted-2 mono" style={{fontSize: 11}}>{s.loc} · {s.ip} · {s.ts}</span>
                  </div>
                  {!s.current && (
                    <button className="btn ghost" style={{height: 26, fontSize: 11.5}} onClick={() => onRevokeSession(s.id)}>
                      <Icon name="close" size={11} /> {t('platform.me.settings.force_logout', '强制下线')}
                    </button>
                  )}
                </li>
              ))}
            </ul>
        </Modal>
      )}
      {historyOpen && (
        <Modal
          open
          eyebrow={t('platform.me.settings.login_history_eyebrow', '登录日志')}
          title={t('platform.me.settings.login_history_title', { n: loginHistory.length, defaultValue: `最近登录 · ${loginHistory.length} 次` })}
          width={640}
          onClose={() => setHistoryOpen(false)}
          footer={<>
            <span className="muted-2" style={{fontSize: 11.5}}>GET /api/auth/login-history</span>
            <div style={{display: "flex", gap: 8}}>
              <button className="btn ghost" onClick={() => setHistoryOpen(false)}>{t('common.close', '关闭')}</button>
              <button className="btn ghost" onClick={() => {
                const url = window.api.base + "/api/v1/auth/login-history?format=csv";
                window.open(url, "_blank");
              }}><Icon name="download" size={12} /> {t('platform.me.settings.export_csv', '导出 CSV')}</button>
            </div>
          </>}
        >
            <ul className="pl-session-list">
              {loginHistory.length === 0 ? (
                <li className="muted" style={{padding: 16, textAlign: "center"}}>{t('platform.me.settings.history_empty', '暂无记录')}</li>
              ) : loginHistory.map((r, i) => (
                <li key={i} className="pl-history-row">
                  <span className="mono muted-2" style={{fontSize: 11, width: 92}}>{r.ts}</span>
                  <span style={{fontSize: 12.5, flex: 1, minWidth: 0}}>{r.dev}</span>
                  <span className="mono muted-2" style={{fontSize: 11}}>{r.ip}</span>
                  {r.result === "ok" ? (
                    <span className="pill ok" style={{fontSize: 10.5}}><span className="dot ok" /> {t('platform.me.settings.login_ok', '成功')}</span>
                  ) : (
                    <span className="pill danger" style={{fontSize: 10.5}}><span className="dot danger" /> {t('platform.me.settings.login_blocked', '已拦截')}</span>
                  )}
                </li>
              ))}
            </ul>
        </Modal>
      )}
      {policyOpen && (
        <Modal
          open
          eyebrow={t('platform.me.settings.policy_eyebrow', '隐私政策摘要')}
          title={t('platform.me.settings.policy_title', '我们如何处理你的数据')}
          width={680}
          onClose={() => setPolicyOpen(false)}
          footer={<>
            <a className="muted" style={{fontSize: 12}} href="#" onClick={(e) => e.preventDefault()}>{t('platform.me.settings.policy_full_link', '查看完整政策（外链）')}</a>
            <button className="btn primary" onClick={() => setPolicyOpen(false)}>{t('platform.me.settings.policy_read', '我已阅读')}</button>
          </>}
        >
            <div style={{fontSize: 13, lineHeight: 1.7, color: "var(--text-quiet)", maxHeight: 360, overflow: "auto"}}>
              <p><strong>{t('platform.me.settings.policy_p1_title', '1. 我们收集什么')}</strong>：{t('platform.me.settings.policy_p1_body', '账号信息（用户名、邮箱、可选手机）、设备指纹（用于会话）、用量遥测（仅在你开启时）。')}</p>
              <p><strong>{t('platform.me.settings.policy_p2_title', '2. 我们 不 收集什么')}</strong>：{t('platform.me.settings.policy_p2_body', '剧本正文、玩家变量、私聊、长期记忆、世界书条目——这些数据加密存储在你的工作区，团队 无 任何访问。')}</p>
              <p><strong>{t('platform.me.settings.policy_p3_title', '3. 与第三方')}</strong>：{t('platform.me.settings.policy_p3_body', '不向第三方分享剧本内容。模型 API 调用按你配置直接发往对应厂商（OpenAI / Anthropic 等），团队 不 代理也 不 留存。')}</p>
              <p><strong>{t('platform.me.settings.policy_p4_title', '4. 数据所有权')}</strong>：{t('platform.me.settings.policy_p4_body', '你可以随时通过『导出我的数据』申请完整归档；可随时『停用账号』（90 天保留）或『永久删除』（立刻执行）。')}</p>
              <p><strong>{t('platform.me.settings.policy_p5_title', '5. 合规')}</strong>：{t('platform.me.settings.policy_p5_body', '本平台符合 GDPR · 中国《个人信息保护法》· 加州 CCPA。')}</p>
            </div>
        </Modal>
      )}
    </CSSpaceBetween>
  );
}

export { MePage };
