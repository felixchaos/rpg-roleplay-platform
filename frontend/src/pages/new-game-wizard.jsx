/* New Game Wizard (Phase F / W6-b) — 5 步建档向导 + 设置(锁死/可改)。
   自包含新文件,不改既有 NewGameModal(零回归)。需浏览器 e2e 验证。
   后端已 live 验证:GET/PATCH /api/saves/{id}/settings(apply 锁死 enforcement)。

   用法:建档创建 save 后,用 saveId 挂载本向导收集设置 → PATCH(is_create:true)。
   props: { saveId, scriptId, worldlines=[], onDone } */

import React from 'react';
import { useState, useEffect } from 'react';

const API = () => (window.__API_BASE || '');

async function getSettings(saveId) {
  const r = await fetch(`${API()}/api/saves/${saveId}/settings`, { credentials: 'include' });
  return r.json();
}
async function patchSettings(saveId, updates, isCreate) {
  const r = await fetch(`${API()}/api/saves/${saveId}/settings`, {
    method: 'PATCH', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates, is_create: isCreate }),
  });
  return r.json();
}

// 步骤 → 字段 key 分组(对齐 settings.SETTINGS_SCHEMA 的 step)
const STEPS = [
  { n: 1, title: '剧本与起始世界线', keys: ['starting_worldline'] },
  { n: 2, title: '角色', keys: [] }, // 角色卡/persona 走既有流程,这里占位
  { n: 3, title: '元知识', keys: ['foreknowledge_mode', 'npc_awareness'] },
  { n: 4, title: '引导与防剧透', keys: ['steering_strength', 'spoiler_guard'] },
  { n: 5, title: '确认', keys: [] },
];

function Field({ field, value, onChange, worldlines }) {
  const locked = false; // 建档阶段都可设
  if (field.key === 'starting_worldline' && worldlines && worldlines.length) {
    return (
      <label className="wz-field">
        <span>{field.label}</span>
        <select value={value} onChange={(e) => onChange(e.target.value)} disabled={locked}>
          {worldlines.map((w) => <option key={w.wl_key} value={w.wl_key}>{w.label}</option>)}
        </select>
        <small>{field.help}</small>
      </label>
    );
  }
  if (field.options) {
    return (
      <label className="wz-field">
        <span>{field.label}</span>
        <select value={value} onChange={(e) => onChange(e.target.value)} disabled={locked}>
          {field.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <small>{field.help}</small>
      </label>
    );
  }
  return (
    <label className="wz-field">
      <span>{field.label}</span>
      <input value={value} onChange={(e) => onChange(e.target.value)} />
      <small>{field.help}</small>
    </label>
  );
}

export function NewGameWizard({ saveId, worldlines = [], onDone }) {
  const [step, setStep] = useState(1);
  const [schema, setSchema] = useState(null);
  const [vals, setVals] = useState({});
  const [err, setErr] = useState('');

  useEffect(() => {
    getSettings(saveId).then((d) => {
      if (d.ok) {
        setSchema(d.schema);
        const init = {};
        (d.schema.fields || []).forEach((f) => { init[f.key] = (d.settings && d.settings[f.key]) ?? f.default; });
        setVals(init);
      } else setErr(d.error || '加载设置失败');
    });
  }, [saveId]);

  if (err) return <div className="wz-error">错误:{err}</div>;
  if (!schema) return <div className="wz-loading">加载向导…</div>;

  const fieldsByKey = Object.fromEntries((schema.fields || []).map((f) => [f.key, f]));
  const cur = STEPS.find((s) => s.n === step);

  const finish = async () => {
    try {
      const r = await patchSettings(saveId, vals, true); // is_create=true → 锁死项也可设
      if (r.applied !== undefined && !r.error) { onDone && onDone(vals, r); }
      else setErr(r.error || '保存失败');
    } catch (e) {
      setErr(e.message || '网络错误');
    }
  };

  return (
    <div className="new-game-wizard" style={{ padding: 16, maxWidth: 560 }}>
      <div className="wz-steps" style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        {STEPS.map((s) => (
          <span key={s.n} className={s.n === step ? 'wz-step active' : 'wz-step'}>{s.n}. {s.title}</span>
        ))}
      </div>
      <h3>{cur.title}</h3>

      {step === 5 ? (
        <div className="wz-confirm">
          <p>确认以下设置(建档后世界线/身份锁死,其余可在游戏内改):</p>
          <ul>{Object.entries(vals).map(([k, v]) => <li key={k}>{(fieldsByKey[k]?.label) || k}:<b>{String(v)}</b></li>)}</ul>
        </div>
      ) : cur.keys.length ? (
        cur.keys.map((k) => fieldsByKey[k] && (
          <Field key={k} field={fieldsByKey[k]} value={vals[k]} worldlines={worldlines}
                 onChange={(v) => setVals((p) => ({ ...p, [k]: v }))} />
        ))
      ) : (
        <p style={{ opacity: 0.6 }}>(此步走既有角色/记忆流程,默认即可)</p>
      )}

      <div className="wz-nav" style={{ marginTop: 16, display: 'flex', justifyContent: 'space-between' }}>
        <button disabled={step === 1} onClick={() => setStep((s) => s - 1)}>上一步</button>
        {step < 5
          ? <button onClick={() => setStep((s) => s + 1)}>下一步</button>
          : <button className="primary" onClick={finish}>开始游戏</button>}
      </div>
    </div>
  );
}

export default NewGameWizard;
