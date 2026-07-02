/**
 * Generates Candlelit Grimoire app icons with zero dependencies (Node zlib only).
 * Renders an ink canvas with a radial candle-ember glow and a centered alchemical
 * rune mark, then writes valid PNGs at the sizes Expo prebuild expects.
 */
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

function crc32(buf) {
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td), 0);
  return Buffer.concat([len, td, crc]);
}
function encodePNG(width, height, rgba) {
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (width * 4 + 1)] = 0; // filter: none
    rgba.copy(raw, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4);
  }
  return Buffer.concat([
    sig,
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function mix(a, b, t) {
  return Math.round(a + (b - a) * t);
}

/**
 * Draw the icon into an RGBA buffer.
 * cx/cy/glow let us place the ember glow; markScale sizes the rune.
 */
function render(size, opts = {}) {
  const { transparent = false, glowX = 0.5, glowY = 0.42, mark = true } = opts;
  const buf = Buffer.alloc(size * size * 4);
  const cx = size * glowX;
  const cy = size * glowY;
  const maxR = size * 0.62;

  // ink palette
  const ink = [11, 9, 7];
  const inkDeep = [8, 6, 4];
  const ember = [255, 181, 92];
  const emberMid = [232, 146, 58];

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = (y * size + x) * 4;
      // base diagonal ink gradient
      const g = (x / size) * 0.5 + (y / size) * 0.5;
      let r = mix(ink[0], inkDeep[0], g);
      let gg = mix(ink[1], inkDeep[1], g);
      let b = mix(ink[2], inkDeep[2], g);

      // radial ember glow
      const dx = x - cx;
      const dy = y - cy;
      const dist = Math.sqrt(dx * dx + dy * dy) / maxR;
      const glow = Math.max(0, 1 - dist);
      const gpow = Math.pow(glow, 2.2);
      r = mix(r, emberMid[0], gpow * 0.85);
      gg = mix(gg, emberMid[1], gpow * 0.85);
      b = mix(b, emberMid[2], gpow * 0.7);
      const core = Math.pow(Math.max(0, 1 - dist * 1.8), 3);
      r = mix(r, ember[0], core);
      gg = mix(gg, ember[1], core);
      b = mix(b, ember[2], core);

      let alpha = 255;
      if (transparent) {
        // adaptive icon foreground: only the central mark + glow are opaque
        alpha = Math.round(Math.min(255, (gpow * 0.9 + core) * 400));
      }
      buf[i] = r;
      buf[i + 1] = gg;
      buf[i + 2] = b;
      buf[i + 3] = alpha;
    }
  }

  if (mark) {
    // Draw a thin ring + inscribed diamond (alchemical "fire" mark) in bright ember.
    const ringR = size * 0.26;
    const ringW = Math.max(2, size * 0.012);
    const mcx = size * 0.5;
    const mcy = size * 0.5;
    const brush = (px, py, strength) => {
      if (px < 0 || py < 0 || px >= size || py >= size) return;
      const i = (py * size + px) * 4;
      buf[i] = mix(buf[i], 255, strength);
      buf[i + 1] = mix(buf[i + 1], 200, strength);
      buf[i + 2] = mix(buf[i + 2], 120, strength);
      if (transparent) buf[i + 3] = Math.max(buf[i + 3], Math.round(255 * strength));
    };
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const dx = x - mcx;
        const dy = y - mcy;
        const d = Math.sqrt(dx * dx + dy * dy);
        const ring = 1 - Math.min(1, Math.abs(d - ringR) / ringW);
        if (ring > 0) brush(x, y, ring * 0.9);
        // diamond edges: |dx|+|dy| == ringR*0.78
        const diamond = 1 - Math.min(1, Math.abs(Math.abs(dx) + Math.abs(dy) - ringR * 0.82) / ringW);
        if (diamond > 0) brush(x, y, diamond * 0.85);
      }
    }
  }
  return buf;
}

const assetsDir = path.join(__dirname, "..", "assets");
fs.mkdirSync(assetsDir, { recursive: true });

const write = (name, size, opts) => {
  const png = encodePNG(size, size, render(size, opts));
  fs.writeFileSync(path.join(assetsDir, name), png);
  console.log(`  ✓ ${name} (${size}×${size}, ${(png.length / 1024).toFixed(1)} KB)`);
};

console.log("Generating Candlelit Grimoire assets…");
write("icon.png", 1024, {});
write("adaptive-icon.png", 1024, { transparent: true, glowY: 0.5 });
write("splash.png", 1284, { glowY: 0.5, mark: true });
write("favicon.png", 48, {});
console.log("Done.");
