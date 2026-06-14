/* MobileSettings.jsx — 移动端设置页(单文件,内部 section 状态切换)
   覆盖路由: settings / settings-models / settings-modelparams / settings-modules
            / settings-memory / settings-permissions / settings-account / settings-danger
   铁律:零 Cloudscape / 零电脑端 UI 复用;数据层全接 window.api.* 真实接口。
   ──────────────────────────────────────────────────────────────────────── */
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Icon } from '../icons.jsx';
import { usePlatformData, useReactiveUser } from '../../platform-app.jsx';

/* ── 工具函数 ────────────────────────────────────────────────────── */
const API_ID_ALIASES = {
  OpenAI:'openai', OpenRouter:'openrouter', DeepSeek:'deepseek',
  Anthropic:'anthropic', AlibabaQwen:'dashscope', DashScope:'dashscope',
  TencentHunyuan:'hunyuan', Hunyuan:'hunyuan', XiaomiMimo:'xiaomi_mimo',
  MiMo:'xiaomi_mimo', SiliconFlow:'siliconflow', MiniMax:'minimax',
  Doubao:'doubao', AgentPlatform:'AgentPlatform', agent_platform:'AgentPlatform',
  vertex:'AgentPlatform', vertex_ai:'AgentPlatform',
};
function normId(id) {
  const v = String(id||'').trim();
  return API_ID_ALIASES[v] || API_ID_ALIASES[v.toLowerCase()] || v;
}
function credId(apiId) { return apiId==='vertex_ai'?'AgentPlatform':normId(apiId); }
function catId(apiId) { const n=normId(apiId); return n==='AgentPlatform'?'vertex_ai':n; }

function fmtCtx(n) {
  if (!n) return '—';
  if (n>=1_000_000) return `${(n/1_000_000).toFixed(0)}M`;
  if (n>=1_000) return `${(n/1_000).toFixed(0)}K`;
  return String(n);
}

/* ── 可复用小件 ─────────────────────────────────────────────────── */
function Toggle({ on, onChange }) {
  return (
    <button
      className={'pl-toggle' + (on ? ' on' : '')}
      onClick={() => onChange(!on)}
      role="switch"
      aria-checked={on}
    />
  );
}

function SetRow({ label, desc, children }) {
  return (
    <div className="pl-setrow">
      <div className="pl-setrow-tx">
        <strong>{label}</strong>
        {desc && <span>{desc}</span>}
      </div>
      <div style={{ flex: 'none', maxWidth: '52%' }}>{children}</div>
    </div>
  );
}

function SetGroup({ title, children, action }) {
  return (
    <div className="pl-sec">
      <div className="pl-sec-head">
        <h2>{title}</h2>
        {action}
      </div>
      <div className="pl-group">{children}</div>
    </div>
  );
}

function MField({ label, desc, children }) {
  return (
    <div className="pl-field">
      <label>{label}</label>
      {desc && <span className="desc">{desc}</span>}
      {children}
    </div>
  );
}

