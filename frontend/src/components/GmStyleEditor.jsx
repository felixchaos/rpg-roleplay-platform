/**
 * GmStyleEditor — GM 叙事「倾向性」6 滑块编辑器(线性 0-100)。
 * scope='user'  → 写用户级默认(window.api.me.setGmStyle)
 * scope='script'→ 写剧本级(window.api.scripts.setGmStyle(scriptId)),仅 owner 可写。
 *
 * 后端:agents/gm/style_harness 的 6 旋钮。滑块值 0-100 确定性映射到 GM 提示词片段。
 * 读现值用后端 normalize_profile 兜底(缺的旋钮取默认),不依赖前端硬编码默认。
 */
import React from 'react';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSAlert from '@cloudscape-design/components/alert';

// 6 旋钮的中文标签 + 说明 + 两端语义(low ↔ high)。顺序即展示顺序。
const KNOBS = [
  { key: 'reply_length',        label: '篇幅',     lo: '精简', hi: '铺陈',
    desc: 'GM 每轮正文的长度。越低越短促克制,越高越长、越多场景与细节。' },
  { key: 'player_action_focus', label: '镜头焦点', lo: '对方反应', hi: '细写你的动作',
    desc: '正文以谁为主体。越低越聚焦对方 NPC 与世界的反应(你的动作一笔带过);越高越细致描摹你这一动作本身。' },
  { key: 'drama_density',       label: '戏剧密度', lo: '镜像你', hi: '主动放大',
    desc: '越低越贴着你这一轮输入的强度(你平淡它也平淡);越高越主动加冲突、转折与张力。' },
  { key: 'interiority',         label: '心理补写', lo: '只写外在', hi: '多写内心',
    desc: '越低只描写外部可见的动作、神态、话语;越高越多补 NPC 的内心活动与潜台词。' },
  { key: 'cliffhanger',         label: '悬念',     lo: '平稳收束', hi: '强钩子',
    desc: '结尾的张力。越低越平稳收束;越高越爱用迫近的危机或未尽之言把你拽进下一轮。' },
  { key: 'guidance_force',      label: '引导力度', lo: '高自由', hi: '强推进',
    desc: '越低越跟着你自由发挥;越高 GM 越主动推进剧情、往原著关键节点引导。' },
];

export default function GmStyleEditor({ scope = 'user', scriptId = null, canWrite = true }) {
  const [vals, setVals] = React.useState(null);     // {key: 0-100}
  const [base, setBase] = React.useState(null);     // 加载时快照,用于「是否有改动」
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [err, setErr] = React.useState('');
  const [okMsg, setOkMsg] = React.useState('');

  const load = React.useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const r = scope === 'script'
        ? await window.api.scripts.getGmStyle(scriptId)
        : await window.api.me.getGmStyle();
      const gs = (r && r.gm_style) || {};
      setVals(gs); setBase(gs);
    } catch (e) {
      setErr(e?.message || '读取失败');
    } finally { setLoading(false); }
  }, [scope, scriptId]);

  React.useEffect(() => { load(); }, [load]);

  const dirty = vals && base && KNOBS.some((k) => vals[k.key] !== base[k.key]);

  const setOne = (key, v) => {
    setOkMsg('');
    setVals((p) => ({ ...p, [key]: Math.max(0, Math.min(100, parseInt(v, 10) || 0)) }));
  };

  const save = async () => {
    setSaving(true); setErr(''); setOkMsg('');
    try {
      const patch = {};
      KNOBS.forEach((k) => { patch[k.key] = vals[k.key]; });
      const r = scope === 'script'
        ? await window.api.scripts.setGmStyle(scriptId, patch)
        : await window.api.me.setGmStyle(patch);
      const saved = (r && r.gm_style) || patch;
      // 后端可能返回部分键,合并回完整 vals
      setVals((p) => ({ ...p, ...saved })); setBase((p) => ({ ...p, ...saved }));
      setOkMsg('已保存。' + (scope === 'script' ? '本剧本' : '你的默认') + '风格已更新,下一轮 GM 即按此生效。');
    } catch (e) {
      setErr(e?.message || '保存失败');
    } finally { setSaving(false); }
  };

  const reset = () => { setVals(base); setOkMsg(''); };

  if (loading) return <CSBox color="text-body-secondary" padding="m">正在读取叙事风格…</CSBox>;

  return (
    <CSSpaceBetween size="m">
      <CSHeader
        variant="h3"
        description={scope === 'script'
          ? '只对本剧本生效,覆盖你的个人默认。留默认即沿用平台默认行为。'
          : '你的全局默认 GM 风格,所有未单独设置的剧本都用它。剧本级 / 存档级设置会覆盖它。'}
        actions={canWrite ? (
          <CSSpaceBetween direction="horizontal" size="xs">
            {dirty && <CSButton onClick={reset} disabled={saving}>还原</CSButton>}
            <CSButton variant="primary" onClick={save} loading={saving} disabled={!dirty}>保存</CSButton>
          </CSSpaceBetween>
        ) : undefined}
      >叙事风格(线性可调)</CSHeader>

      {err && <CSAlert type="error" header="出错了">{err}</CSAlert>}
      {okMsg && <CSAlert type="success" dismissible onDismiss={() => setOkMsg('')}>{okMsg}</CSAlert>}
      {!canWrite && <CSAlert type="info">只有剧本作者能修改本剧本的叙事风格。</CSAlert>}

      <div style={{ display: 'grid', gap: 18 }}>
        {KNOBS.map((k) => (
          <div key={k.key}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
              <strong style={{ fontSize: 14 }}>{k.label}</strong>
              <span style={{ fontSize: 12, color: 'var(--text-quiet, #9a948c)', fontVariantNumeric: 'tabular-nums' }}>{vals?.[k.key] ?? 0}</span>
            </div>
            <input
              type="range" min={0} max={100} step={5}
              value={vals?.[k.key] ?? 0}
              disabled={!canWrite || saving}
              onChange={(e) => setOne(k.key, e.target.value)}
              style={{ width: '100%', accentColor: 'var(--accent, #c96442)', cursor: canWrite ? 'pointer' : 'not-allowed' }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--muted, #b8b2a8)' }}>
              <span>{k.lo}</span><span>{k.hi}</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-quiet, #9a948c)', marginTop: 3, lineHeight: 1.5 }}>{k.desc}</div>
          </div>
        ))}
      </div>
    </CSSpaceBetween>
  );
}
