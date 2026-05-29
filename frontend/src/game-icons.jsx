/* Shared icon set for the RPG console — minimal stroke icons.
   All icons are 24x24 viewBox, currentColor stroke. */
import React from 'react';

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
    // navigation / chrome
    menu: <><path d="M4 7h16M4 12h16M4 17h16" /></>,
    chevron_left: <path d="M14 6l-6 6 6 6" />,
    chevron_right: <path d="M10 6l6 6-6 6" />,
    chevron_down: <path d="M6 10l6 6 6-6" />,
    chevron_up: <path d="M6 14l6-6 6 6" />,
    close: <><path d="M6 6l12 12M18 6L6 18" /></>,
    plus: <><path d="M12 5v14M5 12h14" /></>,
    minus: <path d="M5 12h14" />,
    search: <><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></>,
    settings: <><path d="M19.4 14.6a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /><circle cx="12" cy="12" r="3" /></>,
    refresh: <><path d="M21 12a9 9 0 0 1-15 6.7L3 16" /><path d="M3 12a9 9 0 0 1 15-6.7L21 8" /><path d="M21 3v5h-5M3 21v-5h5" /></>,
    user: <><circle cx="12" cy="8" r="3.6" /><path d="M5 20c1.5-3.4 4.1-5 7-5s5.5 1.6 7 5" /></>,
    logo: <>
      <circle cx="6" cy="6" r="2.2" fill="currentColor" stroke="none" />
      <circle cx="18" cy="6" r="2.2" fill="currentColor" stroke="none" />
      <circle cx="12" cy="18" r="2.2" fill="currentColor" stroke="none" />
      <path d="M6.5 7.5 L11.5 17 M17.5 7.5 L12.5 17" strokeWidth="1.6" />
    </>,

    // platform nav
    home: <><path d="M4 11l8-7 8 7v9a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1z" /></>,
    book: <><path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v18H6.5A1.5 1.5 0 0 1 5 19.5z" /><path d="M9 3v18" /></>,
    play: <path d="M8 5v14l11-7z" />,
    branch: <><circle cx="6" cy="5" r="2" /><circle cx="6" cy="19" r="2" /><circle cx="18" cy="12" r="2" /><path d="M6 7v10M6 12h2a4 4 0 0 0 4-4V7a4 4 0 0 1 4-4" /></>,
    folder: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
    plug: <><path d="M10.5 3a1.5 1.5 0 0 0-1.5 1.5V7H6a2 2 0 0 0-2 2v3.5h2.5a1.5 1.5 0 0 1 0 3H4V19a2 2 0 0 0 2 2h3.5v-2.5a1.5 1.5 0 0 1 3 0V21H16a2 2 0 0 0 2-2v-3.5h2.5a1.5 1.5 0 0 0 0-3H18V9a2 2 0 0 0-2-2h-3v-2.5A1.5 1.5 0 0 0 11.5 3z" /></>,
    diamond: <><circle cx="6" cy="6" r="2" /><circle cx="18" cy="6" r="2" /><circle cx="12" cy="18" r="2" /><path d="M8 6h8M7.5 8l3 8M16.5 8l-3 8" /></>,
    spark: <><path d="M6 4h10a2 2 0 0 1 2 2v15l-7-4-7 4V6a2 2 0 0 1 2-2z" /><path d="M11 8l1 2 2 .4-1.5 1.4.4 2L11 13l-1.9 1 .4-2L8 10.4 10 10z" /></>,
    braces: <><path d="M9 4H7a2 2 0 0 0-2 2v3a2 2 0 0 1-2 2 2 2 0 0 1 2 2v3a2 2 0 0 0 2 2h2" /><path d="M15 4h2a2 2 0 0 1 2 2v3a2 2 0 0 0 2 2 2 2 0 0 0-2 2v3a2 2 0 0 1-2 2h-2" /></>,
    usage: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>,

    // right-panel tabs
    status: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    memory: <><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M8 4v16M16 4v16M4 8h16M4 16h16" /></>,
    world: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></>,
    cards: <><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="9" cy="11" r="2" /><path d="M5 17c1-2 2.5-3 4-3s3 1 4 3M14 9h5M14 12h4M14 15h3" /></>,
    timeline: <><circle cx="6" cy="6" r="2" /><circle cx="18" cy="18" r="2" /><circle cx="12" cy="12" r="2" /><path d="M8 6h2M14 12h2M14 18h2" /></>,
    context: <><path d="M5 4h14v5H5z" /><path d="M5 13h14v7H5z" /><path d="M9 16h6" /></>,
    debug: <><path d="M12 7v10M8 9l-2-2M16 9l2-2M8 15l-2 2M16 15l2 2" /><rect x="9" y="7" width="6" height="10" rx="3" /></>,

    // composer / actions
    send: <><path d="M5 12l14-7-4 14-3-6z" /></>,
    stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
    attach: <path d="M21 11.5l-9 9a5 5 0 1 1-7-7l9-9a3.5 3.5 0 0 1 5 5l-9 9a2 2 0 0 1-3-3l8-8" />,
    image: <><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="9" cy="11" r="1.6" /><path d="M21 17l-5-6-4 5-2-2-4 5" /></>,
    slash: <path d="M16 4 8 20" />,
    mic: <><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></>,
    sparkle: <><path d="M12 4v5M12 15v5M4 12h5M15 12h5" /></>,
    file: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></>,
    skill: <><path d="M5 12l3-7 4 5 4-3 3 9" /><path d="M3 19h18" /></>,
    diamond_sm: <><path d="M12 4 20 12 12 20 4 12z" /></>,

    // statuses
    check: <path d="M5 12l5 5 9-11" />,
    spinner: <><path d="M12 3a9 9 0 1 1-9 9" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 8v.01M12 11v5" /></>,
    warn: <><path d="M12 4l9 16H3z" /><path d="M12 10v4M12 17v.01" /></>,
    err: <><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></>,
    pin: <><path d="M12 3v7M8 10h8l-2 4h-4z" /><path d="M12 14v7" /></>,
    eye: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>,
    drag: <><circle cx="9" cy="6" r="1" /><circle cx="9" cy="12" r="1" /><circle cx="9" cy="18" r="1" /><circle cx="15" cy="6" r="1" /><circle cx="15" cy="12" r="1" /><circle cx="15" cy="18" r="1" /></>,
    arrow_right: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
    arrow_up: <><path d="M12 5v14M6 11l6-6 6 6" /></>,
    git_branch: <><circle cx="6" cy="6" r="2" /><circle cx="18" cy="18" r="2" /><circle cx="6" cy="18" r="2" /><path d="M6 8v8M8 6h6a4 4 0 0 1 4 4v6" /></>,
    save: <><path d="M5 5h11l3 3v11H5z" /><path d="M8 5v5h7V5M8 19v-5h8v5" /></>,
    history: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2M3 12a9 9 0 0 0 9 9" /></>,
    grid: <><rect x="4" y="4" width="7" height="7" /><rect x="13" y="4" width="7" height="7" /><rect x="4" y="13" width="7" height="7" /><rect x="13" y="13" width="7" height="7" /></>,
    list: <><path d="M8 6h12M8 12h12M8 18h12" /><circle cx="4" cy="6" r=".5" /><circle cx="4" cy="12" r=".5" /><circle cx="4" cy="18" r=".5" /></>,
    upload: <><path d="M12 16V4M6 10l6-6 6 6" /><path d="M4 20h16" /></>,
    download: <><path d="M12 4v12M6 14l6 6 6-6" /><path d="M4 4h16" /></>,
    more: <><circle cx="6" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="18" cy="12" r="1" /></>,
    trash: <><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /><path d="M10 11v6M14 11v6" /></>,
    edit: <><path d="M5 19h4l11-11-4-4L5 15z" /></>,
    link: <><path d="M10 14a4 4 0 0 0 5 .5l3-3a4 4 0 0 0-5.6-5.6L11 7" /><path d="M14 10a4 4 0 0 0-5-.5l-3 3a4 4 0 0 0 5.6 5.6L13 17" /></>,
    fork: <><circle cx="6" cy="5" r="2" /><circle cx="18" cy="5" r="2" /><circle cx="12" cy="19" r="2" /><path d="M6 7v3a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7M12 12v5" /></>,
    lock: <><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 1 1 8 0v3" /></>,
    unlock: <><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0" /></>,
    flag: <><path d="M5 21V4M5 4l11 1-2 5 3 4-12 .5" /></>,
    compass: <><circle cx="12" cy="12" r="9" /><path d="M15 9l-2 5-4 1 2-5z" /></>,
    quote: <><path d="M6 7h4v4l-3 5H4V11a4 4 0 0 1 2-4zM16 7h4v4l-3 5h-3V11a4 4 0 0 1 2-4z" /></>,
    eye_off: <><path d="M3 3l18 18" /><path d="M10.6 10.6a2 2 0 0 0 2.8 2.8M9 5.3A10 10 0 0 1 12 5c6 0 10 7 10 7a18 18 0 0 1-2.6 3.4M6.6 6.6A18 18 0 0 0 2 12s4 7 10 7c1.6 0 3-.4 4.3-1" /></>,
  };
  return <svg {...common}>{paths[name] || null}</svg>;
};

window.Icon = Icon;
export { Icon };