function MSlider({ label, desc, value, min, max, step, onChange }) {
  const decimals = step < 1 ? (String(step).split('.')[1]||'').length : 0;
  return (
    <div className="pl-field">
      <div className="pl-slider-head">
        <span className="lab">{label}</span>
        <span className="val">{Number(value).toFixed(decimals)}</span>
      </div>
      {desc && <span className="desc" style={{ fontSize: 11, color: 'var(--muted-2)', marginBottom: 6, display: 'block' }}>{desc}</span>}
      <input
        className="pl-slider"
        type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

function Seg({ options, value, onChange }) {
  return (
    <div className="pl-seg2">
      {options.map(([id, label]) => (
        <button
          key={id}
          className={value === id ? 'active accent' : ''}
          onClick={() => onChange(id)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/* 存入 user_preferences 的自动保存 helper — 直接 POST 用户偏好 */
function usePrefSave(namespace) {
  const timerRef = useRef(null);
  const pendRef = useRef({});
  const save = useCallback((key, value) => {
    const fullKey = namespace ? `${namespace}.${key}` : key;
    pendRef.current[fullKey] = value;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      const batch = pendRef.current;
      pendRef.current = {};
      try {
        await window.api.account.preferences(batch);
      } catch (_) {}
    }, 400);
  }, [namespace]);
  return save;
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 偏好 (preferences)                                        */
/* ────────────────────────────────────────────────────────────────── */
function PrefSection({ nav }) {
  const save = usePrefSave('pref');
  const [lang, setLang] = useState('zh-CN');
  const [serif, setSerif] = useState(true);
  const [auto, setAuto] = useState(true);
  const [blackSwan, setBlackSwan] = useState(false);
  const [threshold, setThreshold] = useState(0.5);
  const saveCurator = usePrefSave('curator');
  const saveBS = usePrefSave('black_swan');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        if (p['pref.ui_language']) setLang(p['pref.ui_language']);
        if (typeof p['pref.serif'] === 'boolean') setSerif(p['pref.serif']);
        if (typeof p['pref.autosave'] === 'boolean') setAuto(p['pref.autosave']);
        if (typeof p['black_swan.enabled'] === 'boolean') setBlackSwan(p['black_swan.enabled']);
        const raw = p['curator.confidence_threshold'];
        if (raw !== undefined && raw !== null) {
          const v = Number(raw);
          if (Number.isFinite(v)) setThreshold(Math.max(0, Math.min(1, v)));
        }
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const commitThreshold = (v) => {
    const n = Math.max(0, Math.min(1, Math.round(Number(v) * 20) / 20));
    setThreshold(n);
    saveCurator('confidence_threshold', n);
  };

  return (
    <>
      {/* 界面偏好 */}
      <SetGroup title="界面偏好">
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>界面语言</strong>
            <span>重新加载后生效</span>
          </div>
          <div className="pl-seg2" style={{ marginLeft: 'auto', flexShrink: 0, width: 170 }}>
            {[['zh-CN','简体'],['zh-TW','繁體'],['en','EN']].map(([id, l]) => (
              <button key={id} className={lang===id?'active accent':''} onClick={() => {
                setLang(id); save('ui_language', id);
                import('../../i18n/index.js').then(m => m.changeLanguage(id)).catch(() => {});
              }}>{l}</button>
            ))}
          </div>
        </div>
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>衬线字体</strong>
            <span>叙事正文使用衬线(宋体风格)</span>
          </div>
          <Toggle on={serif} onChange={(v) => { setSerif(v); save('serif', v); }} />
        </div>
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>自动保存</strong>
            <span>每轮 GM 响应后自动保存存档</span>
          </div>
          <Toggle on={auto} onChange={(v) => { setAuto(v); save('autosave', v); }} />
        </div>
      </SetGroup>

      {/* GM 叙事风格 */}
      <div className="pl-sec" style={{ marginTop: 18 }}>
        <div className="pl-sec-head"><h2>GM 叙事风格</h2></div>
        <button className="pl-row" onClick={() => nav.toast('GM 风格编辑器暂未移植至移动端，请使用电脑版', 'warn', 'sparkle')}>
          <span className="pl-row-ic accent"><Icon name="sparkle" size={18} /></span>
          <span className="pl-row-tx"><strong>自定义文风 / 视角 / 节奏</strong><span>点击前往电脑端编辑器</span></span>
          <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
        </button>
      </div>

      {/* 黑天鹅事件 */}
      <SetGroup title="黑天鹅事件代理">
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>启用黑天鹅</strong>
            <span>低概率主动触发意外世界事件(未设置则沿用环境变量默认)</span>
          </div>
          <Toggle on={blackSwan} onChange={(v) => { setBlackSwan(v); saveBS('enabled', v); }} />
        </div>
      </SetGroup>

      {/* 叙事提取器 */}
      <SetGroup title="叙事提取器(Extractor)">
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>提取器模型</strong>
            <span>在「模块分配」中单独配置叙事提取器模型</span>
          </div>
          <button
            style={{ fontSize: 11.5, color: 'var(--accent)', background: 'none', border: 'none' }}
            onClick={() => nav.go('settings-modules')}
          >
            去配置 <Icon name="chevron_right" size={13} />
          </button>
        </div>
      </SetGroup>

      {/* Curator 反问阈值 */}
      <div className="pl-sec" style={{ marginTop: 18 }}>
        <div className="pl-sec-head"><h2>Curator 反问阈值</h2></div>
        <div className="pl-card" style={{ border: '1px solid var(--line-soft)', borderRadius: 14, background: 'var(--panel)', padding: 14 }}>
          <MSlider
            label="置信度阈值"
            desc="低于此值时 GM 会反问玩家确认意图；0 = 从不反问，1 = 总是反问"
            value={threshold}
            min={0} max={1} step={0.05}
            onChange={(v) => setThreshold(v)}
          />
          <div style={{ textAlign: 'right', marginTop: 4 }}>
            <button
              className="pl-btn-ghost"
              style={{ height: 36, fontSize: 13, width: 'auto', paddingInline: 16 }}
              onTouchEnd={() => commitThreshold(threshold)}
              onClick={() => commitThreshold(threshold)}
            >
              <Icon name="save" size={14} /> 保存
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

/* 供应商详情子视图 */
function ProviderDetail({ api, onBack, onSync, onToggleModel, onDeleteKey, nav }) {
  const [showModels, setShowModels] = useState(true);
  const conn = api.connectivity || {};
  const enabledCount = api.models.filter(m => m.enabled).length;

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title">
          <strong>{api.name}</strong>
          <span className="sub">供应商 · BYOK</span>
        </div>
        <div className="pl-head-actions">
          <button className="pl-headbtn" onClick={onSync} title="同步模型"><Icon name="refresh" size={17} /></button>
          <button className="pl-headbtn" onClick={onDeleteKey} title="删除密钥" style={{ color: 'var(--danger)' }}><Icon name="trash" size={17} /></button>
        </div>
      </div>
      <div className="pl-body">
        <div className="pl-pad">
          {/* 供应商信息 */}
          <div className="pl-card" style={{ marginBottom: 16 }}>
            <div style={{ display: 'grid', gap: 8, fontSize: 13 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>Provider ID</span>
                <span className="mono" style={{ color: 'var(--text-quiet)' }}>{api.id}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>Base URL</span>
                <span className="mono" style={{ color: 'var(--text-quiet)', fontSize: 11, maxWidth: '60%', textAlign: 'right', wordBreak: 'break-all' }}>{api.base_url || '—'}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>API Key</span>
                <span className="mono" style={{ color: 'var(--text-quiet)' }}>•••• {api.key_hint}</span>
              </div>
              {api.enabled===false && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--muted)' }}>状态</span>
                  <span style={{ color: 'var(--muted-2)', fontSize: 12, fontWeight: 600 }}>已禁用</span>
                </div>
              )}
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>连通性</span>
                <span style={{
                  color: conn.status==='ok' ? 'var(--ok)' : conn.status==='err' ? 'var(--danger)' : 'var(--muted)',
                  fontSize: 12
                }}>
                  {conn.status==='ok' ? `✓ 正常${conn.latency_ms ? ` · ${conn.latency_ms}ms` : ''}` :
                   conn.status==='err' ? '✗ 错误' :
                   conn.status==='checking' ? '同步中…' : '未测试'}
                </span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>模型数</span>
                <span style={{ color: 'var(--text-quiet)' }}>{enabledCount} / {api.models.length} 已启用</span>
              </div>
            </div>
          </div>

          {/* 模型列表 */}
          <div className="pl-sec">
            <div className="pl-sec-head">
              <h2>模型 · {api.models.length}</h2>
              <button className="act" onClick={() => setShowModels(v => !v)}>
                {showModels ? '收起' : '展开'} <Icon name={showModels ? 'chevron_up' : 'chevron_down'} size={13} />
              </button>
            </div>
            {showModels && (
              <div className="pl-group">
                {api.models.length === 0 && (
                  <div style={{ padding: '18px 14px', color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>
                    点右上角刷新按钮同步模型
                  </div>
                )}
                {api.models.map(m => (
                  <div key={m.id} className="pl-setrow">
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 'none' }}>
                      <span style={{
                        width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                        background: m.health==='ok' ? 'var(--ok)' : m.health==='err' ? 'var(--danger)' : 'var(--muted-3)'
                      }} />
                    </div>
                    <div className="pl-setrow-tx">
                      <strong style={{ fontSize: 13 }}>{m.display}</strong>
                      <span className="mono">{m.real_name}</span>
                    </div>
                    <Toggle on={m.enabled} onChange={() => onToggleModel(m.id)} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 模型参数 (modelparams)                                     */
/* ────────────────────────────────────────────────────────────────── */
const MP_DEFAULTS = {
  temperature: 0.78, top_p: 0.92, top_k: 40,
  repetition_penalty: 1.15, frequency_penalty: 0.20, presence_penalty: 0.10,
  max_tokens: 4096, context_size: 16384, seed: -1,
  mirostat_mode: 'off', mirostat_tau: 5.0, mirostat_eta: 0.10, stop: '',
};
const MP_PRESETS = {
  conservative: { temperature:0.4, top_p:0.85, repetition_penalty:1.05, frequency_penalty:0.1, presence_penalty:0.0 },
  balanced:     { temperature:0.78, top_p:0.92, repetition_penalty:1.15, frequency_penalty:0.2, presence_penalty:0.1 },
  creative:     { temperature:1.0, top_p:0.98, repetition_penalty:1.2, frequency_penalty:0.3, presence_penalty:0.2 },
  deterministic:{ temperature:0.1, top_p:0.5, repetition_penalty:1.0, frequency_penalty:0.0, presence_penalty:0.0 },
};

function readPref(prefs, key, fallback) {
  for (const k of [`settings.${key}`, key]) {
    if (prefs && Object.prototype.hasOwnProperty.call(prefs, k)) return prefs[k];
  }
  return fallback;
}
function readNumPref(prefs, key, fallback) {
  const v = Number(readPref(prefs, key, fallback));
  return Number.isFinite(v) ? v : fallback;
}

function ModelParamsSection() {
  const save = usePrefSave('settings');
  const [preset, setPreset] = useState('balanced');
  const [params, setParams] = useState(MP_DEFAULTS);
  const [nsfw, setNsfw] = useState({ mode:'soft', intensity:0.5, extra_prompt:'' });
  const [effort, setEffort] = useState('medium');
  const [advanced, setAdvanced] = useState(false);
  const [showJson, setShowJson] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const prefs = (r && r.preferences) || {};
        const next = { ...MP_DEFAULTS };
        for (const key of Object.keys(MP_DEFAULTS)) {
          if (typeof MP_DEFAULTS[key] === 'number') next[key] = readNumPref(prefs, key, MP_DEFAULTS[key]);
          else next[key] = String(readPref(prefs, key, MP_DEFAULTS[key]) ?? '');
        }
        const p = String(readPref(prefs, 'preset', 'balanced') || 'balanced');
        if (['balanced','conservative','creative','deterministic','custom'].includes(p)) setPreset(p);
        setParams(next);
        setAdvanced(next.mirostat_mode !== 'off');
        const nsfwMode = String(readPref(prefs,'nsfw_mode', readPref(prefs,'nsfw',{}).mode||'soft') || 'soft');
        const nsfwIntensity = Number(readPref(prefs,'nsfw_intensity', readPref(prefs,'nsfw',{}).intensity ?? 0.5));
        setNsfw({
          mode: ['block','soft','open','explicit'].includes(nsfwMode) ? nsfwMode : 'soft',
          intensity: Number.isFinite(nsfwIntensity) ? nsfwIntensity : 0.5,
          extra_prompt: String(readPref(prefs,'nsfw_extra_prompt','') || ''),
        });
        const eff = String(readPref(prefs,'reasoning_effort','medium') || 'medium');
        if (['low','medium','high'].includes(eff)) setEffort(eff);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const u = (k, v) => { setParams(p => ({ ...p, [k]: v })); save(k, v); };
  const applyPreset = (name) => {
    setPreset(name); save('preset', name);
    const vals = MP_PRESETS[name];
    if (vals) { setParams(p => ({ ...p, ...vals })); Object.entries(vals).forEach(([k,v]) => save(k,v)); }
  };
  const updateNsfw = (patch) => {
    setNsfw(n => ({ ...n, ...patch }));
    if ('mode' in patch) save('nsfw_mode', patch.mode);
    if ('intensity' in patch) save('nsfw_intensity', patch.intensity);
    if ('extra_prompt' in patch) save('nsfw_extra_prompt', patch.extra_prompt);
  };

  return (
    <>
      {/* 预设 */}
      <MField label="采样预设" desc="快速套用常用配置；手动调节后自动切到「自定义」">
        <Seg
          options={[['balanced','均衡'],['conservative','保守'],['creative','创意'],['deterministic','确定性'],['custom','自定义']]}
          value={preset}
          onChange={applyPreset}
        />
      </MField>

      <MSlider label="Temperature" desc="越高越随机；0 = 最确定；建议 0.4–1.0"
        value={params.temperature} min={0} max={2} step={0.05}
        onChange={(v) => { setPreset('custom'); u('temperature', v); }} />

      {/* 推理强度 */}
      <MField label="推理强度 (reasoning effort)" desc="仅对支持推理的模型生效(如 o3/DeepSeek-R1)">
        <Seg
          options={[['low','低'],['medium','中'],['high','高']]}
          value={effort}
          onChange={(v) => { setEffort(v); save('reasoning_effort', v); }}
        />
      </MField>

      <MSlider label="Top-p" desc="累积概率截断，0.9–0.95 常用"
        value={params.top_p} min={0} max={1} step={0.01}
        onChange={(v) => { setPreset('custom'); u('top_p', v); }} />

      <MSlider label="Top-k" desc="只从前 K 个 token 采样；0 = 关闭"
        value={params.top_k} min={0} max={200} step={1}
        onChange={(v) => { setPreset('custom'); u('top_k', v); }} />

      <MSlider label="重复惩罚 (Repetition Penalty)" desc="抑制复读；1.0 无效果；1.15–1.2 适合长叙事"
        value={params.repetition_penalty} min={1} max={2} step={0.01}
        onChange={(v) => { setPreset('custom'); u('repetition_penalty', v); }} />

      <MSlider label="Frequency Penalty" desc="按词频降权（OpenAI 风格）"
        value={params.frequency_penalty} min={-2} max={2} step={0.05}
        onChange={(v) => { setPreset('custom'); u('frequency_penalty', v); }} />

      <MSlider label="Presence Penalty" desc="鼓励引入新话题（OpenAI 风格）"
        value={params.presence_penalty} min={-2} max={2} step={0.05}
        onChange={(v) => { setPreset('custom'); u('presence_penalty', v); }} />

      {/* 数值输入 */}
      <MField label="最大生成 token" desc="单次回复最多生成的 token 数">
        <input className="pl-input" type="number" value={params.max_tokens}
          onChange={(e) => { setPreset('custom'); u('max_tokens', Number(e.target.value)); }} />
      </MField>

      <MField label="上下文大小" desc="每次请求传给模型的最大历史长度">
        <select className="pl-input" value={String(params.context_size)}
          onChange={(e) => u('context_size', Number(e.target.value))}>
          {[['4096','4K'],['8192','8K'],['16384','16K'],['32768','32K'],['65536','64K'],['131072','128K'],['1048576','1M']].map(([v,l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
      </MField>

      <MField label="随机种子 (Seed)" desc="-1 = 每次随机">
        <input className="pl-input" type="number" value={params.seed}
          onChange={(e) => u('seed', Number(e.target.value))} placeholder="-1" />
      </MField>

      <MField label="停止词 (Stop)" desc="用 | 分隔多个停止词">
        <input className="pl-input" value={params.stop}
          onChange={(e) => u('stop', e.target.value)} placeholder="player:|system:" />
      </MField>

      {/* NSFW */}
      <MField label="内容过滤模式">
        <Seg
          options={[['block','屏蔽'],['soft','温和'],['open','开放'],['explicit','显式']]}
          value={nsfw.mode}
          onChange={(v) => updateNsfw({ mode: v })}
        />
      </MField>

      {nsfw.mode !== 'block' && (
        <MSlider label="NSFW 强度" desc="越高内容越露骨；仅在 soft/open/explicit 模式下生效"
          value={nsfw.intensity} min={0} max={1} step={0.05}
          onChange={(v) => updateNsfw({ intensity: v })} />
      )}

      <MField label="NSFW 附加提示词" desc="强制附加到系统提示(如：All characters must be 18+)">
        <input className="pl-input" value={nsfw.extra_prompt}
          onChange={(e) => updateNsfw({ extra_prompt: e.target.value })}
          placeholder="All characters must be 18+" />
      </MField>

      {/* Mirostat */}
      <div className="pl-setrow">
        <div className="pl-setrow-tx"><strong>Mirostat 高级采样</strong><span>针对 llama.cpp / Ollama 后端的困惑度控制</span></div>
        <Toggle on={advanced} onChange={setAdvanced} />
      </div>
      {advanced && (
        <>
          <MField label="Mirostat 模式">
            <Seg options={[['off','关闭'],['v1','v1'],['v2','v2']]} value={params.mirostat_mode}
              onChange={(v) => u('mirostat_mode', v)} />
          </MField>
          <MSlider label="Mirostat τ (tau)" desc="目标困惑度；5 是常用值"
            value={params.mirostat_tau} min={0} max={10} step={0.1}
            onChange={(v) => u('mirostat_tau', v)} />
          <MSlider label="Mirostat η (eta)" desc="学习率"
            value={params.mirostat_eta} min={0} max={1} step={0.01}
            onChange={(v) => u('mirostat_eta', v)} />
        </>
      )}

      {/* JSON 预览 */}
      <div style={{ marginTop: 8 }}>
        <button className="pl-btn-ghost" style={{ height: 38, fontSize: 13 }} onClick={() => setShowJson(v => !v)}>
          <Icon name={showJson ? 'chevron_up' : 'chevron_down'} size={14} /> {showJson ? '收起' : '查看参数 JSON'}
        </button>
        {showJson && (
          <pre className="quote mono" style={{ fontSize: 11, marginTop: 8, overflowX: 'auto' }}>
            {JSON.stringify({
              temperature: params.temperature, top_p: params.top_p, top_k: params.top_k,
              repetition_penalty: params.repetition_penalty, frequency_penalty: params.frequency_penalty,
              presence_penalty: params.presence_penalty, max_tokens: params.max_tokens,
              context_size: params.context_size, seed: params.seed,
              stop: params.stop.split('|').filter(Boolean),
              nsfw: nsfw.mode==='block' ? null : { mode:nsfw.mode, intensity:nsfw.intensity, extra:nsfw.extra_prompt },
              ...(advanced ? { mirostat_mode:params.mirostat_mode, mirostat_tau:params.mirostat_tau, mirostat_eta:params.mirostat_eta } : {}),
            }, null, 2)}
          </pre>
        )}
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 模块分配 (modules)                                         */
/* ────────────────────────────────────────────────────────────────── */
const MODULES = [
  { id:'gm',           label:'主 GM 默认模型',    shape:'flat', apiKey:'gm.api_id',                      modelKey:'gm.model_real_name',                      tip:'玩家对话主模型' },
  { id:'sub_agent',    label:'上下文子代理',       shape:'dict', overrideKey:'sub_agent_model_override',  tip:'整理意图+检索;空=跟主 GM' },
  { id:'set_parser',   label:'指令解析代理',       shape:'flat', apiKey:'set_parser.api_id',              modelKey:'set_parser.model_real_name',               tip:'/set 命令自然语言解析' },
  { id:'console',      label:'控制台助手',         shape:'dict', overrideKey:'console_assistant_model_override', tip:'侧栏控制台;空=跟主 GM' },
  { id:'extractor',    label:'叙事提取器',         shape:'flat', apiKey:'extractor.api_id',               modelKey:'extractor.model_real_name',                tip:'GM 叙事二次解析(两步 GM)' },
  { id:'card_gen',     label:'角色卡生成器',       shape:'flat', apiKey:'character_card_generator.api_id',modelKey:'character_card_generator.model_real_name', tip:'创意工具:生成/微调角色卡' },
  { id:'card_import',  label:'AI 整理卡字段',      shape:'flat', apiKey:'card_import.api_id',             modelKey:'card_import.model_real_name',              tip:'导入酒馆卡时 LLM 整理字段;空=跟主 GM' },
  { id:'critic',       label:'一致性评分',         shape:'flat', apiKey:'critic.api_id',                  modelKey:'critic.model_real_name',                   tip:'角色卡生成的一致性评分子代理' },
  { id:'verifier',     label:'接受条件验证',       shape:'flat', apiKey:'acceptance_verifier.api_id',     modelKey:'acceptance_verifier.model_real_name',      tip:'GM 输出是否满足 acceptance 条件' },
  { id:'phase_digest', label:'阶段浓缩 (compact)', shape:'flat', apiKey:'phase_digest.api_id',            modelKey:'phase_digest.model_real_name',             tip:'长局历史按阶段浓缩成摘要' },
  { id:'black_swan',   label:'黑天鹅事件代理',     shape:'flat', apiKey:'black_swan_agent.api_id',        modelKey:'black_swan_agent.model_real_name',         tip:'主动触发世界突发事件' },
  { id:'agent',        label:'通用子代理兜底',     shape:'flat', apiKey:'agent.api_id',                   modelKey:'agent.model_real_name',                    tip:'未单独配置模型的子代理兜底' },
  { id:'embedder',     label:'向量嵌入 (RAG)',      shape:'flat', apiKey:'embed.api_id',                   modelKey:'embed.model_real_name',                    tip:'RAG 召回用 embedding 模型', capsFilter:['embedding'], allowInherit:false },
];

function ModuleModelsSection({ nav }) {
  const [prefs, setPrefs] = useState({});
  const [catalog, setCatalog] = useState({ apis:[], selected:null });
  const [credIds, setCredIds] = useState(new Set());
  const [saving, setSaving] = useState(null);
  const [embedStatus, setEmbedStatus] = useState(null);
  // 「自定义模型」态:目录里没有用户的模型时,允许手填(provider + model 名),与桌面端/AgentModelPicker 一致。
  const [customDraft, setCustomDraft] = useState({});

  const reload = useCallback(async () => {
    try {
      const [profile, models, creds, es] = await Promise.all([
        window.api.account.profile(),
        window.api.models.list().catch(() => ({})),
        window.api.credentials.list().catch(() => ({ items:[] })),
        fetch('/api/me/embedder/status', { credentials:'include' }).then(r => r.json()).catch(() => null),
      ]);
      setPrefs((profile && profile.preferences) || {});
      const ids = new Set();
      for (const c of (creds?.items || creds?.credentials || [])) {
        if (c.enabled===false) continue;
        if (!(c.has_credential || c.has_key || c.key_hint)) continue;
        ids.add(catId(credId(c.api_id || c.id)));
      }
      setCredIds(ids);
      const apis = models?.models?.apis ?? (Array.isArray(models?.apis) ? models.apis : []) ?? [];
      setCatalog({ apis: Array.isArray(apis) ? apis : [], selected: models?.models?.selected ?? models?.selected ?? null });
      setEmbedStatus(es?.ok ? es : null);
    } catch (_) {}
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const flatModels = useMemo(() => {
    const out = [];
    for (const api of (catalog.apis || [])) {
      const aid = catId(credId(api.api_id || api.id));
      if (!credIds.has(aid)) continue;
      for (const m of (api.models || api.entries || [])) {
        if (m.enabled===false) continue;
        out.push({ api_id: aid, real_name: m.real_name || m.id, display: m.display_name || m.real_name || m.id, capabilities: m.capabilities || m.caps || [] });
      }
    }
    return out;
  }, [catalog, credIds]);

  const modelsFor = (mod) => {
    const need = Array.isArray(mod.capsFilter) ? mod.capsFilter : null;
    if (!need) return flatModels.filter(m => !(m.capabilities||[]).includes('embedding'));
    return flatModels.filter(m => need.every(c => (m.capabilities||[]).includes(c)));
  };

  const currentFor = (mod) => {
    if (mod.shape==='dict') {
      const v = prefs[mod.overrideKey];
      if (v && typeof v==='object' && (v.api_id || v.model)) {
        const api_id = catId(credId(v.api_id || ''));
        const real_name = v.model;
        if (flatModels.some(x => x.api_id===api_id && x.real_name===real_name)) return { api_id, real_name };
      }
      return null;
    }
    if (mod.id==='gm') {
      const a = catId(credId(prefs['gm.api_id'] || ''));
      const m = prefs['gm.model_real_name'];
      if (a && m && flatModels.some(x => x.api_id===a && x.real_name===m)) return { api_id:a, real_name:m };
      if (flatModels.length) return { api_id:flatModels[0].api_id, real_name:flatModels[0].real_name };
      return null;
    }
    const a = prefs[mod.apiKey]; const m = prefs[mod.modelKey];
    if (a || m) {
      const api_id = catId(credId(a||'')); const real_name = m;
      if (flatModels.some(x => x.api_id===api_id && x.real_name===real_name)) return { api_id, real_name };
    }
    if (mod.allowInherit===false) return null;
    return null;
  };

  const handleChange = async (mod, value) => {
    setSaving(mod.id);
    try {
      if (value==='__inherit__') {
        if (mod.shape==='dict') await window.api.account.preferences({ [mod.overrideKey]: null });
        else { await window.api.account.preferences({ [mod.apiKey]: null }); await window.api.account.preferences({ [mod.modelKey]: null }); }
      } else {
        const sep = value.indexOf('/');
        if (sep<0) return;
        const api_id = catId(credId(value.slice(0,sep)));
        const real_name = value.slice(sep+1);
        if (mod.shape==='dict') {
          await window.api.account.preferences({ [mod.overrideKey]: { api_id, model: real_name } });
        } else {
          await window.api.account.preferences({ [mod.apiKey]: api_id });
          await window.api.account.preferences({ [mod.modelKey]: real_name });
        }
      }
      await reload();
      nav.toast(`${mod.label} 已保存`, 'ok', 'check');
    } catch (e) {
      nav.toast(`保存失败: ${e?.message||''}`, 'danger', 'warn');
    } finally { setSaving(null); }
  };

  // 「自定义」可选 provider 列表:用户已配 key 的 provider(catalog 显示名 + 仅有凭据的自定义 API)。
  const providerOptions = useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const a of (catalog.apis || [])) {
      const id = catId(credId(a.api_id || a.id));
      if (!id || !credIds.has(id) || seen.has(id)) continue;
      seen.add(id);
      out.push({ value: id, label: a.display_name || a.name || id });
    }
    for (const id of credIds) {
      if (!id || seen.has(id)) continue;
      seen.add(id);
      out.push({ value: id, label: id });
    }
    return out;
  }, [catalog, credIds]);

  const openCustom = (mod) => {
    const cur = currentFor(mod);
    const api_id = cur?.api_id || providerOptions[0]?.value || '';
    setCustomDraft(d => ({ ...d, [mod.id]: { api_id, model: cur?.real_name || '' } }));
  };
  const closeCustom = (modId) => setCustomDraft(d => { const n = { ...d }; delete n[modId]; return n; });
  // 自定义保存:复用 handleChange 的同一持久化路径(扁平双 key / dict {api_id, model})。
  const saveCustom = async (mod, draft = customDraft[mod.id]) => {
    const api_id = (draft?.api_id || '').trim();
    const model = (draft?.model || '').trim();
    if (!api_id || !model) return;
    await handleChange(mod, `${api_id}/${model}`);
    closeCustom(mod.id);
  };

  const resetAll = async () => {
    setSaving('__all__');
    const batch = {};
    for (const m of MODULES) {
      if (m.id==='gm') continue;
      if (m.shape==='dict') batch[m.overrideKey] = null;
      else { batch[m.apiKey]=null; batch[m.modelKey]=null; }
    }
    try {
      await window.api.account.preferences(batch);
      await reload();
      nav.toast('已重置所有模块为跟随主 GM', 'ok', 'check');
    } catch (e) { nav.toast('重置失败', 'danger', 'warn'); }
    finally { setSaving(null); }
  };

  return (
    <>
      <div className="pl-sec-note" style={{ marginBottom: 14 }}>
        每个子代理可单独指定模型——把贵的留给主 GM 叙事，把检索/抽取交给便宜快速的模型。
      </div>

      <button className="pl-btn-ghost" style={{ marginBottom: 16, height: 40, fontSize: 13 }}
        disabled={saving==='__all__'} onClick={resetAll}>
        <Icon name="refresh" size={14} /> 重置全部为跟随主 GM
      </button>

      {MODULES.map(mod => {
        const cur = currentFor(mod);
        const isInherit = !cur && mod.id!=='gm' && mod.allowInherit!==false;
        const visibleModels = modelsFor(mod);
        const selectVal = (() => {
          if (mod.shape==='dict') {
            const v = prefs[mod.overrideKey];
            return v && (v.api_id||v.model) ? `${catId(credId(v.api_id||''))}/${v.model||''}` : '__inherit__';
          }
          if (mod.id==='gm') return cur ? `${cur.api_id}/${cur.real_name}` : '';
          return (prefs[mod.apiKey]||prefs[mod.modelKey]) ? `${catId(credId(prefs[mod.apiKey]||''))}/${prefs[mod.modelKey]||''}` : '__inherit__';
        })();

        return (
          <div key={mod.id} className="pl-card" style={{ marginBottom: 10 }}>
            <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:8, marginBottom: 10 }}>
              <div>
                <strong style={{ fontSize: 14 }}>{mod.label}</strong>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{mod.tip}</div>
              </div>
              {isInherit && (
                <span className="pill" style={{ flexShrink:0, fontSize:10 }}>跟主 GM</span>
              )}
            </div>
            {cur && (
              <div style={{ fontSize: 11.5, color: 'var(--text-quiet)', fontFamily: 'var(--font-mono)', marginBottom: 8 }}>
                当前: {cur.api_id} · {cur.real_name}
              </div>
            )}
            {mod.id==='embedder' && embedStatus && !embedStatus.user_configured && (
              <div style={{ fontSize: 11, color: 'var(--warn)', marginBottom: 8, lineHeight: 1.5 }}>
                ⚠ 未配置 embedding key，RAG 召回可能降级
              </div>
            )}
            {(() => {
              const CUSTOM = '__custom__';
              const draft = customDraft[mod.id];
              const knownVals = new Set(visibleModels.map(m => `${m.api_id}/${m.real_name}`));
              // 已持久化但不在目录里的值 = 手填的自定义模型;打开行进入自定义态编辑。
              const isCustomPersisted = !draft && selectVal!=='__inherit__' && !!selectVal && !knownVals.has(selectVal);
              const inCustom = !!draft || isCustomPersisted;
              return (
                <>
                  <select
                    className="pl-input"
                    value={inCustom ? CUSTOM : selectVal}
                    disabled={saving===mod.id || saving==='__all__' || flatModels.length===0}
                    onChange={(e) => {
                      const v = e.target.value;
                      if (v===CUSTOM) { openCustom(mod); }
                      else { closeCustom(mod.id); handleChange(mod, v); }
                    }}
                    style={{ fontSize: 13, height: 42 }}
                  >
                    {mod.id!=='gm' && mod.allowInherit!==false && (
                      <option value="__inherit__">跟随主 GM</option>
                    )}
                    {selectVal && selectVal!=='__inherit__' && !knownVals.has(selectVal) && (
                      <option value={selectVal}>{selectVal}（未在 catalog）</option>
                    )}
                    {visibleModels.map(m => (
                      <option key={`${m.api_id}/${m.real_name}`} value={`${m.api_id}/${m.real_name}`}>
                        {m.api_id} · {m.display}
                      </option>
                    ))}
                    <option value={CUSTOM}>自定义…(手动填写模型名)</option>
                  </select>
                  {inCustom && (() => {
                    const d = draft || { api_id: currentFor(mod)?.api_id || providerOptions[0]?.value || '', model: selectVal && selectVal!=='__inherit__' ? selectVal.slice(selectVal.indexOf('/')+1) : '' };
                    const setD = (patch) => setCustomDraft(s => ({ ...s, [mod.id]: { ...d, ...patch } }));
                    return (
                      <div style={{ display:'grid', gap:8, marginTop:8 }}>
                        <select
                          className="pl-input"
                          value={d.api_id}
                          disabled={saving===mod.id || providerOptions.length===0}
                          onChange={(e) => setD({ api_id: e.target.value })}
                          style={{ fontSize: 13, height: 42 }}
                        >
                          {providerOptions.length===0 && <option value="">请先在 API 设置配置 key</option>}
                          {providerOptions.map(o => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                          ))}
                        </select>
                        <div style={{ display:'grid', gridTemplateColumns:'minmax(0,1fr) auto', gap:8 }}>
                          <input
                            className="pl-input"
                            value={d.model}
                            placeholder="填写模型名,例如 gpt-4o-mini"
                            disabled={saving===mod.id || !d.api_id}
                            onChange={(e) => setD({ model: e.target.value })}
                            style={{ fontSize: 13, height: 42 }}
                          />
                          <button
                            className="pl-btn-primary"
                            disabled={saving===mod.id || !d.api_id || !(d.model||'').trim()}
                            onClick={() => saveCustom(mod, d)}
                            style={{ height: 42, fontSize: 13 }}
                          >
                            保存
                          </button>
                        </div>
                      </div>
                    );
                  })()}
                </>
              );
            })()}
          </div>
        );
      })}

      <div style={{ fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.6, marginTop: 8 }}>
        切换 embedding 模型后，已嵌过的剧本需重新嵌入才会用新模型。
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 记忆 (memory)                                              */
/* ────────────────────────────────────────────────────────────────── */
function MemorySection() {
  const save = usePrefSave('memory');
  const [recallDepth, setRecallDepth] = useState(6);
  const [summaryWindow, setSummaryWindow] = useState(8);
  const [tokenBudget, setTokenBudget] = useState(800);
  const [autoArchive, setAutoArchive] = useState(50);
  const [pinnedMax, setPinnedMax] = useState(20);
  const [bucketPinned, setBucketPinned] = useState(true);
  const [bucketWorld, setBucketWorld] = useState(true);
  const [bucketChar, setBucketChar] = useState(true);

  const loadOr = (p, nk, ok) => {
    if (p[nk]!==undefined && p[nk]!==null) return p[nk];
    if (ok && p[ok]!==undefined && p[ok]!==null) return p[ok];
    return undefined;
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        const rd = loadOr(p, 'memory.recall_depth', 'settings.召回深度');
        if (rd !== undefined) setRecallDepth(Number(rd));
        const sw = loadOr(p, 'memory.summary_window', 'settings.摘要窗口');
        if (sw !== undefined) setSummaryWindow(Number(sw));
        const pm = loadOr(p, 'memory.pinned_max', 'settings.固定记忆上限');
        if (pm !== undefined) setPinnedMax(Number(pm));
        if (p['memory.token_budget'] !== undefined) setTokenBudget(Number(p['memory.token_budget']));
        if (p['memory.auto_archive_after_turns'] !== undefined) setAutoArchive(Number(p['memory.auto_archive_after_turns']));
        if (typeof p['memory.bucket_pinned_enabled'] === 'boolean') setBucketPinned(p['memory.bucket_pinned_enabled']);
        if (typeof p['memory.bucket_world_enabled'] === 'boolean') setBucketWorld(p['memory.bucket_world_enabled']);
        if (typeof p['memory.bucket_character_enabled'] === 'boolean') setBucketChar(p['memory.bucket_character_enabled']);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <>
      <SetGroup title="召回行为">
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <MSlider label={`召回深度 · ${recallDepth}`} desc="每轮召回的记忆片段数上限（2–20）"
            value={recallDepth} min={2} max={20} step={1}
            onChange={(v) => setRecallDepth(v)} />
          <button className="pl-btn-ghost" style={{ height:36, fontSize:13 }}
            onClick={() => { const n=Math.max(2,Math.min(20,recallDepth)); save('recall_depth',n); }}>
            <Icon name="save" size={14} /> 保存
          </button>
        </div>
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <MSlider label={`摘要窗口 · ${summaryWindow}`} desc="每次摘要覆盖的轮次数（3–20）"
            value={summaryWindow} min={3} max={20} step={1}
            onChange={(v) => setSummaryWindow(v)} />
          <button className="pl-btn-ghost" style={{ height:36, fontSize:13 }}
            onClick={() => { const n=Math.max(3,Math.min(20,summaryWindow)); save('summary_window',n); }}>
            <Icon name="save" size={14} /> 保存
          </button>
        </div>
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <MSlider label={`token 预算 · ${tokenBudget}`} desc="注入记忆的 token 上限（200–2000）"
            value={tokenBudget} min={200} max={2000} step={50}
            onChange={(v) => setTokenBudget(v)} />
          <button className="pl-btn-ghost" style={{ height:36, fontSize:13 }}
            onClick={() => { const n=Math.max(200,Math.min(2000,tokenBudget)); save('token_budget',n); }}>
            <Icon name="save" size={14} /> 保存
          </button>
        </div>
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <MSlider label={`自动归档 · 每 ${autoArchive} 轮`} desc="超过此轮数时触发阶段归档（10–200）"
            value={autoArchive} min={10} max={200} step={5}
            onChange={(v) => setAutoArchive(v)} />
          <button className="pl-btn-ghost" style={{ height:36, fontSize:13 }}
            onClick={() => { const n=Math.max(10,Math.min(200,autoArchive)); save('auto_archive_after_turns',n); }}>
            <Icon name="save" size={14} /> 保存
          </button>
        </div>
      </SetGroup>

      <SetGroup title="记忆桶配置">
        <div className="pl-setrow">
          <div className="pl-setrow-tx"><strong>固定记忆上限</strong><span>每局最多保持的固定记忆条数</span></div>
          <input
            type="number" min={5} max={100} value={pinnedMax}
            onChange={(e) => setPinnedMax(Number(e.target.value))}
            onBlur={(e) => { const n=Math.max(5,Math.min(100,Number(e.target.value))); setPinnedMax(n); save('pinned_max',n); }}
            style={{ width:72, fontSize:15, textAlign:'center', padding:'6px', border:'1px solid var(--line)', borderRadius:8, background:'var(--bg-deep)', color:'var(--text)' }}
          />
        </div>
        <div className="pl-setrow">
          <div className="pl-setrow-tx"><strong>固定记忆桶</strong><span>每轮强制注入的固定条目</span></div>
          <Toggle on={bucketPinned} onChange={(v) => { setBucketPinned(v); save('bucket_pinned_enabled',v); }} />
        </div>
        <div className="pl-setrow">
          <div className="pl-setrow-tx"><strong>世界知识桶</strong><span>场景 / 地图 / 规则等世界观记忆</span></div>
          <Toggle on={bucketWorld} onChange={(v) => { setBucketWorld(v); save('bucket_world_enabled',v); }} />
        </div>
        <div className="pl-setrow">
          <div className="pl-setrow-tx"><strong>角色关系桶</strong><span>NPC 人际关系 / 历史事件</span></div>
          <Toggle on={bucketChar} onChange={(v) => { setBucketChar(v); save('bucket_character_enabled',v); }} />
        </div>
      </SetGroup>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 权限 (permissions)                                         */
/* ────────────────────────────────────────────────────────────────── */
const HIGH_RISK_ALL = ['timeline.pending_jump','player.background','world.constraints','relationships.*.tone'];
const CUSTOM_WL_RE = /^[a-zA-Z_][a-zA-Z0-9_.*]*$/;

function PermissionsSection({ nav }) {
  const save = usePrefSave('perm');
  const [mode, setMode] = useState('review');
  const [whitelist, setWhitelist] = useState(['timeline.pending_jump','player.background','world.constraints']);
  const [custom, setCustom] = useState([]);
  const [customInput, setCustomInput] = useState('');
  const [customErr, setCustomErr] = useState('');
  // 审计日志
  const [auditEntries, setAuditEntries] = useState([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditErr, setAuditErr] = useState('');
  const [auditFilter, setAuditFilter] = useState('all');
  const [showAudit, setShowAudit] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        const v = p['perm.default_mode'] || p.default_perm_mode;
        if (v) setMode(v);
        const wl = p['perm.high_risk_whitelist'];
        if (Array.isArray(wl)) setWhitelist(wl);
        const cwl = p['permissions.custom_whitelist'];
        if (Array.isArray(cwl)) setCustom(cwl);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const toggleWhitelist = (field) => {
    const next = whitelist.includes(field) ? whitelist.filter(f => f!==field) : [...whitelist, field];
    setWhitelist(next); save('high_risk_whitelist', next);
  };

  const saveCustom = async (next) => {
    setCustom(next);
    try { await window.api.account.preferences({ 'permissions.custom_whitelist': next }); } catch (_) {}
    try { localStorage.setItem('perm.custom_whitelist', JSON.stringify(next)); } catch (_) {}
  };

  const addCustom = () => {
    const val = customInput.trim();
    if (!val) { setCustomErr('不能为空'); return; }
    if (val.length > 80) { setCustomErr('不超过 80 个字符'); return; }
    if (!CUSTOM_WL_RE.test(val)) { setCustomErr('只允许字母、数字、下划线、点、*'); return; }
    if (HIGH_RISK_ALL.includes(val)) { setCustomErr('已在内置列表中'); return; }
    if (custom.includes(val)) { setCustomErr('已存在'); return; }
    saveCustom([...custom, val]);
    setCustomInput(''); setCustomErr('');
  };

  const loadAudit = useCallback(async () => {
    setAuditLoading(true); setAuditErr('');
    try {
      const s = await window.api.game.state();
      const perms = (s && (s.permissions || s.state?.permissions)) || {};
      const log = Array.isArray(perms.audit_log) ? perms.audit_log : [];
      setAuditEntries(log.slice().reverse());
    } catch (e) { setAuditErr(e?.message || '加载失败'); }
    finally { setAuditLoading(false); }
  }, []);

  const KIND_META = {
    write:            { label:'写入', color:'var(--ok)' },
    parse_error:      { label:'解析错误', color:'var(--warn)' },
    rejected:         { label:'拒绝', color:'var(--danger)' },
    hard_forbidden:   { label:'硬禁止', color:'var(--danger)' },
    extractor_error:  { label:'提取错误', color:'var(--warn)' },
    set_parser_error: { label:'指令错误', color:'var(--warn)' },
    clarify_yield:    { label:'反问', color:'var(--ok)' },
    acceptance_unmet: { label:'条件未满足', color:'var(--warn)' },
    question_skip:    { label:'跳过问题', color:'var(--muted)' },
  };
  const filteredAudit = auditFilter==='all' ? auditEntries : auditEntries.filter(e => e.kind===auditFilter);

  return (
    <>
      {/* 默认权限模式 */}
      <SetGroup title="GM 写入权限">
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>默认模式</strong>
            <span>决定 GM 能否直接改写世界状态，还是需要玩家确认</span>
          </div>
        </div>
        <div style={{ padding: '8px 13px 13px' }}>
          <div className="pl-seg2">
            {[['default','默认'],['review','审查'],['full_access','全权']].map(([id, l]) => (
              <button key={id} className={mode===id?'active accent':''} onClick={() => { setMode(id); save('default_mode',id); }}>{l}</button>
            ))}
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 8, lineHeight: 1.5 }}>
            {mode==='review' ? '高风险写入需要玩家在确认条上明确批准' : mode==='full_access' ? 'GM 可自由修改所有字段，无需确认' : '按系统默认规则处理，中风险需确认'}
          </div>
        </div>

        {/* 高风险字段白名单 */}
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 10 }}>
          <div>
            <strong>高风险字段白名单</strong>
            <div style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 2 }}>选中的字段不触发高风险确认</div>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
            {HIGH_RISK_ALL.map(field => (
              <button
                key={field}
                className={whitelist.includes(field) ? 'pill accent' : 'pill'}
                onClick={() => toggleWhitelist(field)}
                style={{ cursor: 'pointer', fontSize: 11, height: 28, transition: 'all .15s' }}
              >
                {field}
              </button>
            ))}
          </div>
        </div>

        {/* 自定义白名单 */}
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 10 }}>
          <div>
            <strong>自定义高风险白名单</strong>
            <div style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 2 }}>
              格式: <span className="mono">player.hp</span> 或 <span className="mono">world.*</span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, width: '100%' }}>
            <input
              className="pl-input"
              style={{ flex: 1, height: 40, fontSize: 13 }}
              value={customInput}
              placeholder="player.custom_field"
              onChange={(e) => { setCustomInput(e.target.value); if (customErr) setCustomErr(''); }}
              onKeyDown={(e) => { if (e.key==='Enter') { e.preventDefault(); addCustom(); } }}
            />
            <button className="pl-btn-primary" style={{ height: 40, width: 64, fontSize: 13, flexShrink: 0 }} onClick={addCustom}>
              <Icon name="plus" size={14} />
            </button>
          </div>
          {customErr && <div style={{ fontSize: 12, color: 'var(--danger)', marginTop: -4 }}>{customErr}</div>}
          {custom.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {custom.map(entry => (
                <div key={entry} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  padding: '4px 10px', borderRadius: 6, border: '1px solid var(--line-soft)',
                  background: 'var(--panel-2)', fontSize: 12.5, fontFamily: 'var(--font-mono)',
                }}>
                  {entry}
                  <button
                    onClick={() => saveCustom(custom.filter(e => e!==entry))}
                    style={{ color: 'var(--danger)', fontSize: 14, lineHeight: 1, padding: 0 }}
                  >×</button>
                </div>
              ))}
            </div>
          )}
          {custom.length===0 && <span style={{ fontSize: 12, color: 'var(--muted)' }}>暂无自定义条目</span>}
        </div>
      </SetGroup>

      {/* 审计日志 */}
      <div className="pl-sec" style={{ marginTop: 18 }}>
        <div className="pl-sec-head">
          <h2>审计日志</h2>
          <button className="act" onClick={() => { if (!showAudit) loadAudit(); setShowAudit(v => !v); }}>
            {showAudit ? '收起' : '展开'} <Icon name={showAudit ? 'chevron_up' : 'chevron_down'} size={13} />
          </button>
        </div>
        {showAudit && (
          <div className="pl-card" style={{ padding: 12 }}>
            <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center' }}>
              <button className="pl-btn-ghost" style={{ height: 36, fontSize: 12, flex: 1 }}
                disabled={auditLoading} onClick={loadAudit}>
                <Icon name="refresh" size={13} /> {auditLoading ? '加载中…' : '刷新日志'}
              </button>
            </div>
            {auditErr && <div style={{ fontSize: 12, color: 'var(--danger)', marginBottom: 8 }}>{auditErr}</div>}

            {/* 类型筛选 */}
            {auditEntries.length > 0 && (
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 10 }}>
                {['all', ...Object.keys(KIND_META)].map(k => {
                  const count = k==='all' ? auditEntries.length : auditEntries.filter(e => e.kind===k).length;
                  if (k!=='all' && count===0) return null;
                  return (
                    <button key={k} onClick={() => setAuditFilter(k)}
                      className={auditFilter===k ? 'pill accent' : 'pill'}
                      style={{ cursor: 'pointer', fontSize: 10.5, height: 26, transition: 'all .15s' }}>
                      {k==='all' ? '全部' : (KIND_META[k]?.label || k)} · {count}
                    </button>
                  );
                })}
              </div>
            )}

            {auditEntries.length===0 && !auditLoading && (
              <div style={{ fontSize: 12.5, color: 'var(--muted)', textAlign: 'center', padding: '16px 0' }}>
                暂无审计日志（需要有活跃存档）
              </div>
            )}

            {filteredAudit.slice(0, 30).map((e, i) => {
              const meta = KIND_META[e.kind] || { label: e.kind, color: 'var(--muted)' };
              const detail = e.path
                ? `${e.path} = ${typeof e.value==='string' ? e.value : JSON.stringify(e.value)}`
                : (e.raw_spec || e.hint || '—');
              return (
                <div key={i} style={{
                  padding: '8px 0', borderBottom: '1px solid var(--line-soft)',
                  fontSize: 11.5, display: 'grid', gap: 3,
                }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', padding: '2px 7px',
                      borderRadius: 5, border: `1px solid ${meta.color}`, color: meta.color,
                      fontSize: 10.5, flexShrink: 0,
                    }}>{meta.label}</span>
                    <span className="mono" style={{ color: 'var(--muted-2)', fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {(e.ts||'').replace('T',' ').slice(0, 16)}
                    </span>
                    {e.source && <span style={{ color: 'var(--muted-2)', fontSize: 10 }}>{e.source}</span>}
                  </div>
                  <div style={{ color: 'var(--text-quiet)', lineHeight: 1.4, wordBreak: 'break-word' }}>{detail}</div>
                  {e.hint && e.path && <div style={{ color: 'var(--muted-2)', fontSize: 10.5 }}>· {e.hint}</div>}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 账户 (account)                                             */
/* ────────────────────────────────────────────────────────────────── */
function AccountSection({ nav }) {
  const user = useReactiveUser();
  const isCoBuilder = user?.is_co_builder === true;
  const [cbChecked, setCbChecked] = useState(() => !user?.co_builder_opt_out);
  const [cbSaving, setCbSaving] = useState(false);

  useEffect(() => { setCbChecked(!user?.co_builder_opt_out); }, [user?.co_builder_opt_out]);

  const handleCoBuilder = async (v) => {
    setCbChecked(v); setCbSaving(true);
    try {
      await fetch('/api/me/profile', {
        method: 'PATCH', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ co_builder_opt_out: !v }),
      });
      nav.toast('Co-Builder 设置已保存', 'ok', 'check');
    } catch (e) {
      nav.toast('保存失败', 'danger', 'warn');
      setCbChecked(!v);
    } finally { setCbSaving(false); }
  };

  // API 用量(30天)
  const [usage, setUsage] = useState(null);
  useEffect(() => {
    window.api?.account?.usage?.(30).then(setUsage).catch(() => {});
  }, []);

  // 数据迁移
  const [est, setEst] = useState(null);
  const [includeChunks, setIncludeChunks] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [importFile, setImportFile] = useState(null);
  const [importing, setImporting] = useState(false);
  const [importJob, setImportJob] = useState(null);
  const [importResult, setImportResult] = useState(null);
  const esRef = useRef(null);
  const pollRef = useRef(null);
  const fileRef = useRef(null);

  useEffect(() => {
    window.api?.account?.migrateEstimate?.().then(setEst).catch(() => {});
  }, []);

  const doExport = () => {
    setExporting(true);
    try {
      const url = window.api.account.migrateExportUrl(includeChunks);
      const a = document.createElement('a'); a.href=url; a.rel='noopener';
      document.body.appendChild(a); a.click(); a.remove();
      nav.toast('已开始下载数据包', 'ok', 'download');
    } catch (e) { nav.toast('导出失败: ' + (e?.message||''), 'danger', 'warn'); }
    finally { setTimeout(() => setExporting(false), 800); }
  };

  const finishJob = async (jobId) => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current=null; }
    if (esRef.current) { try { esRef.current.close?.(); } catch {} esRef.current=null; }
    try {
      const s = await window.api.scripts.jobStatus(jobId);
      const job = s?.job || {};
      const summary = (job.usage_actual && job.usage_actual.summary) || {};
      setImportResult({ scripts: summary.scripts??0, saves: summary.saves??0, cards: summary.cards??0, warnings: job.warnings||[] });
      nav.toast(`导入完成: 剧本 ${summary.scripts??0}·存档 ${summary.saves??0}·卡 ${summary.cards??0}`, 'ok', 'check');
    } catch (e) { nav.toast('导入失败: ' + (e?.message||''), 'danger', 'warn'); }
    finally { setImportJob(null); setImporting(false); }
  };

  const doImport = async () => {
    if (!importFile) return;
    setImporting(true); setImportResult(null); setImportJob({ stage:'scripts', stage_progress:0, stage_total:0 });
    let jobId=null;
    try {
      const r = await window.api.account.migrateImport(importFile);
      jobId = r?.job_id;
      if (!jobId) throw new Error(r?.error || '未返回作业号');
    } catch (e) {
      setImporting(false); setImportJob(null);
      nav.toast('导入失败: ' + (e?.payload?.error || e?.message || ''), 'danger', 'warn');
      return;
    }
    const isTerminal = (st) => ['done','done_with_errors','failed','cancelled'].includes(st);
    esRef.current = window.api.scripts.streamImport(jobId, {
      on_update: (jb) => { setImportJob(jb); if (isTerminal(jb.status)) finishJob(jobId); },
      on_done: () => finishJob(jobId),
      on_error: () => {
        if (pollRef.current) return;
        pollRef.current = setInterval(async () => {
          try {
            const s = await window.api.scripts.jobStatus(jobId);
            const j = s?.job; if (!j) return;
            setImportJob(j);
            if (isTerminal(j.status)) finishJob(jobId);
          } catch {}
        }, 2000);
      },
    });
  };

  useEffect(() => () => {
    if (esRef.current) { try { esRef.current.close?.(); } catch {} }
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  // 在线剧本库联邦
  const [conn, setConn] = useState(null);
  const DEFAULT_ONLINE_BASE = 'https://rpg-roleplay.stellatrix.icu';
  const reloadConn = useCallback(async () => {
    try { setConn(await window.api.federation.connectorGet()); }
    catch { setConn({ connected: false, base_url: DEFAULT_ONLINE_BASE }); }
  }, []);
  useEffect(() => { reloadConn(); }, [reloadConn]);

  const [connBase, setConnBase] = useState(DEFAULT_ONLINE_BASE);
  const [connToken, setConnToken] = useState('');
  const [connBusy, setConnBusy] = useState(false);

  useEffect(() => { if (conn?.base_url) setConnBase(conn.base_url); }, [conn?.base_url]);

  const savePat = async () => {
    if (!connToken.trim()) { nav.toast('请粘贴访问令牌', 'warn', 'key'); return; }
    setConnBusy(true);
    try {
      await window.api.federation.connectorSet(connBase.trim(), connToken.trim());
      nav.toast('已连接在线剧本库', 'ok', 'check');
      setConnToken(''); reloadConn();
    } catch (e) { nav.toast('连接失败: ' + (e?.payload?.error||e?.message||''), 'danger', 'warn'); }
    finally { setConnBusy(false); }
  };

  const disconnect = async () => {
    setConnBusy(true);
    try { await window.api.federation.connectorSet(connBase.trim(), ''); nav.toast('已断开', 'ok', 'unlock'); reloadConn(); }
    catch (e) { nav.toast('操作失败', 'danger', 'warn'); }
    finally { setConnBusy(false); }
  };

  const initial = (user?.display_name || '?').slice(0, 1);

  return (
    <>
      {/* 用户信息卡 */}
      <div style={{ display:'flex', gap:14, alignItems:'center', marginBottom:18 }}>
        <div style={{ width:60, height:60, borderRadius:18, background:'var(--accent)', color:'#fff8f3',
          display:'grid', placeItems:'center', font:'600 24px var(--font-serif)', flexShrink:0 }}>
          {initial}
        </div>
        <div>
          <div style={{ fontSize:17, fontFamily:'var(--font-serif)', color:'var(--text)' }}>
            {user?.display_name || '—'}
          </div>
          <div style={{ fontSize:12, color:'var(--muted)' }}>
            @{user?.username || '—'} · {user?.role || 'user'}
          </div>
        </div>
      </div>

      {/* 30 天用量 */}
      {usage && (
        <div className="pl-sec" style={{ marginBottom: 18 }}>
          <div className="pl-sec-head"><h2>API 用量(近 30 天)</h2></div>
          <div className="pl-stats">
            <div className="pl-stat">
              <span className="n accent">{(usage.total_tokens||0).toLocaleString()}</span>
              <div className="l">总 token</div>
            </div>
            <div className="pl-stat">
              <span className="n">{(usage.total_calls||0).toLocaleString()}</span>
              <div className="l">总调用</div>
            </div>
            <div className="pl-stat">
              <span className="n">{(usage.cache_hit_rate != null ? `${Math.round(usage.cache_hit_rate*100)}%` : '—')}</span>
              <div className="l">缓存命中</div>
            </div>
          </div>
        </div>
      )}

      {/* Co-Builder 计划 */}
      <SetGroup title="账号设置">
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>Beta Co-Builder 参与</strong>
            <span>{isCoBuilder ? '参与内测共建，可提前体验新功能' : '未加入 Co-Builder 计划'}</span>
          </div>
          {isCoBuilder ? (
            <Toggle on={cbChecked} onChange={handleCoBuilder} />
          ) : (
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>未开放</span>
          )}
        </div>
      </SetGroup>

      {/* 数据迁移 */}
      <div className="pl-sec" style={{ marginTop: 18 }}>
        <div className="pl-sec-head"><h2>数据迁移(导出 / 导入)</h2></div>
        <div className="pl-card" style={{ display: 'grid', gap: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6 }}>
            把全部个人数据(剧本/存档/角色卡)打包迁移到本地实例。出于安全，不含 API 密钥。
            {est && (
              <div style={{ marginTop: 6, color: 'var(--text-quiet)' }}>
                剧本 {est.scripts??0} · 存档 {est.saves??0} · 角色卡 {est.cards??0} · 模型条目 {est.model_entries??0}
              </div>
            )}
          </div>

          {/* 包含切片 */}
          <div style={{ display:'flex', alignItems:'center', gap:10 }}>
            <Toggle on={includeChunks} onChange={setIncludeChunks} />
            <span style={{ fontSize: 12.5, color: 'var(--text-quiet)' }}>包含原文切片（体积更大，用于本地 RAG）</span>
          </div>

          <button className="pl-btn-primary" disabled={exporting} onClick={doExport}>
            <Icon name="download" size={16} /> {exporting ? '准备下载…' : '导出数据包(.zip)'}
          </button>

          {/* 导入 */}
          <div>
            <label style={{ fontSize: 12.5, color: 'var(--text-quiet)', display: 'block', marginBottom: 8 }}>
              导入数据包（选择从在线服务导出的 account-*.zip）
            </label>
            <input
              ref={fileRef} type="file" accept=".zip,application/zip"
              onChange={(e) => { setImportFile(e.target.files?.[0]||null); setImportResult(null); }}
              style={{ fontSize: 13, color: 'var(--text-quiet)', marginBottom: 8 }}
            />
            <button className="pl-btn-ghost" disabled={!importFile||importing} onClick={doImport}>
              <Icon name="upload" size={14} /> {importing ? '导入中…' : '导入到当前账号'}
            </button>
          </div>

          {/* 导入进度 */}
          {importJob && (
            <div>
              <div style={{ fontSize: 12.5, color: 'var(--text-quiet)', marginBottom: 6 }}>
                {{ scripts:'导入剧本', saves:'导入存档', cards:'导入角色卡', done:'完成' }[importJob.stage] || importJob.stage || '处理中'}
                {importJob.stage_total ? ` ${importJob.stage_progress||0}/${importJob.stage_total}` : '…'}
              </div>
              <div style={{ height:5, background:'var(--panel-3)', borderRadius:3, overflow:'hidden' }}>
                <div style={{ height:'100%', background:'var(--accent)', borderRadius:3,
                  width: `${importJob.stage_total ? Math.round(100*(importJob.stage_progress||0)/importJob.stage_total) : 30}%`,
                  transition:'width .3s' }} />
              </div>
            </div>
          )}

          {/* 导入结果 */}
          {importResult && (
            <div style={{ padding:12, borderRadius:10, border:'1px solid var(--line)',
              background: importResult.warnings?.length ? 'var(--warn-soft)' : 'var(--ok-soft)',
              fontSize: 12.5, color: 'var(--text-quiet)' }}>
              <div style={{ fontWeight:600, marginBottom:4 }}>导入完成</div>
              <div>剧本 {importResult.scripts} · 存档 {importResult.saves} · 角色卡 {importResult.cards}</div>
              {importResult.warnings?.length > 0 && (
                <ul style={{ margin:'6px 0 0', paddingLeft:16, fontSize:11.5 }}>
                  {importResult.warnings.slice(0,10).map((w,i) => <li key={i}>{w}</li>)}
                  {importResult.warnings.length>10 && <li>…另 {importResult.warnings.length-10} 条</li>}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 在线剧本库联邦 */}
      <div className="pl-sec" style={{ marginTop: 18 }}>
        <div className="pl-sec-head"><h2>在线剧本库</h2></div>
        <div className="pl-card" style={{ display: 'grid', gap: 14 }}>
          {conn?.connected ? (
            <>
              <div style={{ fontSize: 13, color: 'var(--ok)' }}>
                ✓ 已连接: {conn.base_url}
              </div>
              <button className="pl-btn-ghost" disabled={connBusy} onClick={disconnect}>
                <Icon name="unlock" size={14} /> 断开连接
              </button>
            </>
          ) : (
            <>
              <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.5 }}>
                连接在线服务，浏览公开剧本库 / 导入 / 发布自有剧本
              </div>
              <MField label="在线服务地址">
                <input className="pl-input" value={connBase}
                  onChange={(e) => setConnBase(e.target.value)}
                  placeholder={DEFAULT_ONLINE_BASE} />
              </MField>
              <MField label="个人访问令牌(PAT)" desc="在在线服务「个人访问令牌」里生成后粘贴">
                <input className="pl-input" type="password" value={connToken}
                  onChange={(e) => setConnToken(e.target.value)}
                  placeholder="rpgpat_…" />
              </MField>
              <button className="pl-btn-primary" disabled={connBusy} onClick={savePat}>
                <Icon name="link" size={15} /> {connBusy ? '连接中…' : '保存并连接'}
              </button>
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 危险区 (danger)                                            */
/* ────────────────────────────────────────────────────────────────── */
function DangerSection({ nav }) {
  const { saves = [] } = usePlatformData();
  const nSaves = saves.length;
  const [showClearSheet, setShowClearSheet] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [clearProgress, setClearProgress] = useState(null);

  const openClear = () => { setConfirmText(''); setShowClearSheet(true); };
  const closeClear = () => { setShowClearSheet(false); setConfirmText(''); };

  const doDelete = async () => {
    if (nSaves === 0) { nav.toast('没有存档可清空', 'ok', 'info'); closeClear(); return; }
    setClearProgress({ done:0, total:nSaves });
    let done=0, fail=0;
    for (const s of saves) {
      try { await window.api.saves.remove(s.id); } catch (_) { fail++; }
      done++;
      setClearProgress({ done, total:nSaves });
    }
    setClearProgress(null);
    closeClear();
    nav.toast(fail ? `清除 ${done-fail} 个，${fail} 个失败` : `已清除 ${done} 个存档`, fail ? 'warn' : 'ok', 'trash');
    try { window.dispatchEvent(new CustomEvent('rpg-saves-updated')); } catch (_) {}
  };

  return (
    <>
      <div style={{ padding:'11px 13px', borderRadius:10, background:'var(--danger-soft)', border:'1px solid rgba(200,103,93,0.3)', fontSize:12.5, color:'var(--danger)', lineHeight:1.6, marginBottom:16 }}>
        以下操作不可逆，请谨慎操作。
      </div>

      <SetGroup title="危险操作">
        {/* 清空存档 */}
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>清空所有存档</strong>
            <span>删除账号下全部 {nSaves} 个存档（不可恢复）</span>
          </div>
          <button
            style={{ fontSize:13, color:'var(--danger)', background:'var(--danger-soft)', border:'1px solid rgba(200,103,93,0.3)', borderRadius:8, padding:'7px 14px' }}
            onClick={openClear}
          >
            清空
          </button>
        </div>

        {/* 重置平台 */}
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>重置平台数据</strong>
            <span>需要通过命令行执行，不支持从 UI 操作</span>
          </div>
          <span style={{ fontSize: 11, color: 'var(--muted-2)', fontFamily: 'var(--font-mono)' }}>CLI</span>
        </div>
        <div style={{ padding:'6px 13px 12px', fontSize:11.5, color:'var(--muted)', lineHeight:1.5 }}>
          重置命令(在服务器运行):
          <code className="mono" style={{ display:'block', marginTop:6, padding:'7px 10px', borderRadius:7, background:'var(--bg-deep)', border:'1px solid var(--line-soft)', fontSize:11, userSelect:'all', wordBreak:'break-all' }}>
            python -m rpg.platform_app.migrate reset --confirm
          </code>
        </div>
      </SetGroup>

      {/* 清空存档底部 Sheet */}
      {showClearSheet && (
        <div className="sheet-wrap show">
          <div className="sheet-scrim" onClick={closeClear} />
          <div className="sheet" style={{ maxHeight: '75%' }}>
            <div className="sheet-grip" />
            <div className="sheet-title" style={{ color: 'var(--danger)' }}>清空所有存档</div>
            <div className="sheet-sub">
              此操作将删除账号下全部 <strong style={{ color: 'var(--text)' }}>{nSaves}</strong> 个存档，<strong style={{ color: 'var(--danger)' }}>不可恢复</strong>。
            </div>
            <div className="confirm-preview">
              游戏进度、分支记录、GM 上下文将全部丢失，请确认后再继续。
            </div>
            <div style={{ marginBottom: 14 }}>
              <label style={{ fontSize: 12.5, color: 'var(--muted)', display:'block', marginBottom:8 }}>
                输入 <strong style={{ color:'var(--danger)' }}>清空</strong> 以确认
              </label>
              <input
                className="pl-input"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder="清空"
                autoFocus
              />
            </div>
            {clearProgress && (
              <div style={{ marginBottom: 12, fontSize: 12.5, color: 'var(--text-quiet)' }}>
                正在删除 {clearProgress.done} / {clearProgress.total}…
                <div style={{ height:4, background:'var(--panel-3)', borderRadius:2, marginTop:6, overflow:'hidden' }}>
                  <div style={{ height:'100%', background:'var(--danger)', borderRadius:2, width:`${Math.round(clearProgress.done/clearProgress.total*100)}%`, transition:'width .2s' }} />
                </div>
              </div>
            )}
            <div className="sheet-actions">
              <button className="sheet-btn" onClick={closeClear} disabled={!!clearProgress}>取消</button>
              <button className="sheet-btn danger"
                disabled={confirmText !== '清空' || !!clearProgress}
                onClick={doDelete}>
                <Icon name="trash" size={14} /> 清空存档
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* 主组件                                                               */
/* ────────────────────────────────────────────────────────────────── */
const SECTIONS = [
  { id:'preferences',   label:'偏好',     icon:'settings',  sub:'语言 / 字体 / 黑天鹅' },
  { id:'models',        label:'API 设置', icon:'cpu',       sub:'供应商密钥 (BYOK)', tone:'accent' },
  { id:'modelparams',   label:'模型参数', icon:'gauge',     sub:'温度 / top-p / 采样' },
  { id:'modules',       label:'模块分配', icon:'layers',    sub:'各子代理用哪个模型', tone:'info' },
  { id:'memory',        label:'记忆',     icon:'memory',    sub:'召回深度 / 记忆桶' },
  { id:'permissions',   label:'权限',     icon:'shield',    sub:'GM 写入权限 + 审计', tone:'ok' },
  { id:'account',       label:'账户',     icon:'user',      sub:'Co-Builder / 数据迁移 / 联邦' },
  { id:'danger',        label:'高危操作', icon:'warn',      sub:'清空存档 / 重置平台', tone:'warn' },
];

// 把路由 id 映射到 section id
const ROUTE_MAP = {
  'settings':               null,   // hub
  'settings-models':        'models',
  'settings-modelparams':   'modelparams',
  'settings-modules':       'modules',
  'settings-memory':        'memory',
  'settings-permissions':   'permissions',
  'settings-account':       'account',
  'settings-danger':        'danger',
};

export function MobileSettings({ nav }) {
  // 外部路由可以通过 nav.params.section 指定起始分节
  const [section, setSection] = useState(() => {
    // 支持初始路由直达
    if (nav && nav.params && nav.params.section) return nav.params.section;
    // 支持由 nav.go('settings-xxx') 跳转时传的 routeId
    if (nav && nav.currentRouteId && ROUTE_MAP[nav.currentRouteId]) return ROUTE_MAP[nav.currentRouteId];
    return null; // null = hub 列表
  });

  // 监听 cap-navigate-subsection 事件(电脑端同款)
  useEffect(() => {
    const handler = (ev) => {
      const target = ev?.detail?.target;
      if (!target || typeof target !== 'string') return;
      const parts = target.split('.');
      if (parts[0] !== 'settings' || parts.length < 2) return;
      const ALIASES = { api:'models' };
      const sub = ALIASES[parts[1]] || parts[1];
      if (SECTIONS.some(s => s.id===sub)) setSection(sub);
    };
    window.addEventListener('cap-navigate-subsection', handler);
    return () => window.removeEventListener('cap-navigate-subsection', handler);
  }, []);

  const meta = SECTIONS.find(s => s.id===section) || null;

  /* ── Hub: 分节列表 ── */
  if (!section) {
    return (
      <>
        <div className="pl-head">
          <div className="pl-head-title center">
            <strong>设置</strong>
          </div>
        </div>
        <div className="pl-body tabbed">
          <div className="pl-pad" style={{ display:'grid', gap:7 }}>
            {SECTIONS.map(s => (
              <button key={s.id} className="pl-row" onClick={() => setSection(s.id)}>
                <span className={`pl-row-ic ${s.tone||''}`}><Icon name={s.icon} size={18} /></span>
                <span className="pl-row-tx"><strong>{s.label}</strong><span>{s.sub}</span></span>
                <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
              </button>
            ))}
          </div>
        </div>
      </>
    );
  }

  /* ── 分节视图 ── */
  // ProviderDetail 自带 pl-head，需要特殊处理
  // 其他分节统一用下面的 shell
  return (
    <>
      {/* 如果是 models section 并且 ProviderDetail 正在展示，
          ProviderDetail 内部会 render 自己的 pl-head，所以让 ModelsSection 控制全屏 */}
      {section === 'models' ? (
        <ModelsSection nav={nav} onBack={() => setSection(null)} />
      ) : (
        <>
          <div className="pl-head">
            <button className="pl-back" onClick={() => setSection(null)}>
              <Icon name="chevron_left" size={20} />
            </button>
            <div className="pl-head-title">
              <strong>{meta?.label || '设置'}</strong>
              <span className="sub">{meta?.sub || ''}</span>
            </div>
          </div>
          <div className="pl-body tabbed">
            <div className="pl-pad">
              {section === 'preferences'  && <PrefSection nav={nav} />}
              {section === 'modelparams'  && <ModelParamsSection />}
              {section === 'modules'      && <ModuleModelsSection nav={nav} />}
              {section === 'memory'       && <MemorySection />}
              {section === 'permissions'  && <PermissionsSection nav={nav} />}
              {section === 'account'      && <AccountSection nav={nav} />}
              {section === 'danger'       && <DangerSection nav={nav} />}
            </div>
          </div>
        </>
      )}
    </>
  );
}

/* models section 需要直接渲染(含内部子视图切换),包装一层让 onBack 工作 */
function ModelsSection({ nav, onBack }) {
  const [selected, setSelected] = useState(null);
  const [apis, setApis] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState('');
  const autoSynced = useRef(new Set());

  const mapModel = useCallback((m) => ({
    id: m.real_name || m.id,
    display: m.display_name || m.real_name || m.id,
    real_name: m.real_name || m.id,
    enabled: m.enabled !== false,
    visible: m.hidden !== true,
    capabilities: m.capabilities || {},
    health: m.health || 'untested',
    health_latency_ms: m.health_latency_ms,
  }), []);

  const load = useCallback(async () => {
    const [data, creds] = await Promise.all([
      window.api.models.list(),
      window.api.credentials.list().catch(() => ({ items:[] })),
    ]);
    const credMap = {};
    for (const c of (creds?.items || creds?.credentials || [])) {
      const cid = normId(c.api_id || c.id);
      credMap[cid] = { has_key: !!(c.has_credential||c.has_key||c.key_hint), key_hint: c.key_hint||'', enabled: c.enabled!==false, base_url_override: c.base_url_override||'', proxy_url: c.proxy_url || '' };
    }
    const list = data?.models?.apis || data?.apis || [];
    const rows = Array.isArray(list) ? list.map(api => {
      const cataId = catId(api.api_id || api.id);
      const criId = credId(cataId);
      const cred = credMap[criId] || credMap[normId(cataId)] || {};
      return {
        id: cataId, credential_id: criId,
        name: api.display_name || api.name || cataId,
        // 用户自己的 base_url_override(中转站)优先;非 admin 的 api.base_url 已被后端 redact 成空。
        base_url: cred.base_url_override || api.base_url || '',
        key_set: !!cred.has_key, key_hint: cred.key_hint || '—',
        connectivity: { status:'untested' },
        // proxy 从凭据读(api.proxy 对非 admin 恒 undefined);后端 list_credentials 返回 proxy_url。
        enabled: cred.enabled !== false, proxy: cred.proxy_url ? 'http_proxy' : 'direct',
        models: (api.models || api.entries || []).map(mapModel),
      };
    }).filter(a => a.key_set) : [];
    // 中转站 / 自定义供应商:把不在全局 catalog 里、但带 base_url_override 的用户凭据合成为
    // provider 行,否则保存后在列表里看不到、无法选模型;models=[] 由用户点同步从中转站拉取。
    const catalogIds = new Set((Array.isArray(list) ? list : []).map(a => normId(catId(a.api_id || a.id))));
    const customRows = Object.entries(credMap)
      .filter(([cid, c]) => c.has_key && c.base_url_override && !catalogIds.has(normId(cid)))
      .map(([cid, c]) => ({
        id: cid, credential_id: cid, name: cid,
        base_url: c.base_url_override, key_set: true, key_hint: c.key_hint || '—',
        connectivity: { status:'untested' },
        enabled: c.enabled !== false, proxy: c.proxy_url ? 'http_proxy' : 'direct',
        models: [], _custom: true,
      }));
    const allRows = [...rows, ...customRows];
    setApis(allRows);
    return allRows;
  }, [mapModel]);

  const syncRemote = useCallback(async (api, silent=false) => {
    if (!api) return;
    const aId = catId(api.id);
    setApis(arr => arr.map(a => a.id===aId ? { ...a, connectivity: { ...a.connectivity, status:'checking' } } : a));
    try {
      const r = await window.api.models.syncRemote({ api_id: aId, base_url: api.base_url||'' });
      if (!r?.ok) throw new Error(r?.error||'sync failed');
      const models = (r.models||[]).map(mapModel);
      setApis(arr => arr.map(a => a.id===aId ? {
        ...a, models,
        connectivity: { status:'ok', latency_ms:r.latency_ms, remote_total:r.remote_total??models.length },
      } : a));
      if (!silent) nav.toast(`同步完成：${models.length} 个模型`, 'ok', 'refresh');
    } catch (e) {
      setApis(arr => arr.map(a => a.id===aId ? { ...a, connectivity: { status:'err', error:e?.message||'' } } : a));
      if (!silent) nav.toast('同步失败: ' + (e?.message||''), 'danger', 'warn');
    }
  }, [mapModel, nav]);

  const reload = useCallback(async () => {
    try { setLoadErr(''); await load(); }
    catch (e) { setLoadErr(e?.message || '加载失败'); }
    finally { setLoading(false); }
  }, [load]);

  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    if (loading) return;
    apis.forEach(api => {
      if (autoSynced.current.has(api.id)) return;
      autoSynced.current.add(api.id);
      syncRemote(api, true);
    });
  }, [loading, apis, syncRemote]);

  const selectedApi = apis.find(a => a.id===selected) || null;

  /* 供应商详情 */
  if (selectedApi) {
    return (
      <ProviderDetail
        api={selectedApi}
        onBack={() => setSelected(null)}
        onSync={() => syncRemote(selectedApi)}
        nav={nav}
        onToggleModel={async (mId) => {
          const m = selectedApi.models.find(x => x.id===mId);
          const prev = !!m?.enabled;
          setApis(arr => arr.map(a => a.id===selectedApi.id ? { ...a, models: a.models.map(x => x.id===mId ? { ...x, enabled:!x.enabled } : x) } : a));
          try {
            await window.api.models.upsertModel({ api_id: selectedApi.id, real_name: mId, enabled: !prev });
          } catch (e) {
            // POST /api/models/model 是 admin-only,非 admin 部署模式 403 → 回滚乐观翻转并提示。
            setApis(arr => arr.map(a => a.id===selectedApi.id ? { ...a, models: a.models.map(x => x.id===mId ? { ...x, enabled: prev } : x) } : a));
            nav.toast(e?.status===403 ? '仅管理员可修改全局模型开关' : ('保存失败: ' + (e?.message||'')), 'danger', 'warn');
          }
        }}
        onDeleteKey={async () => {
          if (!window.confirm(`删除「${selectedApi.name}」的 API 密钥?删除后需回电脑端重新配置。`)) return;
          try {
            await window.api.credentials.remove({ api_id: credId(selectedApi.id) });
            setSelected(null);
            setApis(arr => arr.filter(a => a.id!==selectedApi.id));
            nav.toast('已删除密钥', 'ok', 'trash');
          } catch (e) { nav.toast('删除失败: '+(e?.message||''), 'danger', 'warn'); }
        }}
      />
    );
  }

  /* 供应商列表 */
  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title">
          <strong>API 设置</strong>
          <span className="sub">供应商密钥 · {apis.length} 家已配置</span>
        </div>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {loading && (
            <div className="pl-empty">
              <div className="ic"><Icon name="cpu" size={22} /></div>
              <p>加载中…</p>
            </div>
          )}
          {!loading && loadErr && (
            <div className="pl-empty">
              <div className="ic"><Icon name="warn" size={22} /></div>
              <h3>加载失败</h3>
              <p>{loadErr}</p>
              <button className="pl-btn-ghost" style={{ marginTop:12, height:38, fontSize:13, width:'auto', padding:'0 18px' }}
                onClick={() => { setLoading(true); setLoadErr(''); reload(); }}>重试</button>
            </div>
          )}
          {!loading && !loadErr && apis.length===0 && (
            <div className="pl-empty">
              <div className="ic"><Icon name="key" size={22} /></div>
              <h3>尚未配置任何供应商</h3>
              <p>请在电脑端「设置 → 模型」添加 API 密钥后再来这里管理</p>
            </div>
          )}
          {!loading && !loadErr && apis.map(pv => {
            const conn = pv.connectivity || {};
            const enabledCnt = pv.models.filter(m => m.enabled).length;
            const statusOk = conn.status==='ok';
            const statusErr = conn.status==='err';
            const statusBusy = conn.status==='checking';
            return (
              <button key={pv.id} className="pl-prov" style={{ width:'100%', textAlign:'left', marginBottom:10 }}
                onClick={() => setSelected(pv.id)}>
                <div className="pl-prov-head">
                  <span className="pl-prov-logo">{pv.name.slice(0,1)}</span>
                  <span className="pl-prov-id">
                    <strong>
                      {pv.name}
                      {pv.enabled===false && <span style={{ marginLeft:6, fontSize:11, fontWeight:600, color:'var(--muted-2)', border:'1px solid var(--line-soft)', borderRadius:4, padding:'1px 6px' }}>已禁用</span>}
                    </strong>
                    <span className="key mono">•••• {pv.key_hint}</span>
                  </span>
                  <span className={`pl-status ${statusOk?'online':''}`}>
                    <span className="d" style={statusErr ? { background:'var(--danger)' } : statusBusy ? { background:'var(--warn)' } : {}} />
                    {statusOk ? `✓ ${conn.latency_ms||''}${conn.latency_ms?'ms':'已连接'}` :
                     statusErr ? '✗ 错误' : statusBusy ? '同步中' : '未测试'}
                  </span>
                  <Icon name="chevron_right" size={16} style={{ color:'var(--muted-3)', marginLeft:4 }} />
                </div>
                {pv.models.slice(0,2).map(m => (
                  <div key={m.id} className="pl-model-row">
                    <span className="mname" style={{ color: m.enabled ? 'var(--text)' : 'var(--muted-2)' }}>{m.display}</span>
                    <span className="mmeta" style={{ color: m.health==='ok'?'var(--ok)':m.health==='err'?'var(--danger)':'var(--muted-3)' }}>
                      {m.health==='ok' ? '✓' : m.health==='err' ? '✗' : '?'}
                    </span>
                  </div>
                ))}
                {pv.models.length > 0 && (
                  <div className="pl-model-row" style={{ justifyContent:'flex-end', color:'var(--muted-2)', fontSize:11 }}>
                    {enabledCnt}/{pv.models.length} 已启用
                    {pv.models.length > 2 && ` · +${pv.models.length-2} 个`}
                  </div>
                )}
              </button>
            );
          })}
          <div style={{ padding:'8px 0', fontSize:12, color:'var(--muted)', textAlign:'center', lineHeight:1.6 }}>
            添加新供应商请在电脑端「设置 → 模型」操作
          </div>
        </div>
      </div>
    </>
  );
}

export default MobileSettings;
