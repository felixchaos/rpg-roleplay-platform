import React from 'react';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSFormField from '@cloudscape-design/components/form-field';
import CSSelect from '@cloudscape-design/components/select';
import CSInput from '@cloudscape-design/components/input';
import CSAlert from '@cloudscape-design/components/alert';
import CSButton from '@cloudscape-design/components/button';
import { credApiIdSet } from './catalog-helpers.js';

// ── variant="popover" 紧凑浮层样式(只注一次;复用与旧 ModelPopover 同源的视觉 token) ──
const AMP_POP_STYLE_ID = 'amp-pop-styles-v1';
if (typeof document !== 'undefined' && !document.getElementById(AMP_POP_STYLE_ID)) {
  const css = `
.amp-pop{display:flex;flex-direction:column;min-width:280px;max-width:420px;
  background:var(--panel,#211f1d);border:1px solid var(--line,#36322d);
  border-radius:var(--r-3,8px);overflow:hidden;color:var(--text,#ebe7df);
  font-family:var(--font-sans,system-ui);font-size:13px;box-shadow:var(--shadow-3,0 8px 28px rgba(0,0,0,.4));}
.amp-pop-head{padding:8px 10px;border-bottom:1px solid var(--line-soft,#2a2724);background:var(--bg-deep,#131211);}
.amp-pop-search{width:100%;box-sizing:border-box;padding:5px 10px;background:rgba(255,255,255,0.04);
  border:1px solid var(--line-soft,#2a2724);border-radius:6px;color:var(--text,#ebe7df);font-size:12.5px;outline:none;font-family:inherit;}
.amp-pop-search::placeholder{color:var(--muted-2,#6b655e);}
.amp-pop-list{list-style:none;margin:0;padding:4px;max-height:min(60vh,420px);overflow-y:auto;}
.amp-pop-list::-webkit-scrollbar{width:5px;}
.amp-pop-list::-webkit-scrollbar-thumb{background:var(--line,#36322d);border-radius:3px;}
.amp-pop-empty{padding:16px 10px;text-align:center;color:var(--muted,#968f85);font-size:12.5px;}
.amp-pop-item{width:100%;text-align:left;display:flex;flex-direction:column;gap:2px;
  padding:7px 10px;border:1px solid transparent;border-radius:6px;background:transparent;
  color:inherit;cursor:pointer;font-family:inherit;transition:background .1s,border-color .1s;}
.amp-pop-item:hover:not(:disabled){background:var(--panel-2,#282623);}
.amp-pop-item.active{background:var(--info-soft,rgba(122,166,194,.12));border-color:rgba(122,166,194,.45);}
.amp-pop-item:disabled{cursor:not-allowed;}
.amp-pop-item-top{display:flex;align-items:center;gap:6px;}
.amp-pop-item-top strong{font-size:13px;}
.amp-pop-dot{display:inline-block;width:6px;height:6px;border-radius:50%;flex-shrink:0;}
.amp-pop-api{margin-left:auto;font-size:11px;color:var(--muted-2,#6b655e);font-family:var(--font-mono,monospace);}
.amp-pop-err{font-size:10.5px;color:var(--danger,#c8675d);}
.amp-pop-meta{font-size:11.5px;color:var(--muted,#968f85);}
`;
  const el = document.createElement('style');
  el.id = AMP_POP_STYLE_ID;
  el.textContent = css;
  document.head.appendChild(el);
}

