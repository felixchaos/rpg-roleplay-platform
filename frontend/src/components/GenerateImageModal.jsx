import React from 'react';
import CSModal from '@cloudscape-design/components/modal';
import CSBox from '@cloudscape-design/components/box';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSFormField from '@cloudscape-design/components/form-field';
import CSTextarea from '@cloudscape-design/components/textarea';
import CSAlert from '@cloudscape-design/components/alert';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import AgentModelPicker from './AgentModelPicker.jsx';
import ImageSizePicker from './ImageSizePicker.jsx';
import { useImageGeneration } from '../hooks/useImageGeneration.js';

/* GenerateImageModal — AI 生图弹窗，复用 CSModal + AgentModelPicker 范式。

   props:
     open           : boolean  是否可见
     onClose        : ()=>void  关闭回调
     kind           : 生图类型 'cover'|'avatar'|'card'|'chat'|'game'|'persona'
     attach         : { type, id } 可选，生成后写入目标
     defaultPrompt  : 默认 prompt 文本
     onDone         : (url:string)=>void  生成成功并获得 URL 后回调

   内部流程:
     1. 点「生成」→ POST /api/images/generate → {image_id, status:'pending'}
     2. 每 2s 轮询 GET /api/images/{image_id} 直到 status==='done' 或 'failed'
     3. done → onDone(url) + 关闭弹窗
     4. failed / credentials_required → 显示错误提示
*/
export default function GenerateImageModal({
  open,
  onClose,
  kind = 'avatar',
  attach,
  defaultPrompt = '',
  onDone,
  saveId,
}) {
  const { useState, useEffect } = React;

  const [prompt, setPrompt] = useState(defaultPrompt);
  const [size, setSize] = useState('');
  const [selModel, setSelModel] = useState({ api_id: '', model: '' });

  // 生图内核(generate + 每 2s 轮询 + creds 分类)收口到 useImageGeneration;busy/error/credsMissing
  // 取自 hook。done → onDone(url)+onClose;creds 文案逐字保留。
  const CREDS_TEXT = '请先在设置中配置该 Provider 的 API Key，再重试。';
  const { generate, generating: busy, error, credsMissing, reset, stop, setError } = useImageGeneration({
    onDone: (url) => { if (onDone) onDone(url); if (onClose) onClose(); },
  });
  // 反馈采集:生图弹窗(无独立路由)标记当前活跃功能供运行环境快照识别。
  useEffect(() => {
    if (!open) return;
    try { window.__activeFeature = 'AI 生图'; } catch (_) {}
    return () => { try { if (window.__activeFeature === 'AI 生图') window.__activeFeature = null; } catch (_) {} };
  }, [open]);
  // perCall:逐字保留本组件原 done/fail/error 文案与轮询策略。
  const PER_CALL = {
    noImageIdMsg: '服务端未返回任务 ID',   // 响应无 image_id(含 !res)→ 报错
    failFallback: '生成失败',               // failed 取错文兜底
    credsErrorText: CREDS_TEXT,             // creds 时显示该文 + credsMissing 旗标
    emptyResStops: true, emptyResMsg: '轮询返回空响应',   // 轮询空响应:停并报错
    catchStops: true, pollCatchMsg: '轮询出错',           // 轮询 catch:停并报错(不再重试)
    genericErrorMsg: '请求失败',
  };

  // 当 defaultPrompt 变化(如父组件切换上下文)时同步
  useEffect(() => {
    setPrompt(defaultPrompt);
  }, [defaultPrompt]);

  // 弹窗关闭时清理轮询(仅停轮询,逐字保留原行为:不在此清 error/credsMissing,那由 handleClose 做)。
  useEffect(() => {
    if (!open) stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function handleGenerate() {
    const trimmedPrompt = (prompt || '').trim();
    if (!trimmedPrompt) {
      setError('请填写生成描述（Prompt）');
      return;
    }
    if (!selModel.api_id || !selModel.model) {
      setError('请先选择模型');
      return;
    }
    const body = {
      prompt: trimmedPrompt,
      kind,
      api_id: selModel.api_id,
      model: selModel.model,
    };
    if (attach) body.attach = attach;
    if (saveId != null) body.save_id = saveId;
    if (size) body.size = size;
    generate(body, PER_CALL);
  }

  function handleClose() {
    if (busy) return;
    reset();
    if (onClose) onClose();
  }

  return (
    <CSModal
      visible={!!open}
      onDismiss={handleClose}
      header="AI 生图"
      footer={
        <CSBox float="right">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton onClick={handleClose} disabled={busy}>取消</CSButton>
            <CSButton
              variant="primary"
              loading={busy}
              disabled={busy || !(prompt || '').trim()}
              onClick={handleGenerate}
            >
              生成
            </CSButton>
          </CSSpaceBetween>
        </CSBox>
      }
    >
      <CSSpaceBetween size="m">
        {busy && (
          <CSStatusIndicator type="loading">
            生成中，请稍候…
          </CSStatusIndicator>
        )}
        {error && (
          <CSAlert
            type="error"
            header={credsMissing ? '缺少 API Key' : '生成失败'}
            action={credsMissing
              ? <CSButton iconName="settings" onClick={() => { window.location.hash = 'settings-models'; }}>去配 Key</CSButton>
              : undefined
            }
          >
            {error}
          </CSAlert>
        )}
        <CSFormField
          label="生成描述（Prompt）"
          description="描述你想生成的图片内容，越具体越好。"
        >
          <CSTextarea
            value={prompt}
            onChange={({ detail }) => setPrompt(detail.value)}
            placeholder="例如：身着白色汉服的年轻女子，清澈眼神，水墨风格"
            rows={3}
            disabled={busy}
          />
        </CSFormField>
        <AgentModelPicker
          prefPrefix="image_gen"
          fallbackPrefix="gm"
          capabilityFilter="image_gen"
          variant="bare"
          header={undefined}
          description="选择生图模型（仅展示支持图像生成的模型）"
          configHash="settings-models"
          onChange={(api_id, model) => setSelModel({ api_id, model })}
        />
        <CSFormField label="尺寸 / 比例" description="按用途推荐默认比例；改一次后本地记住。vertex(Gemini) 暂不支持自定义尺寸">
          <ImageSizePicker kind={kind} value={size} onChange={setSize} />
        </CSFormField>
      </CSSpaceBetween>
    </CSModal>
  );
}
