// InfoHint —— 可复用「ⓘ 信息按钮」。把过长的说明文本从常驻副标题收进按需弹出的
// popover,既简化界面、又让表单行高统一(输入框对齐)。全站表单 label 旁通用。
//
// 用法:
//   <SetRow label={<LabelWithHint label="上下文上限" hint="每次请求携带的上限;超过会自动截断历史与召回。" />}>
//   或直接:  标题文本 <InfoHint text="说明…" label="上下文上限" />
import React from 'react';
import CSPopover from '@cloudscape-design/components/popover';
import { Icon } from '../game-icons.jsx';

export default function InfoHint({ text, label }) {
  if (!text) return null;
  return (
    <CSPopover
      triggerType="custom"
      dismissButton={false}
      position="bottom"
      size="medium"
      content={<span className="info-hint-text">{text}</span>}
      renderWithPortal
    >
      <button
        type="button"
        className="info-hint"
        aria-label={label ? `${label} · 说明` : '说明'}
        onClick={(e) => e.preventDefault()}
      >
        <Icon name="info" size={13} strokeWidth={1.7} />
      </button>
    </CSPopover>
  );
}

// 标题 + ⓘ 的组合(给 CSFormField/CSHeader 的 label/title 用,保证标题与提示一行对齐)
export function LabelWithHint({ label, hint, secondary }) {
  return (
    <span className="label-with-hint">
      <span className="label-with-hint-main">{label}</span>
      {hint ? <InfoHint text={hint} label={typeof label === 'string' ? label : undefined} /> : null}
      {secondary ? <span className="label-with-hint-sub">{secondary}</span> : null}
    </span>
  );
}
