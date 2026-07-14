// 成就墙 / 分享 / 公开墙 + 解锁 toast 助手。纯机械从 platform-app.jsx 搬出,零行为变化。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import { plNavigate } from '../../router.js';
import AvatarImg from '../AvatarImg.jsx';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSModal from '@cloudscape-design/components/modal';
import CSSpaceBetween from '@cloudscape-design/components/space-between';

const __achvToasted = new Set();
async function flushAchievementToasts(items) {
  const fresh = (items || []).filter(a => a && a.unlocked && a.seen === false && !__achvToasted.has(a.id));
  if (!fresh.length) return;
  fresh.forEach(a => {
    __achvToasted.add(a.id);
    window.toast(`🏆 解锁成就:${a.name}`, { kind: "ok", detail: a.desc, duration: 4200 });
  });
  try { await window.api.account.achievementsSeen(); } catch (_) {}
}
// ── 成就墙(个人主页 + 公开墙共用) ─────────────────────────────────
const ACHV_CAT_ORDER = ["启程", "叙事", "探索", "收藏", "坚持", "隐藏"];
const TIER_RANK = { gold: 3, silver: 2, bronze: 1 };
function fmtAchvDate(iso) {
  if (!iso) return "";
  try { return new Date(iso).toISOString().slice(0, 10); } catch { return ""; }
}