/* AgentModelPicker — 全站唯一的「某 agent / 模块用哪个模型」选择器。
   Provider + Model 两个 CSSelect,默认写入 user_preferences:
     <prefPrefix>.api_id / <prefPrefix>.model_real_name
   后端各 agent 的 resolve_api_and_model 读同名 key。
   导入剧本(extractor)/ 导入角色卡 AI 整理(card_import)/ 设置页「模块分配」/
   游戏内模型切换 等全部共用此组件 —— UI 与持久化完全一致,不再各写一套。

   ── 落库形态(persistShape) ──────────────────────────────────────────────
     "flat"(默认)  : 写 <prefPrefix>.api_id + <prefPrefix>.model_real_name 双 key
     "dict"        : 写 dictKey = { api_id, model } 单 key 对象(sub_agent / console)
     "models_select": 调 POST /api/models/select(per-user gm scope,可带 saveId),
                      不写 preferences —— 游戏内 GM 模型切换用

   ── 继承(allowInherit) ──────────────────────────────────────────────────
     allowInherit=true 时下拉首项为 inheritLabel(默认「跟随主 GM」),选中=清空偏好
     (flat 写 null/null;dict 写 null),后端解析时回退主 GM / 系统默认。

   ── 紧凑浮层(variant="popover") ──────────────────────────────────────────
     纯 CSS 浮层(无 Cloudscape 依赖),按 provider 分组、可搜索、可显示
     health badge(showHealth)/价格(showPricing),替代旧 game-composer ModelPopover。

   props:
     prefPrefix       : 偏好命名空间(如 "extractor" / "card_import" / "gm")
     header           : 容器标题(variant="container" 时)
     description      : 标题下描述
     defaultModel     : 用户未配时的默认候选 model_real_name；不传则取后端 selected.real_name
     preferProvider   : 用户已配 key 时优先选的 provider(如 "deepseek")
     configHash       : 「去配 key」跳转的 hash(默认 settings-models)
     variant          : "container"(带 CSContainer 外框) | "bare"(只渲染内容) | "popover"(紧凑浮层)
     onChange?        : (api_id, model) => void  选择变化时回调(可选)
     capabilityFilter?: string  只展示 capabilities 含此值的模型(如 "image_gen" / "embedding")
     persistShape?    : "flat"(默认) | "dict" | "models_select"
     dictKey?         : persistShape="dict" 时写入的单 key(如 "sub_agent_model_override")
     allowInherit?    : 是否提供「跟随主 GM」(清空偏好)首项,默认 false
     inheritLabel?    : 继承项文案,默认「跟随主 GM」
     saveId?          : persistShape="models_select" 时传入 → 存档级切换(不动全局)
     showHealth?      : variant="popover" 时展示 health badge(默认 false)
     showPricing?     : variant="popover" 时展示价格/ctx 行(默认 false)
     restrictPlatformVertex?: embedder 专用 — 非 admin/vip 不显示平台 vertex embedding 兜底
                              (传入 embedder/status 判定结果布尔:true=允许平台兜底) */
