/* Shared icon set for the RPG console — minimal stroke icons.
   All icons are 24x24 viewBox, currentColor stroke. */
import React from 'react';
import { SHARED_ICON_PATHS } from './lib/icon-paths.jsx';

const Icon = ({ name, size = 16, strokeWidth = 1.6, style }) => {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    style,
  };
  const paths = {
    ...SHARED_ICON_PATHS,

    // navigation / chrome — 平台独有
    minus: <path d="M5 12h14" />,
    message_square: <><path d="M5 5h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 3v-3H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" /><path d="M8 10h8M8 14h5" /></>,

    // right-panel tabs — 平台独有
    context: <><path d="M5 4h14v5H5z" /><path d="M5 13h14v7H5z" /><path d="M9 16h6" /></>,
    debug: <><path d="M12 7v10M8 9l-2-2M16 9l2-2M8 15l-2 2M16 15l2 2" /><rect x="9" y="7" width="6" height="10" rx="3" /></>,

    // composer / actions — 平台独有
    attach: <path d="M21 11.5l-9 9a5 5 0 1 1-7-7l9-9a3.5 3.5 0 0 1 5 5l-9 9a2 2 0 0 1-3-3l8-8" />,
    mic: <><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></>,
    diamond_sm: <><path d="M12 4 20 12 12 20 4 12z" /></>,

    // statuses — 平台独有
    spinner: <><path d="M12 3a9 9 0 1 1-9 9" /></>,
    err: <><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></>,
    drag: <><circle cx="9" cy="6" r="1" /><circle cx="9" cy="12" r="1" /><circle cx="9" cy="18" r="1" /><circle cx="15" cy="6" r="1" /><circle cx="15" cy="12" r="1" /><circle cx="15" cy="18" r="1" /></>,
    git_branch: <><circle cx="6" cy="6" r="2" /><circle cx="18" cy="18" r="2" /><circle cx="6" cy="18" r="2" /><path d="M6 8v8M8 6h6a4 4 0 0 1 4 4v6" /></>,
    quote: <><path d="M6 7h4v4l-3 5H4V11a4 4 0 0 1 2-4zM16 7h4v4l-3 5h-3V11a4 4 0 0 1 2-4z" /></>,
  };
  return <svg {...common}>{paths[name] || null}</svg>;
};

window.Icon = Icon;
export { Icon };
