// Modal —— 全站统一弹窗外壳。收敛此前 ~28 处手写的 `pl-modal-backdrop > pl-modal >
// pl-modal-head / pl-modal-foot` 内联结构(platform-app / settings / scripts / saves /
// cards / game / tavern 各自重复一份)。产出 DOM 与手写结构【完全一致】,迁移零视觉变化。
//
// 用法:
//   <Modal eyebrow="删除确认" title="删除 X" width={420}
//          onClose={close} closeDisabled={busy}
//          footer={<div className="...">…按钮…</div>}>
//     …正文(children,自行决定是否包 pl-modal-form / 自定义 div)…
//   </Modal>
//
// - header 传自定义节点可整体覆盖默认的 eyebrow/title 头;eyebrow/title/header 都没有则不渲染头部。
// - footer 为 null(默认)则不渲染底栏。
// - closeDisabled:忙碌态下背景点击与关闭按钮都禁用,避免操作进行中误关。
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../game-icons.jsx';

export default function Modal({
  open = true,
  eyebrow,
  title,
  header,
  onClose,
  width = 560,
  closeDisabled = false,
  showClose = true,
  footer = null,
  className = '',
  panelStyle,
  children,
}) {
  const { t } = useTranslation();
  if (!open) return null;
  const hasHeader = header != null || title != null || eyebrow != null;
  const tryClose = () => { if (!closeDisabled && onClose) onClose(); };
  return (
    <div className="pl-modal-backdrop" onClick={tryClose}>
      <div
        className={`pl-modal${className ? ' ' + className : ''}`}
        onClick={(e) => e.stopPropagation()}
        style={{ width: `min(${width}px, 100%)`, ...(panelStyle || {}) }}
      >
        {hasHeader && (
          <header className="pl-modal-head">
            {header != null ? header : (
              <div>
                {eyebrow != null && <div className="pl-modal-eyebrow">{eyebrow}</div>}
                {title != null && <h2 className="pl-modal-title">{title}</h2>}
              </div>
            )}
            {showClose && onClose && (
              <button className="iconbtn" onClick={onClose} disabled={closeDisabled} data-tip={t('common.close')}>
                <Icon name="close" size={14} />
              </button>
            )}
          </header>
        )}
        {children}
        {footer != null && <footer className="pl-modal-foot">{footer}</footer>}
      </div>
    </div>
  );
}