export default function AgentModelPicker({
  prefPrefix,
  header = '模型',
  description = '',
  defaultModel = null,      // 不再硬编码；null 时从后端 selected 取默认值
  preferProvider = null,
  configHash = 'settings-models',
  variant = 'container',
  onChange = null,
  persistOnMount = false,   // 无偏好时把解析出的默认(provider+model)一次性写入,保证"所见即所用"
  fallbackPrefix = null,    // 本功能(prefPrefix)无偏好时,继承哪个偏好命名空间作默认(如 'gm'=用户默认模型);默认不继承
  capabilityFilter = null,  // 可选：只展示 capabilities 含此字符串的模型(如 "image_gen")
  persistShape = 'flat',    // "flat" | "dict" | "models_select"
  dictKey = null,           // persistShape="dict" 时的单 key
  allowInherit = false,     // 提供「跟随主 GM」(清空偏好)首项
  inheritLabel = '跟随主 GM',
  saveId = null,            // persistShape="models_select" 存档级切换
  showHealth = false,       // variant="popover" health badge
  showPricing = false,      // variant="popover" 价格/ctx 行
  platformVertexAllowed = false,  // embedder:是否允许平台 vertex embedding 兜底(admin/vip)
}) {
  const { useState, useEffect } = React;
  const [apis, setApis] = useState([]);
  const [credApiIds, setCredApiIds] = useState(new Set());
  const [apiId, setApiId] = useState('');
  const [model, setModel] = useState('');
  const [inherit, setInherit] = useState(false);     // 当前是否「跟随主 GM」(无偏好态)
  const [saving, setSaving] = useState(false);
  const [customSel, setCustomSel] = useState(false);  // 用户在下拉里显式选了「自定义…」
  const [reloadTick, setReloadTick] = useState(0);    // 凭据变更后强制重拉(issue #22)
  const [popOpen, setPopOpen] = useState(false);      // variant="popover" 浮层开关
  const [popQuery, setPopQuery] = useState('');       // popover 搜索词
  const [loaded, setLoaded] = useState(false);        // 模型/凭据首拉是否完成(区分「加载中」与「真的没模型」)
  const popRef = useState(() => React.createRef())[0];
  const popTriggerRef = useState(() => React.createRef())[0];

  // 换/删 API Key 后(api-client 广播 rpg-credentials-updated)重拉 API/模型/凭据列表,
  // 让下拉里能选的 provider/模型与当前 key 同步。
  useEffect(() => {
    const bump = () => setReloadTick((x) => x + 1);
    window.addEventListener('rpg-credentials-updated', bump);
    return () => window.removeEventListener('rpg-credentials-updated', bump);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [profile, models, creds] = await Promise.all([
          window.api.account.profile().catch(() => ({})),
          window.api.models.list().catch(() => ({})),
          window.api.credentials.list().catch(() => ({ items: [] })),
        ]);
        if (cancelled) return;
        const list = models?.models?.apis || (Array.isArray(models?.apis) ? models.apis : []) || [];
        setApis(Array.isArray(list) ? list : []);
        // AgentPlatform 是 Vertex 的 SA 凭证 — UI 里归一成 vertex_ai（与后端 canonical 一致）
        // 局部 ids 供下方 eligible()/Array.from(ids) 用 —— 此前直接引用未声明的 `ids` 会抛
        // ReferenceError,被本块外层 catch 静默吞掉,导致挂载时 setApiId/setModel/onChange(init)
        // 整段不执行(无偏好场景下选择器空着、回显失败)。必须用已构建的 Set。
        const ids = credApiIdSet(creds);
        setCredApiIds(ids);
        // 后端 selected 是全局默认模型（由 /api/models 返回）；
        // defaultModel prop 若未传（null），就从 selected 取。
        const backendSelected = models?.selected;
        const resolvedDefaultModel = defaultModel
          || (backendSelected && (backendSelected.real_name || backendSelected.model_id))
          || '';
        const p = (profile && profile.preferences) || {};
        // ── dict-shape(sub_agent / console)读取:dictKey = { api_id, model } ──
        let prefApi, prefModel;
        if (persistShape === 'dict' && dictKey) {
          const dv = p[dictKey];
          if (dv && typeof dv === 'object' && (dv.api_id || dv.model)) {
            prefApi = dv.api_id; prefModel = dv.model;
          }
        } else if (persistShape === 'models_select') {
          // 游戏内 GM 模型切换:当前生效模型来自后端 selected(per-user gm 偏好),
          // 不读 prefPrefix 双 key(那是别的命名空间)。
          prefApi = backendSelected && backendSelected.api_id;
          prefModel = backendSelected && (backendSelected.real_name || backendSelected.model_id);
        } else {
          prefApi = p[`${prefPrefix}.api_id`];
          prefModel = p[`${prefPrefix}.model_real_name`];
        }
        // allowInherit:本功能完全无偏好 → 当前态为「跟随主 GM」(inherit)。
        const noPref = !(prefApi || prefModel);
        setInherit(allowInherit && noPref);
        // fallbackPrefix(如 'gm')= 用户设置的默认模型。本功能(prefPrefix)无偏好时,优先继承它,
        // 而不是落到便宜档/写死默认 —— 满足"默认是用户设置的默认模型"。
        const fbApi = fallbackPrefix ? p[`${fallbackPrefix}.api_id`] : '';
        const fbModel = fallbackPrefix ? p[`${fallbackPrefix}.model_real_name`] : '';
        // Provider:本功能偏好 > 继承的默认 provider(若已配 key) > 后端 selected provider(若已配 key)
        //   > 偏好的 provider(若已配 key) > 用户首个已配 provider
        const selectedApiId = backendSelected && (backendSelected.api_id || '');
        // embedder(admin/vip)平台兜底:vertex_ai 即使不在 ids(没配用户 SA),也算可选 provider。
        const platVertexOk = allowInherit === false && capabilityFilter === 'embedding'
          && platformVertexAllowed;
        const eligible = (aid) => !!aid && (ids.has(aid)
          || (platVertexOk && aid === 'vertex_ai'
              && list.some((x) => (x.api_id || x.id) === 'vertex_ai'
                  && (x.models || x.entries || []).some((m) => (m.capabilities || m.caps || []).includes('embedding')))));
        const chosenApi = prefApi
          || (fbApi && eligible(fbApi) ? fbApi : null)
          || (selectedApiId && eligible(selectedApiId) ? selectedApiId : null)
          || (preferProvider && eligible(preferProvider) ? preferProvider : null)
          || Array.from(ids)[0]
          || (platVertexOk ? 'vertex_ai' : null)
          || preferProvider || '';
        // Model 必须属于 chosenApi(否则会出现 Anthropic + gemini 这种错配):
        //   本功能偏好 model > 继承的默认 model(若在该 provider 下) > resolvedDefaultModel
        //   > 该 provider 首个 capabilityFilter 过滤后的模型
        const apiObj = list.find((x) => (x.api_id || x.id) === chosenApi);
        let chosenModels = (apiObj?.models || apiObj?.entries || []);
        // capabilityFilter='embedding' 时保留 embedding 模型；否则剔除 embedding-only(避免聊天选择器混进 RAG 模型)。
        if (capabilityFilter !== 'embedding') {
          chosenModels = chosenModels.filter((m) => !((m.capabilities || m.caps || []).length === 1 && (m.capabilities || m.caps || [])[0] === 'embedding'));
        }
        // capabilityFilter 过滤（仅影响"默认首选"查找，不影响最终兜底）
        const capFilteredModels = capabilityFilter
          ? chosenModels.filter((m) => (m.capabilities || m.caps || []).includes(capabilityFilter))
          : chosenModels;
        const fbModelValid = fbModel && capFilteredModels.some((m) => (m.real_name || m.id) === fbModel);
        const hasDefault = capFilteredModels.some((m) => (m.real_name || m.id) === resolvedDefaultModel);
        // 该 provider 下偏向便宜档(haiku/flash/mini/lite/small/nano),适合整理这种工具任务,
        // 避免默认落到旗舰(如 Opus)烧额度。
        const cheapRe = /haiku|flash|mini|lite|small|nano/i;
        const cheap = capFilteredModels.find((m) => cheapRe.test(m.real_name || m.id || '') || cheapRe.test(m.display_name || ''));
        const firstModel = capFilteredModels[0] ? (capFilteredModels[0].real_name || capFilteredModels[0].id) : '';
        const chosenModel = prefModel
          || (fbModelValid ? fbModel : null)
          || (hasDefault ? resolvedDefaultModel : null)
          || (cheap ? (cheap.real_name || cheap.id) : null)
          || firstModel
          || resolvedDefaultModel;
        setApiId(chosenApi);
        setModel(chosenModel);
        // 把解析出的当前 provider+model 告知父组件(父拿它提交),与展示完全一致,不依赖 persistOnMount。
        // source='init':这是「挂载时解析出的当前模型回声」,不是用户真的换了模型 ——
        // 游戏内浮层据此【不关闭/不刷新】(否则一打开就被这条回声关掉,见 game-composer / MobileGame)。
        if (chosenApi && chosenModel) onChange && onChange(chosenApi, chosenModel, 'init');
        // 无偏好时把解析出的一致默认写回(仅当 provider+model 都有效),避免"显示一套、后端用另一套"。
        // persistOnMount 只对 flat shape 有意义(dict/models_select/allowInherit 不在挂载时强写)。
        if (persistOnMount && persistShape === 'flat' && !allowInherit
            && chosenApi && chosenModel && !(prefApi && prefModel)) {
          try {
            await window.api.account.preferences({
              [`${prefPrefix}.api_id`]: chosenApi,
              [`${prefPrefix}.model_real_name`]: chosenModel,
            });
          } catch (_) { /* 静默 */ }
        }
      } catch (_) {} finally { if (!cancelled) setLoaded(true); }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefPrefix, dictKey, persistShape, saveId, reloadTick]);

  // 统一落库:按 persistShape 走 flat 双 key / dict 单 key 对象 / POST /api/models/select。
  const persist = async (aid, m) => {
    if (!aid || !m) return;
    setSaving(true);
    setInherit(false);
    try {
      if (persistShape === 'dict' && dictKey) {
        await window.api.account.preferences({ [dictKey]: { api_id: aid, model: m } });
      } else if (persistShape === 'models_select') {
        await window.api.models.select({
          api_id: aid, model_id: m,
          ...(saveId != null ? { save_id: saveId } : {}),
        });
      } else {
        await window.api.account.preferences({
          [`${prefPrefix}.api_id`]: aid,
          [`${prefPrefix}.model_real_name`]: m,
        });
      }
      onChange && onChange(aid, m, 'user');   // 用户真的换了模型 → 浮层据此关闭/刷新
    } catch (_) { /* 静默 */ } finally { setSaving(false); }
  };

  // allowInherit:清空本功能偏好 → 后端解析回退主 GM / 系统默认。
  const persistInherit = async () => {
    setSaving(true);
    try {
      if (persistShape === 'dict' && dictKey) {
        await window.api.account.preferences({ [dictKey]: null });
      } else {
        await window.api.account.preferences({
          [`${prefPrefix}.api_id`]: null,
          [`${prefPrefix}.model_real_name`]: null,
        });
      }
      setInherit(true);
      setCustomSel(false);
      onChange && onChange(null, null, 'user');
    } catch (_) { /* 静默 */ } finally { setSaving(false); }
  };

  const apiOf = (id) => apis.find((x) => (x.api_id || x.id) === id);
  const modelsOf = (id) => (apiOf(id)?.models || apiOf(id)?.entries || []);
  const isEmbeddingOnly = (m) => {
    const caps = m.capabilities || m.caps || [];
    return caps.length === 1 && caps[0] === 'embedding';
  };
  // embedder 平台兜底:仅 admin/vip(platformVertexAllowed=true)可见平台 vertex embedding。
  // 非 vip/admin 用户没上传自己的 SA 时 vertex_ai 不在 credApiIds → 不会被显示(收紧到位)。
  const platformVertexEmbedding = platformVertexAllowed && capabilityFilter === 'embedding';
  // 单一真相:某 provider 在选择器里是否可见。
  //   = provider 级 curation 开(a.enabled !== false,即用户/admin 没在「模型管理」隐藏它)
  //     且(用户配了该 provider 凭据 OR 平台 vertex embedding 兜底)。
  // 游戏内 popover 与设置/聊天 bare 的 providerOptions 共用此判定,避免两套门控不一致 ——
  // 历史 bug:popover 看 a.enabled、下拉只看 cred,导致用户禁用的 provider(如 openrouter 336 模型)
  // 在聊天/游戏选择器里仍冒出「完整模型列表」。
  const _providerVisible = (a) => {
    const aid = a && (a.api_id || a.id);
    if (!aid) return false;
    if (a.enabled === false) return false;          // 用户/admin 在模型管理隐藏了该 provider
    if (credApiIds.has(aid)) return true;           // 用户配了凭据
    if (platformVertexEmbedding && aid === 'vertex_ai'
        && (a.models || a.entries || []).some((m) => (m.capabilities || m.caps || []).includes('embedding'))) return true;
    return false;
  };
  const providerOptions = React.useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const a of apis) {
      const id = a.api_id || a.id;
      if (!id || seen.has(id)) continue;
      if (!_providerVisible(a)) continue;            // 尊重 a.enabled curation + 凭据
      seen.add(id);
      out.push({ value: id, label: a.display_name || a.name || id });
    }
    // 自定义 OpenAI-compatible API 可能只有用户凭证,不在全局模型目录里。
    for (const id of credApiIds) {
      if (!id || seen.has(id)) continue;
      seen.add(id);
      out.push({ value: id, label: id });
    }
    return out;
  }, [apis, credApiIds, platformVertexEmbedding]);
  // 过滤后的模型条目(共用于下拉 + popover)。
  const filteredModels = modelsOf(apiId).filter((m) => {
    const caps = m.capabilities || m.caps || [];
    // capabilityFilter='embedding' 时只看 embedding;其它(含无 filter)排除 embedding-only(避免聊天选择器混进 RAG)。
    if (capabilityFilter === 'embedding') return caps.includes('embedding');
    if (isEmbeddingOnly(m)) return false;
    if (capabilityFilter) return caps.includes(capabilityFilter);
    return true;
  });
  const modelOptions = filteredModels.map((m) => ({
    value: m.real_name || m.id,
    label: `${m.display_name || m.real_name || m.id}${m.enabled === false ? ' (禁用)' : ''}`,
    disabled: m.enabled === false,
  }));

  // 「自定义」态:用户显式选了自定义,或当前 model 不在该 provider 的目录里(如手填的旧偏好)。
  const CUSTOM_MODEL = '__custom_model__';
  const INHERIT = '__inherit__';
  const knownModelVals = new Set(modelOptions.map((o) => o.value));
  const isCustomModel = !inherit && customSel || (!inherit && modelOptions.length > 0 && !!model && !knownModelVals.has(model));
  // 是否展示「未配 key」告警:无任何凭据 且 没有平台 vertex 兜底可用。
  const showNoKeyAlert = credApiIds.size === 0 && !(platformVertexEmbedding && providerOptions.length > 0);

  const noProviders = providerOptions.length === 0;
  // Model 下拉项:首项可选「跟随主 GM」(allowInherit),末项「自定义…」。
  const modelSelectOptions = [
    ...(allowInherit ? [{ value: INHERIT, label: inheritLabel }] : []),
    ...modelOptions,
    { value: CUSTOM_MODEL, label: '自定义…(手动填写模型名)' },
  ];
  const modelSelectSelected = inherit
    ? { value: INHERIT, label: inheritLabel }
    : isCustomModel
      ? { value: CUSTOM_MODEL, label: '自定义…' }
      : (() => {
          const m = modelsOf(apiId).find((x) => (x.real_name || x.id) === model);
          return m ? { value: model, label: m.display_name || m.real_name || m.id }
            : (model ? { value: model, label: model } : null);
        })();

  const body = (
    <>
      {showNoKeyAlert && (
        <CSAlert type="warning" header="尚未配置任何 API key" action={
          <CSButton iconName="settings" onClick={() => { window.location.hash = configHash; }}>去配 key</CSButton>
        }>
          请先去 设置 → 模型管理 给至少一家 provider 配 key。
        </CSAlert>
      )}
      <CSColumnLayout columns={2}>
        <CSFormField label="Provider">
          <CSSelect
            selectedOption={inherit ? null : (() => {
              const a = apiOf(apiId);
              return a ? { value: apiId, label: a.display_name || a.name || apiId }
                : (apiId ? { value: apiId, label: apiId + ' (未配 key)' } : null);
            })()}
            options={providerOptions}
            placeholder={noProviders ? '请先配 API key' : (inherit ? inheritLabel : '选择 provider')}
            onChange={({ detail }) => {
              const aid = detail.selectedOption.value;
              setApiId(aid);
              setInherit(false);
              const m0 = filteredModelsOf(aid).find((m) => m.enabled !== false);
              const mid = m0 ? (m0.real_name || m0.id) : '';
              setCustomSel(false);  // 换 provider 重置「自定义」态
              // 切到「无可用模型」的 provider 时,绝不回写旧 provider 的 model
              // (否则会把 {新api_id, 旧model_real_name} 错配写进 preferences → 后端解析失败)。
              if (mid) { setModel(mid); persist(aid, mid); }
              else { setModel(''); }
            }}
            disabled={saving || noProviders}
            empty="还没配 API key"
          />
        </CSFormField>
        <CSFormField label="Model" description="列表里没有你的模型时,选「自定义…」手动填写服务商实际 model 名称。">
          <div style={{ display: 'grid', gap: 8 }}>
            {(modelOptions.length > 0 || allowInherit) && (
              <CSSelect
                selectedOption={modelSelectSelected}
                options={modelSelectOptions}
                placeholder="选择模型"
                onChange={({ detail }) => {
                  const mid = detail.selectedOption.value;
                  if (mid === INHERIT) { persistInherit(); }
                  else if (mid === CUSTOM_MODEL) { setCustomSel(true); setInherit(false); }
                  else { setCustomSel(false); setInherit(false); setModel(mid); persist(apiId, mid); }
                }}
                disabled={saving || (!apiId && !allowInherit)}
              />
            )}
            {(isCustomModel || (modelOptions.length === 0 && !allowInherit)) && (
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 8 }}>
                <CSInput
                  value={model}
                  placeholder="填写模型名,例如 gpt-4o-mini"
                  onChange={({ detail }) => setModel(detail.value)}
                  onBlur={() => { const m = (model || '').trim(); if (apiId && m) persist(apiId, m); }}
                  disabled={saving || !apiId}
                />
                <CSButton
                  loading={saving}
                  disabled={saving || !apiId || !(model || '').trim()}
                  onClick={() => persist(apiId, (model || '').trim())}
                >
                  保存
                </CSButton>
              </div>
            )}
          </div>
        </CSFormField>
      </CSColumnLayout>
    </>
  );

  if (variant === 'popover') return renderPopover();
  if (variant === 'bare') return body;
  return (
    <CSContainer header={<CSHeader variant="h2" description={description}>{header}</CSHeader>}>
      {body}
    </CSContainer>
  );

  // ── variant="popover":游戏内紧凑浮层(替代旧 game-composer ModelPopover) ──────
  function filteredModelsOf(aid) {
    return modelsOf(aid).filter((m) => {
      const caps = m.capabilities || m.caps || [];
      if (capabilityFilter === 'embedding') return caps.includes('embedding');
      if (isEmbeddingOnly(m)) return false;
      if (capabilityFilter) return caps.includes(capabilityFilter);
      return true;
    });
  }

  function renderPopover() {
    // 扁平化所有【可见 provider】(尊重 a.enabled curation + 凭据)下的可选模型(+ health / pricing)。
    // 与 providerOptions 共用 _providerVisible,两处门控一致。
    const flat = [];
    for (const a of apis) {
      const aid = a.api_id || a.id;
      if (!_providerVisible(a)) continue;
      for (const m of filteredModelsOf(aid)) {
        if (m.enabled === false) continue;
        const pricing = m.pricing || {};
        flat.push({
          api_id: aid,
          api_label: a.display_name || a.name || aid,
          real_name: m.real_name || m.id,
          label: m.display_name || m.real_name || m.id,
          desc: (m.capabilities || m.caps || []).slice(0, 3).join(' · '),
          health: m.health || 'untested',
          health_error: m.health_error || '',
          health_latency_ms: m.health_latency_ms,
          price_in: pricing.input != null ? pricing.input : null,
          price_out: pricing.output != null ? pricing.output : null,
          ctx: pricing.context != null ? pricing.context : null,
        });
      }
    }
    const order = { ok: 0, untested: 1, degraded: 2, err: 3 };
    flat.sort((a, b) => (order[a.health] ?? 4) - (order[b.health] ?? 4));
    const q = popQuery.trim().toLowerCase();
    const filtered = q ? flat.filter((m) =>
      `${m.label} ${m.real_name} ${m.api_label} ${m.api_id}`.toLowerCase().includes(q)) : flat;
    const selKey = (apiId && model) ? `${apiId}::${model}` : '';
    // K/M 缩写走 window.__fmt.compact(语义统一 #30);本组件 falsy → null(非 "—"),故保留该分支。
    const fmtCtx = (n) => !n ? null
      : ((window.__fmt && window.__fmt.compact) ? window.__fmt.compact(n)
        : (n >= 1000000 ? `${Math.round(n / 1000000)}M` : n >= 1000 ? `${Math.round(n / 1000)}K` : String(n)));
    const fmtPrice = (m) => (m.price_in != null && m.price_out != null)
      ? (m.price_in === 0 && m.price_out === 0 ? '免费' : `$${m.price_in.toFixed(2)} / $${m.price_out.toFixed(2)} per M`) : null;

    return (
      <div ref={popRef} className="amp-pop">
        <div className="amp-pop-head">
          <input
            className="amp-pop-search"
            type="text"
            value={popQuery}
            placeholder="搜索模型…"
            onChange={(e) => setPopQuery(e.target.value)}
            autoFocus
          />
        </div>
        <ul className="amp-pop-list">
          {filtered.length === 0 && (
            <li className="amp-pop-empty">{
              !loaded ? '加载模型中…'
                : q ? `没有匹配「${popQuery}」的模型`
                : '没有可用模型(先配 API key)'
            }</li>
          )}
          {filtered.map((m) => {
            const key = `${m.api_id}::${m.real_name}`;
            const active = key === selKey;
            const unavailable = showHealth && m.health === 'err';
            const dotColor = m.health === 'ok' ? 'var(--ok,#3fa66a)'
              : m.health === 'degraded' ? '#e89b3a'
              : m.health === 'err' ? 'var(--danger,#c8675d)'
              : 'var(--muted,#968f85)';
            const price = showPricing ? fmtPrice(m) : null;
            const ctx = showPricing ? fmtCtx(m.ctx) : null;
            return (
              <li key={key}>
                <button
                  className={'amp-pop-item' + (active ? ' active' : '')}
                  disabled={saving || unavailable}
                  style={unavailable ? { opacity: 0.45 } : undefined}
                  onClick={() => { if (!saving && !unavailable) { setApiId(m.api_id); setModel(m.real_name); persist(m.api_id, m.real_name); } }}
                  title={unavailable ? `unreachable: ${(m.health_error || '').slice(0, 120)}` : m.real_name}
                >
                  <div className="amp-pop-item-top">
                    {showHealth && <span className="amp-pop-dot" style={{ background: dotColor }} />}
                    <strong>{m.label}</strong>
                    <span className="amp-pop-api">{m.api_label}</span>
                    {unavailable && <span className="amp-pop-err">unreachable</span>}
                  </div>
                  {(m.desc || price || ctx) && (
                    <span className="amp-pop-meta">
                      {m.desc || null}
                      {price && <span style={{ marginLeft: m.desc ? 6 : 0, opacity: 0.85 }}>{price}</span>}
                      {ctx && <span style={{ marginLeft: (m.desc || price) ? 6 : 0, opacity: 0.7 }}>ctx {ctx}</span>}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    );
  }
}
