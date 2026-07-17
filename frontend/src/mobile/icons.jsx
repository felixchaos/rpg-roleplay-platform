/* icons.jsx — 24x24 stroke 图标集(ESM 版,从设计稿 / 生产 game-icons.jsx 抬取)。
   currentColor 描边,size/strokeWidth 可调。供移动端各页复用。 */
import React from 'react';
import { SHARED_ICON_PATHS } from '../lib/icon-paths.jsx';

export const Icon = ({ name, size = 16, strokeWidth = 1.7, style }) => {
  const common = {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth, strokeLinecap: 'round', strokeLinejoin: 'round', style,
  };
  const paths = {
    ...SHARED_ICON_PATHS,

    // 移动端独有
    at: <><circle cx="12" cy="12" r="4" /><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8" /></>,
    shield: <><path d="M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z" /><path d="M9 12l2 2 4-4" /></>,
    copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h8" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    cloud: <><path d="M6 16a4 4 0 0 1 .5-8 5.5 5.5 0 0 1 10.5 1.5A3.5 3.5 0 0 1 17 16z" /></>,
    drag_h: <><circle cx="9" cy="8" r="1" /><circle cx="9" cy="16" r="1" /><circle cx="15" cy="8" r="1" /><circle cx="15" cy="16" r="1" /></>,
    key: <><circle cx="8" cy="12" r="4" /><path d="M12 12h9M18 12v3M15 12v2" /></>,
    bell: <><path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6z" /><path d="M10 20a2 2 0 0 0 4 0" /></>,
    globe: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></>,
    dice: <><rect x="4" y="4" width="16" height="16" rx="3" /><circle cx="9" cy="9" r="1.2" fill="currentColor" stroke="none" /><circle cx="15" cy="15" r="1.2" fill="currentColor" stroke="none" /><circle cx="15" cy="9" r="1.2" fill="currentColor" stroke="none" /><circle cx="9" cy="15" r="1.2" fill="currentColor" stroke="none" /></>,
    layers: <><path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5M3 17l9 5 9-5" /></>,
    gauge: <><path d="M4 18a8 8 0 1 1 16 0" /><path d="M12 14l4-4" /></>,
    mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></>,
    star: <><path d="M12 3l2.6 5.5 6 .8-4.4 4.2 1.1 6L12 16.8 6.7 19.5l1.1-6L3.4 9.3l6-.8z" /></>,
    heart: <><path d="M12 20s-7-4.3-7-9.3A3.7 3.7 0 0 1 12 7a3.7 3.7 0 0 1 7 3.7c0 5-7 9.3-7 9.3z" /></>,
    trophy: <><path d="M7 4h10v4a5 5 0 0 1-10 0z" /><path d="M7 6H4v2a3 3 0 0 0 3 3M17 6h3v2a3 3 0 0 1-3 3M9 16h6M10 16v3M14 16v3M8 21h8" /></>,
    sliders: <><path d="M4 8h10M18 8h2M4 16h2M10 16h10" /><circle cx="16" cy="8" r="2" /><circle cx="8" cy="16" r="2" /></>,
    filter: <><path d="M4 5h16l-6 7v5l-4 2v-7z" /></>,
    chart: <><path d="M4 4v16h16" /><path d="M8 14l3-4 3 3 4-6" /></>,
    cpu: <><rect x="7" y="7" width="10" height="10" rx="2" /><path d="M10 3v2M14 3v2M10 19v2M14 19v2M3 10h2M3 14h2M19 10h2M19 14h2" /></>,
    feedback: <><path d="M4 5h16v11H9l-4 4z" /><path d="M9 10h6M9 13h3" /></>,
    help: <><circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 1 1 3.4 2.3c-.6.3-.9.8-.9 1.4v.3M12 16.5v.01" /></>,
    book_open: <><path d="M12 6c-2-1.5-5-1.5-8-1v13c3-.5 6-.5 8 1 2-1.5 5-1.5 8-1V5c-3-.5-6-.5-8 1z" /><path d="M12 6v13" /></>,
    add_card: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M12 9v6M9 12h6" /></>,
    logout: <><path d="M14 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4" /><path d="M10 12H3M6 8l-3 4 3 4" /></>,
    sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" /></>,
    moon: <><path d="M20 14A8 8 0 1 1 10 4a6 6 0 0 0 10 10z" /></>,
    qq: <><circle cx="12" cy="11" r="6" /><path d="M8 18c0 2 1.8 3 4 3s4-1 4-3M9 9v.01M15 9v.01" /></>,
  };
  return <svg {...common}>{paths[name] || null}</svg>;
};

export default Icon;