function AchievementWall({ items }) {
  const { t } = useTranslation();
  const groups = (() => {
    const m = new Map();
    (items || []).forEach(a => { if (!m.has(a.category)) m.set(a.category, []); m.get(a.category).push(a); });
    return [...m.keys()]
      .sort((x, y) => (ACHV_CAT_ORDER.indexOf(x) < 0 ? 99 : ACHV_CAT_ORDER.indexOf(x)) - (ACHV_CAT_ORDER.indexOf(y) < 0 ? 99 : ACHV_CAT_ORDER.indexOf(y)))
      .map(k => [k, m.get(k)]);
  })();
  return (
    <CSSpaceBetween size="l">
      {groups.map(([cat, list]) => (
        <CSSpaceBetween size="xs" key={cat}>
          <CSBox variant="awsui-key-label">{cat} <span className="muted-2">{list.filter(a => a.unlocked).length}/{list.length}</span></CSBox>
          <CSColumnLayout columns={4} variant="text-grid">
            {list.map(a => (
              <div key={a.id} className={`pl-achv ${a.unlocked ? "unlocked" : "locked"}${a.tier ? " tier-" + a.tier : ""}`}>
                <div className="pl-achv-mark">
                  {a.icon ? <span style={{fontSize: 16}}>{a.icon}</span> : <Icon name={a.unlocked ? "check" : "lock"} size={a.unlocked ? 16 : 14} />}
                </div>
                <div className="pl-achv-body">
                  <strong>{a.name}</strong>
                  <span className="pl-achv-desc muted">{a.desc}</span>
                  {a.unlocked ? (
                    <span className="muted-2 mono" style={{fontSize: 10.5}}>
                      {a.unlocked_at ? t('platform.achv.unlocked_at', { date: fmtAchvDate(a.unlocked_at), defaultValue: `解锁于 ${fmtAchvDate(a.unlocked_at)}` }) : t('platform.achv.achieved', '✓ 已达成')}
                      {a.rarity != null && t('platform.achv.rarity', { pct: a.rarity, defaultValue: ` · ${a.rarity}% 玩家解锁` })}
                    </span>
                  ) : (
                    <div className="pl-achv-progress">
                      <div className="pl-achv-bar"><div className="pl-achv-fill" style={{width: (a.pct || 0) + "%"}} /></div>
                      <span className="muted-2 mono" style={{fontSize: 10.5}}>
                        {a.target != null ? `${Number(a.value || 0).toLocaleString()} / ${Number(a.target || 0).toLocaleString()}` : `${a.pct || 0}%`}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </CSColumnLayout>
        </CSSpaceBetween>
      ))}
    </CSSpaceBetween>
  );
}

function AchvShareModal({ user, items, unlockedCount, total, onClose }) {
  const { t } = useTranslation();
  const username = (user && user.username) || "";
  const wallUrl = `${location.origin}/wall?u=${encodeURIComponent(username)}`;
  const top = (items || []).filter(a => a.unlocked)
    .sort((a, b) => (TIER_RANK[b.tier] || 0) - (TIER_RANK[a.tier] || 0))
    .slice(0, 6);
  const copy = async () => {
    try { await navigator.clipboard.writeText(wallUrl); window.toast(t('platform.achv.link_copied', '链接已复制'), { kind: "ok" }); }
    catch (_) { window.toast(t('platform.achv.copy_failed', '复制失败，请手动复制'), { kind: "warn" }); }
  };
  return (
    <CSModal visible onDismiss={onClose} header={t('platform.achv.share_title', '分享成就')}
      footer={<CSBox float="right"><CSSpaceBetween direction="horizontal" size="xs">
        <CSButton variant="link" onClick={onClose}>{t('common.close')}</CSButton>
        <CSButton onClick={() => { onClose(); plNavigate('wall', { search: '?u=' + encodeURIComponent(username) }); }}>{t('platform.achv.view_wall', '查看我的公开墙')}</CSButton>
        <CSButton variant="primary" iconName="copy" onClick={copy}>{t('platform.achv.copy_link', '复制链接')}</CSButton>
      </CSSpaceBetween></CSBox>}>
      <CSSpaceBetween size="m">
        <div className="pl-achv-share-card">
          <div className="pl-achv-share-head">
            <AvatarImg src={user._raw?.avatar_url || null} name={user.display_name || '?'} size={40} shape="circle" className="pl-achv-share-avatar" />
            <div>
              <strong>{user.display_name}</strong>
              <div className="muted-2" style={{ fontSize: 12 }}>{t('platform.achv.unlocked_count', { unlocked: unlockedCount, total, defaultValue: `解锁 ${unlockedCount} / ${total} 成就` })}</div>
            </div>
          </div>
          <div className="pl-achv-share-grid">
            {top.map(a => (
              <div key={a.id} className={`pl-achv-chip tier-${a.tier || 'bronze'}`} title={a.desc}>
                <span style={{ fontSize: 18 }}>{a.icon || "🏆"}</span>
                <span className="pl-achv-chip-name">{a.name}</span>
              </div>
            ))}
          </div>
        </div>
        <CSBox color="text-body-secondary" fontSize="body-s">
          {t('platform.achv.wall_link_hint', '公开成就墙链接(需在「设置 → 隐私」开启「公开个人主页」后，他人方可访问):')}
          <div className="mono" style={{ marginTop: 4, wordBreak: "break-all", fontSize: 12 }}>{wallUrl}</div>
        </CSBox>
      </CSSpaceBetween>
    </CSModal>
  );
}

export function PublicAchievementsPage() {
  const { t } = useTranslation();
  const [data, setData] = useStatePL(null);
  const [err, setErr] = useStatePL(null);
  const username = (() => { try { return new URLSearchParams(location.search).get("u") || ""; } catch { return ""; } })();
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      if (!username) { setErr(t('platform.achv.missing_username', '缺少用户名')); return; }
      try { const r = await window.api.account.publicWall(username); if (!cancelled) setData(r); }
      catch (e) { if (!cancelled) setErr((e && e.message) || t('platform.achv.load_failed', '加载失败')); }
    })();
    return () => { cancelled = true; };
  }, [username]);
  if (err) {
    const notFound = err === "not found" || /404|not found/i.test(err);
    return <CSContainer><CSBox textAlign="center" color="text-body-secondary" padding="xxl">
      {notFound ? t('platform.achv.wall_not_found', '该用户未公开成就墙，或不存在。') : err}
    </CSBox></CSContainer>;
  }
  if (!data) return <CSContainer><CSBox textAlign="center" padding="xxl">{t('common.loading', '加载中…')}</CSBox></CSContainer>;
  const items = data.items || [];
  return (
    <CSSpaceBetween size="l">
      <CSContainer>
        <CSSpaceBetween direction="horizontal" size="m">
          <AvatarImg src={data.avatar_url || null} name={data.display_name || data.username || '?'} size={56} shape="circle" className="pl-achv-share-avatar lg" />
          <div>
            <CSBox variant="h2">{data.display_name || data.username}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">@{data.username} · {t('platform.achv.unlocked_count', { unlocked: data.unlocked_count, total: data.total, defaultValue: `解锁 ${data.unlocked_count} / ${data.total} 成就` })}</CSBox>
          </div>
        </CSSpaceBetween>
      </CSContainer>
      <CSContainer header={<CSHeader variant="h2">{t('platform.achv.wall_heading', '成就墙')}</CSHeader>}>
        <AchievementWall items={items} />
      </CSContainer>
    </CSSpaceBetween>
  );
}

export { AchievementWall, AchvShareModal, flushAchievementToasts };
