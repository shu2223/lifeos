"use strict";
// On yishuapp.com the page lives under /life/ and Cloudflare Access signs every call in.
// Opened directly on the mini PC (http://127.0.0.1:8773/#token=lifeos_…) it uses that token.
const BASE = location.pathname.startsWith("/life/") ? "/life" : "";
const API = BASE + "/web/api/v1";
const AGENT_API = location.origin + BASE + "/api/v1";
const REPO = "https://github.com/shu2223/lifeos";

let token = sessionStorage.getItem("lifeos-token") || "";
{
  const m = location.hash.match(/^#token=(\S+)$/);
  if (m) {
    token = m[1];
    sessionStorage.setItem("lifeos-token", token);
    history.replaceState(null, "", location.pathname + "#/today");
  }
}

const $ = id => document.getElementById(id);
const state = {today: null, tz: "Asia/Tokyo", profile: {}, categories: {}, metrics: [], charts: [],
  records: {category: "", q: "", deleted: false}, trends: {range: 30, metric: "energy"}, foods: "",
  addKind: "meal"};

// ---------------------------------------------------------------------------------------------
// DOM helpers. Text from records always goes in as text nodes, never as HTML.

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  for (const kid of kids.flat(Infinity)) {
    if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}
function setAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k === "style") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
}
function s(tag, attrs, ...kids) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  setAttrs(el, attrs);
  for (const kid of kids.flat(Infinity)) {
    if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}
const fill = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(k => k != null && k !== false));
const button = (label, onclick, cls = "btn", attrs = {}) => h("button", {class: cls, type: "button", onclick, ...attrs}, label);
const card = (title, aside, ...body) => h("section", {class: "card"},
  h("div", {class: "card-head"}, h("h2", null, title), aside ? h("span", {class: "muted"}, aside) : null), ...body);

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 3200);
}
function issues(list) {
  const el = $("issues");
  el.replaceChildren(...list.map(([kind, text]) => h("li", null, h("span", {class: "dot " + kind}), h("span", null, text))));
  el.hidden = !list.length;
}

// ---------------------------------------------------------------------------------------------
// API

async function api(path, {method = "GET", body, raw, type} = {}) {
  const headers = {};
  if (token) headers.Authorization = "Bearer " + token;
  let payload = body;
  if (raw) { if (type) headers["Content-Type"] = type; }
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(API + path, {method, headers, body: payload, credentials: "same-origin"});
  const ctype = res.headers.get("content-type") || "";
  const data = ctype.includes("json") ? await res.json() : await res.blob();
  if (!res.ok) {
    const err = new Error(data?.error?.message || `请求失败（${res.status}）`);
    err.status = res.status;
    throw err;
  }
  return data;
}
const q = params => {
  const out = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== "" && v != null && v !== false) out.set(k, v);
  return out.toString();
};

// ---------------------------------------------------------------------------------------------
// Formatting

const WEEK = "日一二三四五六";
const fmt = (n, digits = 0) => n == null || Number.isNaN(Number(n)) ? "—"
  : Number(n).toLocaleString("zh-CN", {maximumFractionDigits: digits});
