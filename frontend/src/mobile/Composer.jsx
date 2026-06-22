/* mobile/Composer.jsx — 移动端统一聊天输入框(语义统一:游戏台 + 酒馆共用一套)
 *
 * 收口 MobileGame.Composer 与 MobileTavern.ChatView 此前各自手写的 .composer 实现。
 * 统一了:textarea 自动增高、Enter 发送(中文输入法 isComposing 守卫)、发送/停止
 * 按钮(空输入=idle 不可发、running=停止)。两个聊天页的输入框从此外观与行为一致。
 *
 * 差异用 slot 注入(不传即没有,行为与旧版对齐):
 *   leading  输入行左侧节点  —— 游戏台的「+ 附件」按钮;酒馆不传。
 *   footer   输入框下方 chip 行 —— 游戏台的 斜杠/模型/权限/上下文;酒馆不传。
 *   topSlot  composer-zone 顶部 —— 游戏台的建议词条;酒馆不传。
 *
 * 视觉/class 沿用 mobile.css 既有 .composer-zone/.composer/.composer-input-row/
 * .c-text/.c-send/.composer-foot —— 零新 CSS。
 */
import React, { useRef, useEffect } from 'react';
import { Icon } from './icons.jsx';

export function MobileComposer({
  value,
  onChange,
  onSubmit,
  onStop,
  running = false,
  placeholder = '',
  leading = null,
  footer = null,
  topSlot = null,
  sendAria,
  stopAria,
  sendIconSize = 18,
  taRef: extTaRef,
}) {
  const innerRef = useRef(null);
  const taRef = extTaRef || innerRef;

  /* textarea 自动增高(收口两端各自的同款 effect)。 */
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
    // taRef 引用稳定;只随 value 重算高度。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const canSend = !!String(value || '').trim() && !running;
  const doSubmit = () => { if (!canSend) return; onSubmit && onSubmit(); };
  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent?.isComposing) {
      e.preventDefault();
      doSubmit();
    }
  };

  return (
    <div className="composer-zone">
      {topSlot}
      <div className="composer">
        <div className="composer-input-row">
          {leading}
          <textarea
            ref={taRef}
            className="c-text"
            rows={1}
            value={value}
            placeholder={placeholder}
            onChange={(e) => onChange && onChange(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button
            className={`c-send${running ? '' : (canSend ? '' : ' idle')}`}
            onClick={() => (running ? (onStop && onStop()) : doSubmit())}
            disabled={!running && !canSend}
            aria-label={running ? stopAria : sendAria}
          >
            <Icon name={running ? 'stop' : 'send'} size={sendIconSize} />
          </button>
        </div>
        {footer && <div className="composer-foot">{footer}</div>}
      </div>
    </div>
  );
}

export default MobileComposer;