const parseDay = d => { const [y, m, dd] = d.split("-").map(Number); return new Date(Date.UTC(y, m - 1, dd)); };
const shiftDay = (d, n) => { const t = parseDay(d); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };
const dayLabel = (d, week = true) => { const t = parseDay(d); return `${t.getUTCMonth() + 1}月${t.getUTCDate()}日` + (week ? ` 周${WEEK[t.getUTCDay()]}` : ""); };
const shortDay = d => { const t = parseDay(d); return `${t.getUTCMonth() + 1}/${t.getUTCDate()}`; };
const relDay = d => d === state.today ? "今天" : d === shiftDay(state.today, -1) ? "昨天" : dayLabel(d);
const clock = iso => iso ? iso.slice(11, 16) : "";
const stamp = isoUtc => new Date(isoUtc).toLocaleString("zh-CN", {timeZone: state.tz, month: "numeric", day: "numeric",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
function ago(iso) {
  const min = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  if (min < 24 * 60) return `${Math.round(min / 60)} 小时前`;
  return `${Math.round(min / 1440)} 天前`;
}
function hm(minutes) {
  const m = Math.round(minutes);
  return m >= 60 ? `${Math.floor(m / 60)} 小时 ${m % 60} 分` : `${m} 分钟`;
}
function nowLocal() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {timeZone: state.tz, year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).formatToParts(new Date()).map(p => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

const MEAL = {breakfast: "早餐", lunch: "午餐", dinner: "晚餐", snack: "加餐", late_night: "夜宵", drink: "饮品", other: "其他"};
const ACTIVITY = {walking: "步行", running: "跑步", cycling: "骑行", swimming: "游泳", hiking: "徒步", yoga: "瑜伽",
  traditional_strength_training: "力量训练", functional_strength_training: "功能性力量训练", core_training: "核心训练",
  high_intensity_interval_training: "高强度间歇训练", elliptical: "椭圆机", rowing: "划船", stair_climbing: "爬楼",
  dance: "舞蹈", pilates: "普拉提", cooldown: "整理放松", mixed_cardio: "混合有氧", other: "运动"};
const NUTRIENTS = [["kcal", "热量", "kcal"], ["protein_g", "蛋白质", "g"], ["fat_g", "脂肪", "g"], ["carbs_g", "碳水", "g"],
  ["fiber_g", "膳食纤维", "g"], ["sugar_g", "糖", "g"], ["sodium_mg", "钠", "mg"], ["saturated_fat_g", "饱和脂肪", "g"],
  ["cholesterol_mg", "胆固醇", "mg"], ["caffeine_mg", "咖啡因", "mg"], ["alcohol_g", "酒精", "g"], ["water_ml", "水", "ml"]];
const FIELDS = {
  duration_min: ["时长", "分钟"], distance_km: ["距离", "km"], active_kcal: ["活动消耗", "kcal"], total_kcal: ["总消耗", "kcal"],
  avg_hr: ["平均心率", "次/分"], max_hr: ["最高心率", "次/分"], min_hr: ["最低心率", "次/分"],
  hr_recovery_1min: ["1 分钟心率恢复", "次/分"], hr_recovery_2min: ["2 分钟心率恢复", "次/分"], step_count: ["步数", "步"],
  step_cadence: ["步频", "步/分"], avg_speed_kmh: ["平均速度", "km/h"], speed_kmh: ["速度", "km/h"], max_speed_kmh: ["最高速度", "km/h"],
  elevation_up_m: ["爬升", "m"], elevation_down_m: ["下降", "m"], temperature_c: ["气温", "°C"], humidity_pct: ["湿度", "%"],
  effort_score: ["运动强度", "/10"], estimated_effort_score: ["估算运动强度", "/10"], intensity: ["强度", "MET"],
  flights_climbed: ["爬楼", "层"], rpe: ["主观疲劳", "/10"], location: ["地点", ""], indoor: ["室内", ""],
  device_source: ["设备", ""], activity_name: ["名称", ""], score: ["心情", "/10"], energy: ["精力", "/10"],
  stress: ["压力", "/10"], emotions: ["情绪", ""], reasons: ["原因", ""], severity: ["严重程度", "/10"], name: ["名称", ""],
  dose: ["剂量", ""], unit: ["单位", ""], kind: ["类型", ""], reason: ["原因", ""], amount: ["金额", ""], currency: ["币种", ""],
  merchant: ["商家", ""], body_part: ["部位", ""], quality: ["睡眠质量", "/5"], awakenings: ["醒来次数", "次"],
  place: ["地点", ""], payment_method: ["支付方式", ""],
  move_kcal: ["活动", "kcal"], move_goal_kcal: ["活动目标", "kcal"], exercise_min: ["锻炼", "分钟"],
  exercise_goal_min: ["锻炼目标", "分钟"], stand_hours: ["站立", "小时"], stand_goal_hours: ["站立目标", "小时"]};
const HIDDEN_FIELDS = new Set(["import_via", "hae_id", "metadata", "other_stats", "activity_type", "items", "totals",
  "meal_type", "estimated", "estimation_note", "text", "sets"]);
const SOURCES = {apple_health: "Apple 健康", manual: "手动", agent: "agent"};
const who = rec => rec.source === "agent" ? rec.created_by : SOURCES[rec.source] || rec.source;
const catTitle = name => state.categories[name]?.title || name;

function recordSide(rec) {
  const d = rec.data || {};
  if (rec.category === "meal" && d.totals?.kcal != null) return [fmt(d.totals.kcal), "kcal"];
  if (rec.category === "workout" && d.duration_min != null) return [fmt(d.duration_min), "分钟"];
  if (rec.category === "mood" && d.score != null) return [d.score, "/10"];
  if (rec.category === "expense" && d.amount != null) return [fmt(d.amount, 2), d.currency || ""];
  if (rec.category === "symptom" && d.severity != null) return [d.severity, "/10"];
  return null;
}
function recordSub(rec) {
  const d = rec.data || {};
  if (rec.category === "meal") return (d.items || []).map(i => i.name + (i.amount ? ` ${fmt(i.amount, 1)}${i.unit || ""}` : "")).join(" · ");
  if (rec.category === "workout") {
    return [d.active_kcal != null && `${fmt(d.active_kcal)} kcal`, d.distance_km && `${fmt(d.distance_km, 2)} km`,
      d.avg_hr && `平均心率 ${fmt(d.avg_hr)}`].filter(Boolean).join(" · ");
  }
  if (rec.category === "journal") return (d.text || "").slice(0, 80);
  if (rec.category === "mood") return rec.note || "";
  return rec.note || "";
}

// ---------------------------------------------------------------------------------------------
// Charts: thin marks, hairline grid, one axis, crosshair or per-bar tooltip, table view beside.

function niceScale(min, max, count = 4, zero = false) {
  if (zero) min = Math.min(0, min);
  if (min === max) { const pad = Math.abs(min) * 0.1 || 1; min -= pad; max += pad; }
  const raw = (max - min) / count, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(m => m >= raw);
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return {lo, hi, ticks};
}
const compact = n => Math.abs(n) >= 10000 ? fmt(n / 1000, 1) + "K" : fmt(n, Math.abs(n) < 10 ? 1 : 0);

function chartFrame(host, label) {
  host.classList.add("chart");
  const W = Math.max(260, host.clientWidth || 600);
  const tip = h("div", {class: "tooltip", hidden: true});
  return {W, tip, label};
}
function placeTip(host, tip, x, rows) {
  tip.replaceChildren(...rows);
  tip.hidden = false;
  const w = tip.offsetWidth;
  tip.style.left = Math.max(0, Math.min(host.clientWidth - w, x + 12)) + "px";
}
function xTickIndices(n, max = 6) {
  if (n <= max) return [...Array(n).keys()];
  const out = [];
  for (let i = 0; i < max; i++) out.push(Math.round(i * (n - 1) / (max - 1)));
  return [...new Set(out)];
}

// series: [{label, color, points: [{x, y, lo?, hi?}]}] with numeric x; xs shared or not.
function lineChart(host, o) {
  const {W, tip} = chartFrame(host);
  const H = o.height || 200, padL = 40, padR = 48, padT = 10, padB = 24;
  const pts = o.series.flatMap(sr => sr.points.filter(p => p.y != null));
  if (!pts.length) { host.replaceChildren(h("p", {class: "empty"}, "这段时间没有数据。")); return; }
  const xs = [...new Set(o.series.flatMap(sr => sr.points.map(p => p.x)))].sort((a, b) => a - b);
  const minX = xs[0], maxX = xs[xs.length - 1];
  const ys = pts.flatMap(p => [p.y, p.lo, p.hi]).concat(o.goal ?? []).filter(v => v != null);
  const {lo, hi, ticks} = niceScale(Math.min(...ys), Math.max(...ys), 4, o.zero);
  const X = x => padL + (maxX === minX ? (W - padL - padR) / 2 : (x - minX) / (maxX - minX) * (W - padL - padR));
  const Y = y => padT + (1 - (y - lo) / (hi - lo)) * (H - padT - padB);
  const svg = s("svg", {viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.label, tabindex: 0});
  for (const t of ticks) {
    svg.append(s("line", {class: "grid", x1: padL, x2: W - padR, y1: Y(t), y2: Y(t)}),
      s("text", {class: "tick", x: padL - 6, y: Y(t) + 4, "text-anchor": "end"}, compact(t)));
  }
  const tickXs = o.xTicks || xTickIndices(xs.length, Math.max(2, Math.floor((W - padL - padR) / 70))).map(i => xs[i]);
  tickXs.forEach((x, k) => svg.append(s("text", {class: "tick", x: X(x), y: H - 6,
    "text-anchor": tickXs.length === 1 ? "middle" : k === 0 ? "start" : k === tickXs.length - 1 ? "end" : "middle"}, o.xFmt(x))));
  if (o.goal != null) {
    svg.append(s("line", {class: "crosshair", x1: padL, x2: W - padR, y1: Y(o.goal), y2: Y(o.goal)}),
      s("text", {class: "tick", x: padL + 4, y: Y(o.goal) - 4}, "目标 " + compact(o.goal)));
  }
  for (const m of o.markers || []) {
    svg.append(s("line", {class: "grid", x1: X(m.x), x2: X(m.x), y1: padT, y2: H - padB}),
      s("text", {class: "tick", x: X(m.x) + 4, y: padT + 10}, m.label));
  }
  const ends = [];
  for (const sr of o.series) {
    const band = sr.points.filter(p => p.lo != null && p.hi != null);
    if (band.length > 1) {
      const d = band.map((p, i) => `${i ? "L" : "M"}${X(p.x)},${Y(p.hi)}`).join("") +
        band.slice().reverse().map(p => `L${X(p.x)},${Y(p.lo)}`).join("") + "Z";
      svg.append(s("path", {d, style: {fill: sr.color, fillOpacity: 0.12, stroke: "none"}}));
    }
    let d = "", pen = false;
    for (const p of sr.points) {
      if (p.y == null) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${X(p.x)},${Y(p.y)}`;
      pen = true;
    }
    svg.append(s("path", {d, style: {fill: "none", stroke: sr.color, strokeWidth: 2, strokeLinejoin: "round", strokeLinecap: "round"}}));
    const last = [...sr.points].reverse().find(p => p.y != null);
    if (last) {
      svg.append(s("circle", {cx: X(last.x), cy: Y(last.y), r: 4, style: {fill: sr.color, stroke: "var(--surface)", strokeWidth: 2}}));
      ends.push({y: Y(last.y), x: X(last.x), text: o.yFmt(last.y)});
    }
  }
  ends.sort((a, b) => a.y - b.y).forEach((e, i, all) => {
    if (i && Math.abs(e.y - all[i - 1].y) < 14) return;  // colliding end labels: legend and tooltip carry it
    svg.append(s("text", {class: "end-label", x: e.x + 8, y: e.y + 4}, e.text));
  });
  const cross = s("line", {class: "crosshair", y1: padT, y2: H - padB, visibility: "hidden"});
  const hit = s("rect", {x: padL, y: padT, width: W - padL - padR, height: H - padT - padB, fill: "transparent"});
  svg.append(cross, hit);
  let at = -1;
  const show = i => {
    at = Math.max(0, Math.min(xs.length - 1, i));
    const x = xs[at];
    cross.setAttribute("x1", X(x)); cross.setAttribute("x2", X(x)); cross.setAttribute("visibility", "visible");
    const rows = [h("span", {class: "when"}, o.xTip(x))];
    for (const sr of o.series) {
      const p = sr.points.find(q => q.x === x);
      if (p?.y == null) continue;
      const extra = p.lo != null ? `（${o.yFmt(p.lo)}–${o.yFmt(p.hi)}）` : "";
      rows.push(h("span", {class: "val"}, h("i", {style: {background: sr.color}}), h("strong", null, o.yFmt(p.y) + (o.unit ? " " + o.unit : "")),
        o.series.length > 1 ? h("span", {class: "muted"}, sr.label) : null, extra ? h("span", {class: "muted"}, extra) : null));
    }
    placeTip(host, tip, X(x) * host.clientWidth / W, rows);
  };
  const hide = () => { cross.setAttribute("visibility", "hidden"); tip.hidden = true; };
  hit.addEventListener("pointermove", e => {
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * W / r.width;
    let best = 0;
    xs.forEach((x, i) => { if (Math.abs(X(x) - px) < Math.abs(X(xs[best]) - px)) best = i; });
    show(best);
  });
  hit.addEventListener("pointerleave", hide);
  svg.addEventListener("keydown", e => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); show(at < 0 ? xs.length - 1 : at + (e.key === "ArrowRight" ? 1 : -1)); }
  });
  svg.addEventListener("blur", hide);
  host.replaceChildren(svg, tip);
}

// points: [{key, label, y}] one per category slot; columns <= 24px with a 4px rounded cap.
function barChart(host, o) {
  const {W, tip} = chartFrame(host);
  const H = o.height || 200, padL = 40, padR = 12, padT = 10, padB = 24;
  const n = o.points.length;
  if (!o.points.some(p => p.y)) { host.replaceChildren(h("p", {class: "empty"}, "这段时间没有数据。")); return; }
  const max = Math.max(...o.points.map(p => p.y || 0), o.goal || 0);
  const {hi, ticks} = niceScale(0, max, 4, true);
  const slot = (W - padL - padR) / n, bw = Math.max(1, Math.min(24, slot - 2));
  const Y = y => padT + (1 - y / hi) * (H - padT - padB), base = Y(0);
  const svg = s("svg", {viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.label, tabindex: 0});
  for (const t of ticks) {
    svg.append(s("line", {class: "grid", x1: padL, x2: W - padR, y1: Y(t), y2: Y(t)}),
      s("text", {class: "tick", x: padL - 6, y: Y(t) + 4, "text-anchor": "end"}, compact(t)));
  }
  const bars = o.points.map((p, i) => {
    const x = padL + i * slot + (slot - bw) / 2, top = Y(p.y || 0), hgt = base - top, r = Math.min(4, bw / 2, hgt);
    const d = hgt <= 0 ? "" : `M${x},${base}V${top + r}Q${x},${top} ${x + r},${top}H${x + bw - r}Q${x + bw},${top} ${x + bw},${top + r}V${base}Z`;
    const bar = s("path", {class: "bar", d, style: {fill: o.color}});
    svg.append(bar);
    return bar;
  });
  if (o.goal) {
    svg.append(s("line", {class: "crosshair", x1: padL, x2: W - padR, y1: Y(o.goal), y2: Y(o.goal)}),
      s("text", {class: "tick", x: W - padR, y: Y(o.goal) - 4, "text-anchor": "end"}, "目标 " + compact(o.goal)));
  }
  const idx = xTickIndices(n, Math.max(2, Math.floor((W - padL - padR) / 60)));
  idx.forEach(i => svg.append(s("text", {class: "tick", x: padL + (i + 0.5) * slot, y: H - 6, "text-anchor": "middle"}, o.points[i].label)));
  let at = -1;
  const show = i => {
    at = Math.max(0, Math.min(n - 1, i));
    bars.forEach((b, k) => b.classList.toggle("active", k === at));
    const p = o.points[at];
    placeTip(host, tip, (padL + (at + 0.5) * slot) * host.clientWidth / W, [h("span", {class: "when"}, p.tip || p.label),
      h("span", {class: "val"}, h("i", {style: {background: o.color}}), h("strong", null, p.y == null ? "无数据" : o.yFmt(p.y) + (o.unit ? " " + o.unit : "")))]);
  };
  const hide = () => { bars.forEach(b => b.classList.remove("active")); tip.hidden = true; };
  o.points.forEach((p, i) => {
    const r = s("rect", {x: padL + i * slot, y: padT, width: slot, height: H - padT - padB, fill: "transparent"});
    r.addEventListener("pointerenter", () => show(i));
    r.addEventListener("pointerleave", hide);
    svg.append(r);
  });
  svg.addEventListener("keydown", e => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); show(at < 0 ? n - 1 : at + (e.key === "ArrowRight" ? 1 : -1)); }
  });
  svg.addEventListener("blur", hide);
  host.replaceChildren(svg, tip);
}
function chart(draw) {
  const host = h("div");
  state.charts.push(() => draw(host));
  requestAnimationFrame(() => draw(host));
  return host;
}
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => state.charts.forEach(draw => draw()), 150);
});

function meter(ratio, overIsBad = false) {
  const span = h("span", {class: overIsBad && ratio > 1 ? "over" : null});
  span.style.width = Math.min(100, Math.max(0, ratio * 100)) + "%";
  return h("div", {class: "meter", role: "presentation"}, span);
}
function tile(label, value, unit, note, ratio, overIsBad) {
  return h("div", {class: "tile"}, h("span", {class: "label"}, label),
    h("span", {class: "value"}, value, unit ? h("span", {class: "unit"}, unit) : null),
    note ? h("span", {class: "note"}, note) : null, ratio != null ? meter(ratio, overIsBad) : null);
}
const SLEEP_STAGES = [["deep_min", "深睡", "stage-deep"], ["core_min", "核心", "stage-core"], ["rem_min", "快速眼动", "stage-rem"],
  ["awake_min", "清醒", "stage-awake"]];
function sleepBar(sl) {
  const parts = SLEEP_STAGES.filter(([k]) => sl[k] > 0);
  const total = parts.reduce((a, [k]) => a + sl[k], 0);
  if (!total) return null;
  const bar = h("div", {class: "sleepbar", role: "img", "aria-label": parts.map(([k, l]) => `${l} ${hm(sl[k])}`).join("，")},
    parts.map(([k, , cls]) => { const sp = h("span", {class: cls}); sp.style.flex = `${sl[k]} 1 0`; return sp; }));
  const legend = h("div", {class: "legend"}, parts.map(([k, l, cls]) => h("span", null, h("i", {class: cls}), `${l} ${hm(sl[k])}`)),
    sl.asleep_unstaged_min ? h("span", {class: "muted"}, `未分期 ${hm(sl.asleep_unstaged_min)}`) : null);
  return [bar, legend];
}

// ---------------------------------------------------------------------------------------------
// Views

const views = {today: viewDay, day: viewDay, records: viewRecords, record: viewRecords, trends: viewTrends,
  foods: viewFoods, settings: viewSettings};

async function route() {
  const m = location.hash.match(/^#\/(\w+)(?:\/(.+))?$/);
  const name = m && views[m[1]] ? m[1] : "today", arg = m?.[2] && decodeURIComponent(m[2]);
  const tab = {day: "today", record: "records"}[name] || name;
  for (const a of $("tabs").querySelectorAll("a")) {
    if (a.dataset.view === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  const view = $("view");
  view.classList.add("loading");
  try {
    state.charts = [];
    const content = await views[name](name === "day" ? arg : name === "record" ? null : arg);
    fill(view, content);
    if (name === "record" && arg) openRecord(arg);
  } catch (err) {
    view.replaceChildren(h("section", {class: "card"}, h("p", {class: "bad"}, err.message)));
  } finally {
    view.classList.remove("loading");
  }
}
const rerender = () => route();

async function sleepWindow(day) {
  const lists = await Promise.all(["sleep_core", "sleep_asleep", "sleep_in_bed", "sleep_deep"].map(m =>
    api("/samples?" + q({metric: m, from: day, to: day, limit: 50})).then(r => r.samples).catch(() => [])));
  const all = lists.flat();
  if (!all.length) return null;
  return {start: all.map(x => x.start).sort()[0], end: all.map(x => x.end).sort().at(-1)};
}

async function viewDay(day) {
  day = day && /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : state.today;
  const [sum, list] = await Promise.all([api("/summary/day?date=" + day), api("/records?" + q({date: day, order: "asc", limit: 500}))]);
  const win = sum.sleep ? await sleepWindow(day) : null;
  const recs = list.records, goals = sum.goals || {}, intake = sum.nutrition.intake, act = sum.activity, rings = sum.rings || {};
  const go = d => { location.hash = d === state.today ? "#/today" : "#/day/" + d; };

  const dateInput = h("input", {type: "date", value: day, max: state.today, "aria-label": "跳到日期",
    onchange: e => e.target.value && go(e.target.value)});
  const daybar = h("div", {class: "daybar"},
    button("‹", () => go(shiftDay(day, -1)), "btn icon", {"aria-label": "前一天"}),
    h("span", {class: "date"}, dayLabel(day)),
    button("›", () => go(shiftDay(day, 1)), "btn icon", {"aria-label": "后一天", disabled: day >= state.today}),
    dateInput, day !== state.today ? button("回到今天", () => go(state.today), "btn small") : null);

  const tiles = [];
  const kcal = intake.kcal || 0, kGoal = goals.kcal;
  const burned = sum.energy_balance.burned_kcal;
  tiles.push(tile("摄入", fmt(kcal), "kcal",
    kGoal ? (kcal <= kGoal ? `目标 ${fmt(kGoal)} · 还剩 ${fmt(kGoal - kcal)}` : `目标 ${fmt(kGoal)} · 超出 ${fmt(kcal - kGoal)}`)
      : burned ? `消耗 ${fmt(burned)}` : "还没设热量目标", kGoal ? kcal / kGoal : null, true));
  if (act.active_kcal != null) {
    tiles.push(tile("活动消耗", fmt(act.active_kcal), "kcal", rings.move_goal_kcal ? `目标 ${fmt(rings.move_goal_kcal)}`
      : act.basal_kcal ? `静息 ${fmt(act.basal_kcal)}` : null, rings.move_goal_kcal ? act.active_kcal / rings.move_goal_kcal : null));
  }
  if (act.steps != null) {
    tiles.push(tile("步数", fmt(act.steps), "", [goals.steps && `目标 ${fmt(goals.steps)}`, act.distance_km && `${fmt(act.distance_km, 1)} km`]
      .filter(Boolean).join(" · "), goals.steps ? act.steps / goals.steps : null));
  }
  const exGoal = rings.exercise_goal_min || goals.exercise_min;
  if (act.exercise_min != null) tiles.push(tile("锻炼", fmt(act.exercise_min), "分钟", exGoal ? `目标 ${fmt(exGoal)}` : null, exGoal ? act.exercise_min / exGoal : null));
  if (act.stand_hours != null) tiles.push(tile("站立", fmt(act.stand_hours), "小时", rings.stand_goal_hours ? `目标 ${fmt(rings.stand_goal_hours)}` : null,
    rings.stand_goal_hours ? act.stand_hours / rings.stand_goal_hours : null));
  if (sum.sleep?.asleep_min) {
    tiles.push(tile("睡眠", fmt(sum.sleep.asleep_min / 60, 1), "小时", win ? `${clock(win.start)} 入睡 · ${clock(win.end)} 醒来` : null,
      goals.sleep_hours ? sum.sleep.asleep_min / 60 / goals.sleep_hours : null));
  }
  if (act.resting_hr != null) tiles.push(tile("静息心率", fmt(act.resting_hr), "次/分", act.hrv_ms ? `心率变异性 ${fmt(act.hrv_ms)} ms` : null));
  if (sum.body.weight_kg != null) {
    tiles.push(tile("体重", fmt(sum.body.weight_kg, 1), "kg", sum.body.weight_date === day ? "当天测量" : `${relDay(sum.body.weight_date)}测量`));
  }

  // Meals
  const meals = recs.filter(r => r.category === "meal");
  const macro = (key, label) => {
    const v = intake[key] || 0, g = goals[key];
    return h("div", {class: "macro"}, h("div", {class: "line"}, h("span", {class: "muted"}, label),
      h("span", null, h("strong", null, fmt(v)), g ? h("span", {class: "muted"}, ` / ${fmt(g)} g`) : " g")), meter(g ? v / g : 0, true));
  };
  const mealCard = card("饮食", meals.length ? `${meals.length} 次 · ${fmt(kcal)} kcal` : null,
    h("div", {class: "macros"}, macro("protein_g", "蛋白质"), macro("fat_g", "脂肪"), macro("carbs_g", "碳水")),
    meals.length ? recordRows(meals, {mealLabel: true}) : h("p", {class: "empty"}, "还没有记录。对 agent 说一句“我中午吃了……”，就会出现在这里。"));

  const right = [];
  if (sum.sleep?.asleep_min) {
    right.push(card("睡眠", win ? `${clock(win.start)} – ${clock(win.end)}` : null,
      h("p", null, h("strong", {class: "num"}, hm(sum.sleep.asleep_min)), h("span", {class: "muted"}, " 睡着")), sleepBar(sum.sleep)));
  }
  const workouts = recs.filter(r => r.category === "workout");
  if (workouts.length) right.push(card("运动", `${workouts.length} 次`, recordRows(workouts, {range: true})));
  const others = recs.filter(r => !["meal", "workout", "activity_summary"].includes(r.category));
  right.push(card("其他记录", null, others.length ? recordRows(others, {chip: true})
    : h("p", {class: "empty"}, "心情、日记、用药、症状、花销都会出现在这里。")));

  return [daybar, h("section", {class: "tiles", "aria-label": "当天概览"}, tiles),
    h("div", {class: "columns"}, h("div", {class: "stack"}, mealCard), h("div", {class: "stack"}, right))];
}

const workoutName = d => (ACTIVITY[d.activity_type] && d.activity_type !== "other" ? ACTIVITY[d.activity_type] : d.activity_name || "运动")
  + (d.indoor ? "（室内）" : "");
function shortTitle(rec) {
  if (rec.category === "workout") return workoutName(rec.data || {});
  if (rec.category === "mood") return (rec.data?.emotions || []).join("、") || "心情";
  const prefix = catTitle(rec.category) + "：";
  return rec.title.startsWith(prefix) ? rec.title.slice(prefix.length) : rec.title;
}
function recordRows(recs, opts = {}) {
  return h("ul", {class: "rows"}, recs.map(rec => {
    const side = recordSide(rec);
    const label = opts.mealLabel ? (MEAL[rec.data?.meal_type] || "饮食") : null;
    const title = opts.mealLabel ? (rec.data.items || []).map(i => i.name).join("、") || rec.title : shortTitle(rec);
    return h("li", null, h("button", {class: "row" + (rec.deleted_at ? " deleted" : ""), type: "button", onclick: () => openRecord(rec.id)},
      h("span", {class: "time"}, clock(rec.start), opts.range && rec.end ? h("br") : null, opts.range && rec.end ? clock(rec.end) : null),
      h("span", {class: "main"},
        h("span", {class: "title"}, opts.chip ? h("span", {class: "chip"}, catTitle(rec.category)) : null,
          label ? h("span", {class: "chip"}, label) : null, title),
        recordSub(rec) ? h("span", {class: "sub"}, recordSub(rec)) : null),
      side ? h("span", {class: "side"}, side[0], h("span", {class: "unit"}, side[1])) : h("span")));
  }));
}

// --- records list ------------------------------------------------------------------------------

async function viewRecords() {
  const f = state.records;
  const cats = await api("/categories");
  const listHost = h("div");
  const filterButtons = h("div", {class: "filters", role: "group", "aria-label": "按类别筛选"},
    [["", "全部"], ...cats.categories.filter(c => c.records > 0).map(c => [c.name, c.title])].map(([name, title]) =>
      h("button", {type: "button", "aria-pressed": String(f.category === name), onclick: () => { f.category = name; rerender(); }}, title)));
  let timer;
  const search = h("input", {type: "search", placeholder: "搜索标题、备注、内容", value: f.q, "aria-label": "搜索记录",
    oninput: e => { clearTimeout(timer); timer = setTimeout(() => { f.q = e.target.value.trim(); load(true); }, 300); }});
  const deleted = h("label", {class: "small muted"}, h("input", {type: "checkbox", checked: f.deleted,
    onchange: e => { f.deleted = e.target.checked; load(true); }}), " 显示已删除");
  let cursor = null, shown = [];
  async function load(reset) {
    if (reset) { cursor = null; shown = []; }
    const page = await api("/records?" + q({category: f.category, q: f.q, include_deleted: f.deleted, limit: 50, cursor}));
    shown = shown.concat(page.records);
    cursor = page.next_cursor;
    const groups = new Map();
    for (const r of shown) {
      if (!groups.has(r.local_date)) groups.set(r.local_date, []);
      groups.get(r.local_date).push(r);
    }
    fill(listHost,
      shown.length ? [...groups].map(([d, rs]) => h("div", {class: "day-group"},
        h("h3", null, h("a", {href: d === state.today ? "#/today" : "#/day/" + d, class: "back"}, relDay(d))),
        recordRows(rs, {chip: !f.category, range: f.category === "workout"})))
        : h("p", {class: "empty"}, f.q ? "没有找到匹配的记录。" : "还没有记录。"),
      cursor ? button("加载更多", () => load(false), "btn more") : null);
  }
  await load(true);
  return [h("div", {class: "filter-row"}, filterButtons, h("div", {class: "toolbar"}, deleted, search)), h("section", {class: "card"}, listHost)];
}

// --- record detail -------------------------------------------------------------------------------

async function openRecord(id) {
  const dlg = $("recordDialog"), body = $("recordBody");
  body.replaceChildren(h("p", {class: "muted"}, "正在读取…"));
  if (!dlg.open) dlg.showModal();
  let rec;
  try { rec = await api("/records/" + encodeURIComponent(id)); }
  catch (err) { body.replaceChildren(h("p", {class: "bad"}, err.message)); return; }
  $("recordTitle").textContent = rec.title;
  $("recordMeta").textContent = [catTitle(rec.category), `${relDay(rec.local_date)} ${clock(rec.start)}${rec.end ? "–" + clock(rec.end) : ""}`,
    `${who(rec)}记录`].join(" · ");
  const parts = [];
  if (rec.deleted_at) parts.push(h("div", {class: "note-box"}, "这条记录已删除，不计入统计。"));
  if (rec.category === "meal") parts.push(mealDetail(rec));
  else if (rec.category === "workout") parts.push(...workoutDetail(rec));
  else if (rec.category === "journal") parts.push(h("p", {class: "journal"}, rec.data.text || ""));
  const facts = factList(rec.data, rec.category);
  if (facts) parts.push(facts);
  const extra = Object.fromEntries(["other_stats", "metadata", "sets"].filter(k => rec.data[k] && Object.keys(rec.data[k]).length).map(k => [k, rec.data[k]]));
  if (Object.keys(extra).length) parts.push(h("details", null, h("summary", null, "更多数据"), h("pre", {class: "mono"}, JSON.stringify(extra, null, 2))));
  if (rec.attachments?.length) parts.push(attachmentSection(rec));

  const note = h("textarea", {rows: 2, placeholder: "备注"});
  note.value = rec.note || "";
  const title = h("input", {type: "text", value: rec.title, "aria-label": "标题"});
  parts.push(h("div", {class: "section"}, h("h3", null, "标题和备注"), title, note,
    h("div", {class: "actions"}, button("保存", async () => {
      await guarded(() => api("/records/" + rec.id, {method: "PATCH", body: {title: title.value.trim() || null, note: note.value}}));
      toast("已保存"); openRecord(rec.id); rerender();
    }, "btn small"))));

  const raw = h("textarea", {rows: 8, class: "mono", spellcheck: "false"});
  raw.value = JSON.stringify(rec.data, null, 2);
  parts.push(h("details", {class: "section"}, h("summary", null, "编辑数据（JSON）"), raw,
    h("div", {class: "actions"}, button("保存数据", async () => {
      let data;
      try { data = JSON.parse(raw.value); } catch { toast("不是有效的 JSON"); return; }
      const patch = {...data};
      for (const k of Object.keys(rec.data)) if (!(k in data)) patch[k] = null;  // removed keys
      await guarded(() => api("/records/" + rec.id, {method: "PATCH", body: {data: patch}}));
      toast("已保存"); openRecord(rec.id); rerender();
    }, "btn small"), h("span", {class: "muted small"}, "饮食的合计会自动重算。"))));

  const history = h("ol", {class: "history"}, h("li", null, h("span", {class: "muted"}, "正在读取…")));
  parts.push(h("details", {class: "section"}, h("summary", null, "修改历史"), history));
  api("/changes?" + q({entity_id: rec.id, limit: 50})).then(r => history.replaceChildren(...r.changes.map(c =>
    h("li", null, h("time", null, stamp(c.at)), h("span", null, `${c.actor} ${ACTIONS[c.action] || c.action}`)))))
    .catch(err => history.replaceChildren(h("li", null, h("span", {class: "bad"}, err.message))));

  parts.push(h("div", {class: "dialog-foot"}, rec.deleted_at
    ? button("恢复", async () => { await guarded(() => api(`/records/${rec.id}/restore`, {method: "POST"})); toast("已恢复"); openRecord(rec.id); rerender(); })
    : button("删除", async () => {
      if (!confirm("删除这条记录？之后可以在“记录”里勾选“显示已删除”找回。")) return;
      await guarded(() => api("/records/" + rec.id, {method: "DELETE"}));
      toast("已删除"); dlg.close(); rerender();
    }, "btn danger")));
  fill(body, parts);
}
const ACTIONS = {create: "创建", update: "修改", delete: "删除", restore: "恢复", import: "导入", revoke: "吊销"};

const DIGITS = {distance_km: 2, avg_speed_kmh: 1, speed_kmh: 1, max_speed_kmh: 1, intensity: 1, step_cadence: 0};
const WORKOUT_ORDER = ["duration_min", "distance_km", "active_kcal", "total_kcal", "avg_hr", "max_hr", "min_hr", "hr_recovery_1min",
  "hr_recovery_2min", "step_count", "step_cadence", "avg_speed_kmh", "speed_kmh", "max_speed_kmh", "elevation_up_m", "elevation_down_m",
  "effort_score", "estimated_effort_score", "intensity", "rpe", "temperature_c", "humidity_pct", "device_source"];
function factList(data, category) {
  const simple = v => v !== "" && v != null && (typeof v !== "object" || Array.isArray(v) && v.length && v.every(x => typeof x !== "object"));
  let rows = Object.entries(data || {}).filter(([k, v]) => !HIDDEN_FIELDS.has(k) && simple(v));
  if (category === "workout") {
    rows = rows.filter(([k]) => WORKOUT_ORDER.includes(k)).sort(([a], [b]) => WORKOUT_ORDER.indexOf(a) - WORKOUT_ORDER.indexOf(b));
  }
  if (!rows.length) return null;
  return h("dl", {class: "facts" + (rows.length > 6 ? " two" : "")}, rows.flatMap(([k, v]) => {
    const [label, unit] = FIELDS[k] || [k, ""];
    const digits = DIGITS[k] ?? (Math.abs(v) >= 10 ? 0 : 1);
    const text = Array.isArray(v) ? v.join("、") : typeof v === "boolean" ? (v ? "是" : "否") : typeof v === "number" ? fmt(v, digits) : v;
    return [h("dt", null, label), h("dd", null, text, unit ? h("span", {class: "muted"}, " " + unit) : null)];
  }));
}

function mealDetail(rec) {
  const d = rec.data, cols = NUTRIENTS.slice(0, 4);
  const table = h("table", null,
    h("thead", null, h("tr", null, h("th", null, "食物"), h("th", null, "份量"), cols.map(([, l, u]) => h("th", {class: "right"}, `${l}（${u}）`)))),
    h("tbody", null, (d.items || []).map(i => h("tr", null, h("td", null, i.name, i.confidence ? h("span", {class: "muted small"}, ` 估算·${{low: "低", medium: "中", high: "高"}[i.confidence] || i.confidence}`) : null),
      h("td", null, [i.amount != null && `${fmt(i.amount, 1)}${i.unit || ""}`, i.grams != null && `${fmt(i.grams)} g`].filter(Boolean).join(" · ")),
      cols.map(([k]) => h("td", {class: "right"}, fmt(i[k], 1)))))),
    h("tfoot", null, h("tr", null, h("td", {colspan: 2}, "合计"), cols.map(([k]) => h("td", {class: "right"}, fmt(d.totals?.[k], 1))))));
  const more = NUTRIENTS.slice(4).filter(([k]) => d.totals?.[k] != null);
  return h("div", {class: "dialog-stack"},
    h("p", {class: "muted small"}, [MEAL[d.meal_type], d.estimated ? "数值为估算" : null].filter(Boolean).join(" · ")),
    h("div", {class: "scroll-x"}, table),
    more.length ? h("p", {class: "muted small"}, more.map(([k, l, u]) => `${l} ${fmt(d.totals[k], 1)} ${u}`).join(" · ")) : null,
    d.estimation_note ? h("p", {class: "muted small"}, "估算依据：" + d.estimation_note) : null);
}

function workoutDetail(rec) {
  const host = h("div", {class: "dialog-stack"}, h("p", {class: "muted small"}, "正在读取心率…"));
  const startTs = Date.parse(rec.start), endTs = rec.end ? Date.parse(rec.end) : null;
  api(`/records/${rec.id}/series`).then(({series}) => {
    const hr = series.find(x => x.name === "heart_rate"), rec2 = series.find(x => x.name === "heart_rate_recovery");
    if (!hr) { host.replaceChildren(); return; }
    const toPts = sr => sr.points.map(p => sr.kind === "range" ? {x: Date.parse(p[0]), y: p[2], lo: p[1], hi: p[3]} : {x: Date.parse(p[0]), y: p[1]});
    const points = toPts(hr).concat(rec2 ? toPts(rec2) : []);
    const mins = x => `${Math.round((x - startTs) / 60000)} 分`;
    const span = (points.at(-1).x - startTs) / 60000, step = [1, 2, 5, 10, 15, 30, 60].find(m => span / m <= 6) || 120;
    const xTicks = [];
    for (let m = 0; m <= span; m += step) xTicks.push(startTs + m * 60000);
    const table = h("details", null, h("summary", {class: "small"}, "心率数据表"), h("div", {class: "scroll-x"}, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "时间"), h("th", {class: "right"}, "平均"), h("th", {class: "right"}, "范围"))),
      h("tbody", null, points.map(p => h("tr", null, h("td", null, new Date(p.x).toLocaleTimeString("zh-CN", {timeZone: state.tz})),
        h("td", {class: "right"}, fmt(p.y)), h("td", {class: "right"}, p.lo != null ? `${fmt(p.lo)}–${fmt(p.hi)}` : "")))))));
    host.replaceChildren(h("div", {class: "chart-head"}, h("h3", null, "心率（次/分）"),
      h("span", {class: "stats"}, rec2 ? "结束后两分钟的恢复也画在里面" : "")),
    chart(el => lineChart(el, {label: "运动中的心率", series: [{label: "心率", color: "var(--series-1)", points}], unit: "次/分",
      yFmt: v => fmt(v), xFmt: mins, xTip: x => new Date(x).toLocaleTimeString("zh-CN", {timeZone: state.tz}),
      markers: endTs && rec2 ? [{x: endTs, label: "结束"}] : [], xTicks, height: 180})), table);
  }).catch(() => host.replaceChildren());
  return [h("p", {class: "muted small"}, workoutName(rec.data)), host];
}

function attachmentSection(rec) {
  const thumbs = h("div", {class: "thumbs"}), files = h("ul", {class: "history"});
  for (const a of rec.attachments) {
    if (a.mime.startsWith("image/")) {
      const img = h("img", {alt: a.filename});
      api("/attachments/" + a.id).then(blob => { img.src = URL.createObjectURL(blob); });
      thumbs.append(img);
    } else {
      files.append(h("li", null, h("span", {class: "muted"}, `${fmt(a.size / 1024)} KB`),
        h("button", {class: "link", type: "button", onclick: () => download("/attachments/" + a.id, a.filename)}, a.filename)));
    }
  }
  return h("div", {class: "section"}, h("h3", null, "附件"), thumbs.children.length ? thumbs : null, files.children.length ? files : null);
}

async function download(path, filename) {
  try {
    const data = await api(path);
    const blob = data instanceof Blob ? data : new Blob([JSON.stringify(data, null, 2)], {type: "application/json"});
    const a = h("a", {href: URL.createObjectURL(blob), download: filename});
    document.body.append(a); a.click(); a.remove();
  } catch (err) { toast(err.message); }
}
async function guarded(fn) {
  try { return await fn(); } catch (err) { toast(err.message); throw err; }
}

// --- trends ----------------------------------------------------------------------------------

const UNIT_LABELS = {"count": "", "count/min": "次/分", "min": "分钟", "hr": "小时", "degC": "°C", "mL": "ml",
  "km/hr": "km/h", "appleEffortScore": "", "dBASPL": "dB"};
const TREND_METRICS = [["energy", "热量"], ["body_mass", "体重"], ["step_count", "步数"], ["sleep", "睡眠"],
  ["exercise_time", "锻炼"], ["resting_heart_rate", "静息心率"], ["heart_rate_variability", "心率变异性"]];
const RANGES = [[7, "7 天"], [30, "30 天"], [90, "90 天"], [365, "1 年"]];

function weekly(rows) {
  // 365-day views: one point per week, the average of the days that have data.
  const out = new Map();
  for (const r of rows) {
    const t = parseDay(r.date), monday = shiftDay(r.date, -((t.getUTCDay() + 6) % 7));
    if (!out.has(monday)) out.set(monday, {date: monday, vals: {}});
    const w = out.get(monday);
    for (const [k, v] of Object.entries(r)) if (k !== "date" && v != null) (w.vals[k] ||= []).push(v);
  }
  return [...out.values()].map(w => ({date: w.date, ...Object.fromEntries(Object.entries(w.vals).map(([k, vs]) => [k, vs.reduce((a, b) => a + b, 0) / vs.length]))}));
}

async function viewTrends() {
  const t = state.trends, to = state.today, from = shiftDay(to, -(t.range - 1)), week = t.range > 90;
  const metricMeta = Object.fromEntries(state.metrics.map(m => [m.name, m]));
  const days = (await api("/summary/days?" + q({from, to}))).days;
  const dayRows = names => Promise.all(names.map(n => metricMeta[n]
    ? api(`/metrics/${n}/series?` + q({from, to, bucket: "day"})).then(r => Object.fromEntries(r.points.map(p => [p.bucket, p.value])))
    : Promise.resolve({})));
  let rows, kind, unit, title, digits = 0, goal = null;
  if (t.metric === "energy") {
    const [active, basal] = await dayRows(["active_energy", "basal_energy"]);
    rows = days.map(d => ({date: d.date, intake: d.intake.kcal ?? null,
      burned: active[d.date] != null || basal[d.date] != null ? (active[d.date] || 0) + (basal[d.date] || 0) : null}));
    kind = "energy"; unit = "kcal"; title = "摄入与消耗"; goal = state.profile.goals?.kcal;
  } else if (t.metric === "sleep") {
    rows = days.map(d => ({date: d.date, value: d.sleep_hours ?? null}));
    kind = "bar"; unit = "小时"; title = "睡眠时长"; digits = 1; goal = state.profile.goals?.sleep_hours;
  } else {
    const m = metricMeta[t.metric] || {title: t.metric, unit: "", aggregation: "avg"};
    const [vals] = await dayRows([t.metric]);
    rows = days.map(d => ({date: d.date, value: vals[d.date] ?? null}));
    kind = m.aggregation === "sum" ? "bar" : "line"; unit = UNIT_LABELS[m.unit] ?? m.unit; title = m.title;
    digits = m.aggregation === "sum" && m.unit === "count" ? 0 : 1;
    goal = {step_count: state.profile.goals?.steps, exercise_time: state.profile.goals?.exercise_min, body_mass: state.profile.goals?.weight_kg}[t.metric];
  }
  if (week) rows = weekly(rows);
  const label = r => week ? `${shortDay(r.date)} 起一周` : dayLabel(r.date);
  const yFmt = v => fmt(v, digits);

  const head = h("div", {class: "filter-row"},
    h("div", {class: "filters", role: "group", "aria-label": "时间范围"}, RANGES.map(([n, l]) =>
      h("button", {type: "button", "aria-pressed": String(t.range === n), onclick: () => { t.range = n; rerender(); }}, l))),
    h("div", {class: "toolbar"},
      h("div", {class: "filters", role: "group", "aria-label": "指标"}, TREND_METRICS.filter(([k]) => ["energy", "sleep"].includes(k) || metricMeta[k]).map(([k, l]) =>
        h("button", {type: "button", "aria-pressed": String(t.metric === k), onclick: () => { t.metric = k; rerender(); }}, l))),
      h("select", {"aria-label": "其他指标", onchange: e => { if (e.target.value) { t.metric = e.target.value; rerender(); } }},
        h("option", {value: ""}, "其他指标…"),
        state.metrics.filter(m => !TREND_METRICS.some(([k]) => k === m.name)).map(m =>
          h("option", {value: m.name, selected: t.metric === m.name}, m.title)))));

  const mean = xs => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  const values = kind === "energy" ? rows.map(r => r.intake).filter(v => v != null) : rows.map(r => r.value).filter(v => v != null);
  const avg = mean(values), per = week ? "周均" : "日均";
  const burnedVals = kind === "energy" ? rows.map(r => r.burned).filter(v => v != null) : [];
  const stats = kind === "energy" ? h("div", {class: "stats"},
    h("span", null, `${per}摄入 `, h("strong", null, values.length ? yFmt(avg) : "—")),
    h("span", null, `${per}消耗 `, h("strong", null, burnedVals.length ? yFmt(mean(burnedVals)) : "—")),
    goal ? h("span", null, "目标 ", h("strong", null, yFmt(goal))) : null)
    : h("div", {class: "stats"}, values.length ? [
    h("span", null, `${per} `, h("strong", null, yFmt(avg)), unit ? " " + unit : ""),
    h("span", null, "最高 ", h("strong", null, yFmt(Math.max(...values)))),
    h("span", null, "最低 ", h("strong", null, yFmt(Math.min(...values)))),
    h("span", null, `${values.length} ${week ? "周" : "天"}有数据`)] : "没有数据");

  let plot, legend = null, tableRows;
  if (kind === "energy") {
    const x = r => parseDay(r.date).getTime();
    plot = chart(el => lineChart(el, {label: "每天摄入与消耗的热量", unit, yFmt, zero: true, goal: goal || null,
      xFmt: v => shortDay(new Date(v).toISOString().slice(0, 10)), xTip: v => label({date: new Date(v).toISOString().slice(0, 10)}),
      series: [{label: "摄入", color: "var(--series-1)", points: rows.map(r => ({x: x(r), y: r.intake}))},
        {label: "消耗", color: "var(--series-2)", points: rows.map(r => ({x: x(r), y: r.burned}))}]}));
    legend = h("div", {class: "legend"}, h("span", null, h("i", {class: "line series-1"}), "摄入"), h("span", null, h("i", {class: "line series-2"}), "消耗（活动 + 静息）"));
    tableRows = rows.map(r => [label(r), yFmt(r.intake), yFmt(r.burned)]);
  } else if (kind === "bar") {
    plot = chart(el => barChart(el, {label: title, unit, yFmt, color: "var(--series-1)", goal,
      points: rows.map(r => ({label: shortDay(r.date), tip: label(r), y: r.value}))}));
    tableRows = rows.map(r => [label(r), yFmt(r.value)]);
  } else {
    plot = chart(el => lineChart(el, {label: title, unit, yFmt, xFmt: v => shortDay(new Date(v).toISOString().slice(0, 10)),
      xTip: v => label({date: new Date(v).toISOString().slice(0, 10)}),
      series: [{label: title, color: "var(--series-1)", points: rows.map(r => ({x: parseDay(r.date).getTime(), y: r.value}))}]}));
    tableRows = rows.map(r => [label(r), yFmt(r.value)]);
  }
  const table = h("details", null, h("summary", {class: "small"}, "数据表"), h("div", {class: "scroll-x"}, h("table", null,
    h("thead", null, h("tr", null, h("th", null, week ? "周" : "日期"), kind === "energy" ? [h("th", {class: "right"}, "摄入"), h("th", {class: "right"}, "消耗")]
      : h("th", {class: "right"}, unit || "数值"))),
    h("tbody", null, tableRows.slice().reverse().map(cells => h("tr", null, h("td", null, cells[0]), cells.slice(1).map(c => h("td", {class: "right"}, c))))))));

  const chartCard = h("section", {class: "card"},
    h("div", {class: "chart-head"}, h("h2", null, title + (unit ? `（${unit}${week ? "，周平均" : ""}）` : week ? "（周平均）" : "")), stats),
    legend, plot, table);

  const filled = days.filter(d => Object.keys(d.intake).length || ["steps", "active_kcal", "exercise_min", "sleep_hours", "resting_hr", "weight_kg"].some(k => d[k] != null));
  const overview = t.range <= 30 && filled.length ? card("每日概览", `${filled.length} 天有数据`, h("div", {class: "scroll-x"}, h("table", null,
    h("thead", null, h("tr", null, ["日期", "摄入", "步数", "活动", "锻炼", "睡眠", "静息心率", "体重"].map((l, i) => h("th", {class: i ? "right" : null}, l)))),
    h("tbody", null, filled.slice().reverse().map(d => h("tr", {class: "clickable", onclick: () => { location.hash = d.date === state.today ? "#/today" : "#/day/" + d.date; }},
      h("td", null, relDay(d.date)), h("td", {class: "right"}, fmt(d.intake.kcal)), h("td", {class: "right"}, fmt(d.steps)),
      h("td", {class: "right"}, fmt(d.active_kcal)), h("td", {class: "right"}, fmt(d.exercise_min)), h("td", {class: "right"}, fmt(d.sleep_hours, 1)),
      h("td", {class: "right"}, fmt(d.resting_hr)), h("td", {class: "right"}, fmt(d.weight_kg, 1)))))))) : null;
  return [head, chartCard, overview];
}

// --- foods -----------------------------------------------------------------------------------

async function viewFoods() {
  const list = h("div");
  async function load() {
    const {foods} = await api("/foods?" + q({q: state.foods, limit: 200}));
    list.replaceChildren(foods.length ? h("div", {class: "scroll-x"}, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "食物"), h("th", null, "一份"), h("th", {class: "right"}, "每份热量"),
        h("th", {class: "right"}, "每 100 g"), h("th", {class: "right"}, "用过"), h("th", null, "来源"))),
      h("tbody", null, foods.map(f => h("tr", {class: "clickable", onclick: () => editFood(f)},
        h("td", null, f.name, f.aliases?.length ? h("span", {class: "muted small"}, " " + f.aliases.join("、")) : null,
          f.brand ? h("span", {class: "muted small"}, " · " + f.brand) : null),
        h("td", null, f.serving ? `${fmt(f.serving.amount, 1)} ${f.serving.unit || ""}${f.serving.grams ? `（${fmt(f.serving.grams)} g）` : ""}` : "—"),
        h("td", {class: "right"}, fmt(f.per_serving?.kcal)), h("td", {class: "right"}, fmt(f.per_100g?.kcal)),
        h("td", {class: "right"}, fmt(f.use_count)), h("td", null, FOOD_SOURCES[f.source] || f.source))))))
      : h("p", {class: "empty"}, state.foods ? "没有找到。" : "还没有食物。agent 记录饮食时遇到常吃的东西会存进来，也可以手动新建。"));
  }
  let timer;
  const search = h("input", {type: "search", placeholder: "搜索食物", value: state.foods, "aria-label": "搜索食物",
    oninput: e => { clearTimeout(timer); timer = setTimeout(() => { state.foods = e.target.value.trim(); load(); }, 300); }});
  await load();
  return [h("div", {class: "filter-row"}, search, button("新建食物", () => editFood(null), "btn")), h("section", {class: "card"}, list)];
}
const FOOD_SOURCES = {estimate: "估算", label: "包装标签", user: "自己填写", database: "数据库"};

function field(label, input, cls) { return h("label", {class: "field" + (cls ? " " + cls : "")}, h("span", null, label), input); }
const numInput = (name, value, step = "any") => h("input", {type: "number", name, step, min: 0, value: value ?? ""});
const textInput = (name, value, attrs = {}) => h("input", {type: "text", name, value: value ?? "", ...attrs});

function editFood(food) {
  const f = food || {};
  $("foodTitle").textContent = food ? "编辑食物" : "新建食物";
  $("foodError").hidden = true;
  $("foodDelete").hidden = !food;
  const fields = [
    field("名称", textInput("name", f.name, {required: true})), field("别名（用顿号或逗号分隔）", textInput("aliases", (f.aliases || []).join("、"))),
    field("品牌", textInput("brand", f.brand)),
    field("来源", h("select", {name: "source"}, Object.entries(FOOD_SOURCES).map(([v, l]) => h("option", {value: v, selected: (f.source || "user") === v}, l)))),
    h("h3", null, "一份是多少"),
    field("数量", numInput("serving_amount", f.serving?.amount)), field("单位（碗、个、杯…）", textInput("serving_unit", f.serving?.unit)),
    field("约多少克", numInput("serving_grams", f.serving?.grams)), h("span"),
    h("h3", null, "每份营养"), ...NUTRIENTS.slice(0, 7).map(([k, l, u]) => field(`${l}（${u}）`, numInput("ps_" + k, f.per_serving?.[k]))),
    h("h3", null, "每 100 g 营养"), ...NUTRIENTS.slice(0, 7).map(([k, l, u]) => field(`${l}（${u}）`, numInput("p100_" + k, f.per_100g?.[k]))),
    field("备注", h("textarea", {name: "note", rows: 2}, f.note || ""), "full")];
  fill($("foodFields"), fields);
  const form = $("foodForm");
  form.onsubmit = async e => {
    e.preventDefault();
    const v = n => form.elements[n].value.trim(), num = n => v(n) === "" ? null : Number(v(n));
    const pick = prefix => { const o = {}; for (const [k] of NUTRIENTS.slice(0, 7)) if (num(prefix + k) != null) o[k] = num(prefix + k); return Object.keys(o).length ? o : null; };
    const body = {name: v("name"), aliases: v("aliases").split(/[、,，\s]+/).filter(Boolean), brand: v("brand") || null, source: v("source"),
      serving: v("serving_unit") ? {amount: num("serving_amount") || 1, unit: v("serving_unit"), ...(num("serving_grams") ? {grams: num("serving_grams")} : {})} : null,
      per_serving: pick("ps_"), per_100g: pick("p100_"), note: v("note") || null};
    try {
      await api(food ? "/foods/" + food.id : "/foods", {method: food ? "PATCH" : "POST", body});
      $("foodDialog").close(); toast("已保存"); rerender();
    } catch (err) { $("foodError").textContent = err.message; $("foodError").hidden = false; }
  };
  $("foodDelete").onclick = async () => {
    if (!confirm(`删除“${f.name}”？已有的饮食记录不受影响。`)) return;
    await guarded(() => api("/foods/" + food.id, {method: "DELETE"}));
    $("foodDialog").close(); toast("已删除"); rerender();
  };
  $("foodDialog").showModal();
}

// --- settings --------------------------------------------------------------------------------

const GOALS = [["kcal", "热量", "kcal"], ["protein_g", "蛋白质", "g"], ["fat_g", "脂肪", "g"], ["carbs_g", "碳水", "g"],
  ["steps", "步数", "步"], ["exercise_min", "锻炼", "分钟"], ["sleep_hours", "睡眠", "小时"], ["water_ml", "饮水", "ml"],
  ["weight_kg", "目标体重", "kg"]];
const SCOPES = {read: "只读", write: "读写", admin: "管理"};

async function viewSettings() {
  const [{profile}, tokens, imports, changes] = await Promise.all([api("/profile"), api("/tokens"), api("/imports?limit=10"), api("/changes?limit=30")]);
  const p = profile, goals = p.goals || {}, diet = p.diet || {};
  const form = h("form", {class: "form-grid", onsubmit: async e => {
    e.preventDefault();
    const el = form.elements, v = n => el[n].value.trim(), num = n => v(n) === "" ? null : Number(v(n));
    const list = n => v(n).split(/[、,，\n]+/).map(x => x.trim()).filter(Boolean);
    const newGoals = {...goals};
    for (const [k] of GOALS) { if (num("goal_" + k) == null) delete newGoals[k]; else newGoals[k] = num("goal_" + k); }
    const body = {name: v("name") || null, birthday: v("birthday") || null, sex: v("sex") || null, height_cm: num("height_cm"),
      timezone: v("timezone") || null, currency: v("currency").toUpperCase() || null,
      goals: Object.keys(newGoals).length ? newGoals : null,
      diet: {...diet, allergies: list("allergies"), preferences: list("preferences"), avoid: list("avoid")}, notes: v("notes") || null};
    await guarded(() => api("/profile", {method: "PATCH", body}));
    await loadContext(); toast("已保存");
  }},
  field("称呼", textInput("name", p.name)), field("生日", h("input", {type: "date", name: "birthday", value: p.birthday || ""})),
  field("性别", textInput("sex", p.sex)), field("身高（cm）", numInput("height_cm", p.height_cm)),
  field("时区", textInput("timezone", p.timezone, {placeholder: "Asia/Tokyo"})), field("默认币种", textInput("currency", p.currency, {placeholder: "JPY"})),
  h("h3", null, "每日目标"), GOALS.map(([k, l, u]) => field(`${l}（${u}）`, numInput("goal_" + k, goals[k]))),
  h("h3", null, "饮食"), field("过敏", textInput("allergies", (diet.allergies || []).join("、"))),
  field("偏好", textInput("preferences", (diet.preferences || []).join("、"))), field("不吃", textInput("avoid", (diet.avoid || []).join("、"))), h("span"),
  field("给 agent 的备注", h("textarea", {name: "notes", rows: 3}, p.notes || ""), "full"),
  h("div", {class: "dialog-foot full"}, h("span", {class: "muted small"}, "agent 估算饮食时会参考这些信息。"), h("button", {class: "btn primary", type: "submit"}, "保存")));

  const tokenName = textInput("token_name", "", {placeholder: "例如 claude-phone", pattern: "[A-Za-z0-9_-]+", "aria-label": "agent 名称"});
  const tokenScope = h("select", {"aria-label": "权限"}, Object.entries(SCOPES).map(([v, l]) => h("option", {value: v, selected: v === "write"}, l)));
  const tokenTable = h("div", {class: "scroll-x"}, h("table", null,
    h("thead", null, h("tr", null, ["名称", "权限", "最近使用", "状态", ""].map(l => h("th", null, l)))),
    h("tbody", null, tokens.tokens.slice().reverse().map(t => h("tr", null, h("td", null, t.name), h("td", null, SCOPES[t.scope]),
      h("td", null, t.last_used_at ? ago(t.last_used_at) : "从未"), h("td", null, t.revoked_at ? h("span", {class: "muted"}, "已吊销") : "有效"),
      h("td", {class: "right"}, t.revoked_at ? null : button("吊销", async () => {
        if (!confirm(`吊销“${t.name}”？用它的 agent 会立刻失去访问权限。`)) return;
        await guarded(() => api("/tokens/" + encodeURIComponent(t.name), {method: "DELETE"}));
        toast("已吊销"); rerender();
      }, "btn small danger")))))));
  const agents = card("接入的 agent", null, h("div", {class: "dialog-stack"},
    h("p", {class: "muted small"}, "每个 agent 用自己的 token，写下的每条记录都能看出是谁写的。把 API 地址和 token 交给它；不支持 skill 的 agent 让它先读说明书。"),
    h("div", {class: "secret"}, h("code", null, AGENT_API), button("复制地址", () => copy(AGENT_API), "btn small")),
    h("p", {class: "small"}, h("a", {href: AGENT_API + "/manual", target: "_blank", rel: "noopener"}, "agent 说明书"), " · ",
      h("a", {href: REPO + "/tree/main/skills/lifeos", target: "_blank", rel: "noopener"}, "skill（GitHub）")),
    tokenTable,
    h("form", {class: "actions", onsubmit: async e => {
      e.preventDefault();
      const name = tokenName.value.trim();
      if (!name) return;
      const res = await guarded(() => api("/tokens", {method: "POST", body: {name, scope: tokenScope.value}}));
      showToken(res); tokenName.value = ""; rerender();
    }}, tokenName, tokenScope, h("button", {class: "btn", type: "submit"}, "生成 token"))));

  const KIND = {apple_health: "健康导出文件", health_auto_export: "Health Auto Export"};
  const importRows = imports.imports.map(j => {
    const st = j.stats || {};
    const what = [st.samples_added != null && `${fmt(st.samples_added)} 个新数据点`, st.samples_updated && `${fmt(st.samples_updated)} 个更新`,
      st.workouts && `${st.workouts} 次运动`, st.activity_days && `${st.activity_days} 天圆环`].filter(Boolean).join("，");
    return h("tr", null, h("td", {class: "num"}, ago(j.created_at)), h("td", null, KIND[j.kind] || j.kind),
      h("td", null, h("span", {class: "dot " + ({done: "good", failed: "critical", running: "running", queued: "running"}[j.status] || "")}), " ",
        {done: "完成", failed: "失败", running: "进行中", queued: "排队中"}[j.status] || j.status),
      h("td", null, j.error ? h("span", {class: "bad"}, j.error) : what || "—"));
  });
  const fileInput = h("input", {type: "file", accept: ".zip,.xml", "aria-label": "选择导出文件"});
  const uploadState = h("span", {class: "muted small", role: "status"});
  const uploadBtn = button("上传并导入", async () => {
    const file = fileInput.files[0];
    if (!file) { toast("先选择导出的 zip 文件"); return; }
    if (BASE && file.size > 100 * 1024 * 1024) { toast("超过 100 MB，请用 scp 传到小主机后运行 lifeos import apple-health"); return; }
    uploadBtn.disabled = true; uploadState.textContent = "上传中…";
    try {
      const job = await api("/imports/apple-health", {method: "POST", body: file, raw: true, type: file.name.endsWith(".xml") ? "application/xml" : "application/zip"});
      uploadState.textContent = "导入中…";
      for (;;) {
        await new Promise(r => setTimeout(r, 2000));
        const j = await api("/imports/" + job.import_id);
        if (j.status === "done") { toast(`导入完成：${fmt(j.stats.samples_added)} 个新数据点，${j.stats.workouts} 次运动`); break; }
        if (j.status === "failed") { toast("导入失败：" + j.error); break; }
        uploadState.textContent = `导入中… 已读 ${fmt(j.stats.samples_read || 0)} 个数据点`;
      }
      rerender();
    } catch (err) { toast(err.message); } finally { uploadBtn.disabled = false; uploadState.textContent = ""; }
  }, "btn");
  const importCard = card("Apple 健康", null, h("div", {class: "dialog-stack"},
    h("p", {class: "muted small"}, "手机上的 Health Auto Export 会自动推送；以前的历史可在“健康” App → 头像 → 导出所有健康数据，再上传这里。超过 100 MB 的文件请用 scp 传到小主机后运行 lifeos import apple-health。"),
    h("div", {class: "actions"}, fileInput, uploadBtn, uploadState),
    importRows.length ? h("div", {class: "scroll-x"}, h("table", null, h("thead", null, h("tr", null, ["时间", "来源", "状态", "内容"].map(l => h("th", null, l)))),
      h("tbody", null, importRows))) : h("p", {class: "empty"}, "还没有导入过。")));

  const exportCard = card("导出", null, h("div", {class: "dialog-stack"},
    h("p", {class: "muted small"}, "所有数据都存在小主机的 /var/lib/lifeos，每晚自动备份。这里可以下载一份带走。"),
    h("div", {class: "actions"}, button("全部记录（JSON）", () => download("/export", `lifeos-${state.today}.json`), "btn small"),
      button("全部数据点（NDJSON）", () => download("/export/samples.ndjson", `lifeos-samples-${state.today}.ndjson`), "btn small"))));

  const changeCard = card("最近变更", null, h("ol", {class: "history"}, changes.changes.map(c =>
    h("li", null, h("time", null, ago(c.at)), c.entity === "import" ? h("span", null, `${c.actor} 导入了 Apple 健康数据`) : h("span", null, `${c.actor} ${ACTIONS[c.action] || c.action} `,
      c.entity === "record" ? h("button", {class: "link", type: "button", onclick: () => openRecord(c.entity_id)}, c.after?.title || c.before?.title || "记录")
        : h("span", {class: "muted"}, ENTITY[c.entity] || c.entity, c.entity === "food" ? " " + (c.after?.name || c.before?.name || "") : ""))))));
  return [h("div", {class: "columns"}, h("div", {class: "stack"}, card("个人资料与目标", null, form)),
    h("div", {class: "stack"}, agents, importCard, exportCard, changeCard))];
}
const ENTITY = {profile: "个人资料", food: "食物", sample: "数据点", category: "类别", token: "token", import: "导入", metric: "指标", attachment: "附件"};

function showToken(res) {
  $("tokenBody").replaceChildren(
    h("p", null, `“${res.name}”（${SCOPES[res.scope]}）`),
    h("div", {class: "field"}, h("span", null, "LIFEOS_TOKEN"), h("div", {class: "secret"}, h("code", null, res.token), button("复制", () => copy(res.token), "btn small"))),
    h("div", {class: "field"}, h("span", null, "LIFEOS_URL"), h("div", {class: "secret"}, h("code", null, AGENT_API), button("复制", () => copy(AGENT_API), "btn small"))),
    h("p", {class: "muted small"}, "把这两个值交给 agent。请求时带上 Authorization: Bearer 加 token。"));
  $("tokenDialog").showModal();
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("已复制"); } catch { toast("复制失败，请手动选择"); }
}

// --- quick add ---------------------------------------------------------------------------------

const ADD_KINDS = [["meal", "饮食"], ["weight", "体重"], ["water", "饮水"], ["mood", "心情"], ["journal", "日记"]];

function openAdd() {
  $("addError").hidden = true;
  $("addKinds").replaceChildren(...ADD_KINDS.map(([k, l]) => h("button", {type: "button", role: "tab", "aria-selected": String(state.addKind === k),
    onclick: () => { state.addKind = k; openAdd(); }}, l)));
  const when = field("时间", h("input", {type: "datetime-local", name: "start", value: nowLocal(), required: true}));
  const fields = {
    meal: () => [field("餐次", h("select", {name: "meal_type"}, h("option", {value: ""}, "按时间自动"), Object.entries(MEAL).map(([v, l]) => h("option", {value: v}, l)))), when,
      h("div", {class: "full dialog-stack", id: "itemRows"}, itemRow()),
      h("div", {class: "full"}, button("再加一项", () => $("itemRows").append(itemRow()), "link small")),
      field("备注", h("textarea", {name: "note", rows: 2}), "full")],
    weight: () => [field("体重（kg）", h("input", {type: "number", name: "value", step: "0.1", min: 20, max: 300, required: true})), when],
    water: () => [field("饮水（ml）", h("input", {type: "number", name: "value", step: "1", min: 1, value: 250, required: true})), when],
    mood: () => [field("心情（1 很差 – 10 很好）", h("input", {type: "number", name: "score", min: 1, max: 10, step: 1, value: 6, required: true})), when,
      field("情绪（顿号分隔）", textInput("emotions", "", {placeholder: "平静、有点累"}), "full"), field("备注", h("textarea", {name: "note", rows: 2}), "full")],
    journal: () => [field("标题（可选）", textInput("title", ""), "full"), when, h("span"), field("内容", h("textarea", {name: "text", rows: 6, required: true}), "full")],
  };
  fill($("addFields"), fields[state.addKind]());
  if (!$("addDialog").open) $("addDialog").showModal();
}
function itemRow() {
  const row = h("div", {class: "item-row"},
    field("食物", textInput("item_name", "", {required: true, placeholder: "牛肉面"})), field("份量", textInput("item_amount", "", {placeholder: "1 碗"})),
    field("热量", numInput("item_kcal", "")), field("蛋白质", numInput("item_protein_g", "")), field("脂肪", numInput("item_fat_g", "")),
    field("碳水", numInput("item_carbs_g", "")),
    button(s("svg", {class: "i", viewBox: "0 0 24 24", "aria-hidden": "true"}, s("path", {d: "m6 6 12 12M18 6 6 18"})),
      () => { if ($("itemRows").children.length > 1) row.remove(); }, "btn icon", {"aria-label": "删除这一项"}));
  return row;
}
async function submitAdd(e) {
  e.preventDefault();
  const form = $("addForm"), el = form.elements, v = n => el[n]?.value?.trim() ?? "";
  const start = v("start");
  try {
    if (state.addKind === "meal") {
      const items = [...$("itemRows").children].map(row => {
        const get = n => row.querySelector(`[name=${n}]`).value.trim(), item = {name: get("item_name")};
        const m = get("item_amount").match(/^([\d.]+)\s*(.*)$/);
        if (m) { item.amount = Number(m[1]); if (m[2]) item.unit = m[2]; if (/^(g|克)$/i.test(m[2])) item.grams = item.amount; }
        else if (get("item_amount")) item.unit = get("item_amount");
        for (const k of ["kcal", "protein_g", "fat_g", "carbs_g"]) if (get("item_" + k) !== "") item[k] = Number(get("item_" + k));
        return item;
      }).filter(i => i.name);
      await api("/records", {method: "POST", body: {category: "meal", start, source: "manual", note: v("note") || null,
        data: {...(v("meal_type") ? {meal_type: v("meal_type")} : {}), items}}});
    } else if (state.addKind === "weight" || state.addKind === "water") {
      const metric = state.addKind === "weight" ? ["body_mass", "kg"] : ["dietary_water", "mL"];
      await api("/samples", {method: "POST", body: {samples: [{metric: metric[0], value: Number(v("value")), unit: metric[1], start, source: "manual"}]}});
    } else if (state.addKind === "mood") {
      const emotions = v("emotions").split(/[、,，\s]+/).filter(Boolean);
      await api("/records", {method: "POST", body: {category: "mood", start, source: "manual", note: v("note") || null,
        data: {score: Number(v("score")), ...(emotions.length ? {emotions} : {})}}});
    } else {
      await api("/records", {method: "POST", body: {category: "journal", start, source: "manual", title: v("title") || null, data: {text: v("text")}}});
    }
    $("addDialog").close();
    toast("已记下");
    rerender();
  } catch (err) {
    $("addError").textContent = err.message;
    $("addError").hidden = false;
  }
}

// ---------------------------------------------------------------------------------------------

async function loadContext() {
  const [ctx, metrics, imports] = await Promise.all([api("/context"), api("/metrics"), api("/imports?limit=1")]);
  state.today = ctx.today;
  state.tz = ctx.timezone;
  state.profile = ctx.profile;
  state.categories = Object.fromEntries(ctx.categories.map(c => [c.name, c]));
  state.metrics = metrics.metrics.filter(m => m.last_date);
  const last = imports.imports[0];
  $("subtitle").textContent = [dayLabel(ctx.today), last ? `Apple 健康 ${ago(last.created_at)}同步` : "还没有导入 Apple 健康数据"].join(" · ");
  const warn = [];
  if (last?.status === "failed") warn.push(["critical", `最近一次导入失败：${last.error}`]);
  if (!ctx.profile.goals?.kcal) warn.push(["warning", "还没有设置每日目标。到“设置”里填上热量和营养目标，今天的页面会显示还剩多少。"]);
  issues(warn);
}

async function boot() {
  if (!BASE) $("homeLink").hidden = true;
  for (const dlg of document.querySelectorAll("dialog")) {
    dlg.querySelector(".close-dialog")?.addEventListener("click", () => dlg.close());
  }
  $("recordDialog").addEventListener("close", () => { if (location.hash.startsWith("#/record/")) history.replaceState(null, "", "#/records"); });
  $("addButton").addEventListener("click", openAdd);
  $("addForm").addEventListener("submit", submitAdd);
  try {
    await loadContext();
  } catch (err) {
    $("subtitle").textContent = "";
    issues([["critical", err.status === 401 || err.status === 403
      ? (BASE ? "没有通过登录，请从 yishuapp.com 重新打开。" : "本机打开需要 token：地址后面加 #token=lifeos_…")
      : "读取失败：" + err.message]]);
    return;
  }
  window.addEventListener("hashchange", route);
  route();
}
boot();
