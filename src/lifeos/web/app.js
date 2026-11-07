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
const button = (label, onclick, cls = "ys-btn", attrs = {}) => h("button", {class: cls, type: "button", onclick, ...attrs}, label);
// A card with its title inside, and a group whose title sits above it on the page ground.
const card = (title, aside, ...body) => h("section", {class: "ys-card"},
  h("div", {class: "ys-card-head"}, h("h2", {class: "ys-card-title"}, title), aside ? h("div", {class: "ys-card-aside"}, aside) : null), ...body);
const group = (title, aside, ...body) => h("section", {class: "ys-section"},
  h("div", {class: "ys-section-head"}, h("h2", {class: "ys-section-title"}, title), aside ? h("span", {class: "ys-section-aside"}, aside) : null), ...body);
const emptyCard = text => h("div", {class: "ys-card"}, h("p", {class: "ys-empty ys-empty--compact"}, text));
// A view or sheet that could not load: the problem and what to do, with the server's own words as a quiet detail.
const failure = err => {
  const [problem, fix] = explain(err);
  return [ysIcon("error"), h("p", {class: "ys-empty-title"}, problem), h("p", {class: "ys-empty-text"}, fix), errorDetail(err, "bad")];
};
const disclosure = (label, body, attrs = {}) => h("details", {class: "ys-disclosure", ...attrs},
  h("summary", null, h("span", null, label), ysIcon("chevron-right", "ys-disclosure-chevron")), h("div", {class: "ys-disclosure-body"}, body));
// Facts side by side, as on the hub, /pc/ and /classroom/: a fact that does not fit moves to the next line whole, and one
// that can be long (a name, a note; wrap: true) wraps inside itself when it stands alone on a line. The dot between two
// facts is drawn by the style sheet in the gap before the second, and the row clips the dot of a fact that opens a
// line, so no line starts or ends with one. A part is a string, {text, cls, wrap}, or {nodes, cls, wrap}.
const CLOSING_BRACKET = /[）」』】〕〉》］｝]$/;
function factRow(parts, className) {
  const row = h("span", {class: "fact-row" + (className ? " " + className : "")});
  for (const part of parts) {
    if (!part) continue;
    const {text = "", nodes = null, cls = null, wrap = false} = typeof part === "string" ? {text: part} : part;
    if (!text && !nodes) continue;
    const fact = h("span", {class: ["fact", wrap && "fact--wrap", cls].filter(Boolean).join(" ")});
    if (nodes) fact.append(...nodes.filter(Boolean));
    // A full-width closing bracket ends in half an em of blank, which would double the gap before the next dot.
    else if (CLOSING_BRACKET.test(text)) fact.append(text.slice(0, -1), h("span", {class: "fact-halt"}, text.slice(-1)));
    else fact.textContent = text;
    row.append(fact);
  }
  return row;
}
// A sheet opens with focus on the sheet itself, so no control shows a ring before the keyboard is used.
function openModal(dlg) {
  if (dlg.open) return;
  dlg.showModal();
  dlg.focus();
}
// Table rows that open something also answer Enter and Space.
const rowAction = go => ({class: "clickable", tabindex: 0, onclick: go,
  onkeydown: e => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); go(); } }});
// On a phone a wide table reads as an inset list (style.css .phone-rows): the .pr-head cell titles the row,
// the .pr-key cell trails it, and every .pr-more cell wraps underneath as a "label value" phrase.
// Units and the chevron only show there; an empty value ("—") drops out of the phrases.
const prUnit = (value, unit) => unit && value !== "—" ? h("span", {class: "pr-unit"}, " " + unit) : null;
const prKey = (value, unit, cls, opens) => h("td", {class: (cls ? cls + " " : "") + "pr-key"}, value, prUnit(value, unit),
  opens ? ysIcon("chevron-right", "pr-chevron") : null);
const prMore = (label, value, unit, cls) => h("td", {class: [cls, "pr-more", value === "—" && "pr-empty"].filter(Boolean).join(" "),
  "data-label": label}, value, prUnit(value, unit));
function phoneRows(table) {
  table.classList.add("phone-rows");
  // the phone layout turns rows into flex boxes, so the table says what it is in roles too
  table.setAttribute("role", "table");
  for (const g of table.querySelectorAll("thead, tbody, tfoot")) g.setAttribute("role", "rowgroup");
  for (const tr of table.rows) {
    tr.setAttribute("role", "row");
    for (const c of tr.cells) c.setAttribute("role", c.tagName === "TH" ? "columnheader" : "cell");
    // " ·" closes every phrase but the last, so a wrapped line never starts with one
    tr.querySelectorAll(".pr-more:not(.pr-empty)").forEach((c, i, all) => c.classList.toggle("pr-sep", i < all.length - 1));
  }
  return table;
}

// The shared outline icon set (24px grid, stroke 2), built with createElementNS.
const YS_ICONS = {
  "back": "M15.5 4 7.5 12l8 8",
  "chevron-left": "M14.5 5.5 8 12l6.5 6.5",
  "chevron-right": "M9.5 5.5 16 12l-6.5 6.5",
  "chevron-down": "M5.5 9.5 12 16l6.5-6.5",
  "plus": "M12 5v14M5 12h14",
  "close": "M6.5 6.5l11 11M17.5 6.5l-11 11",
  "check": "M5 12.5l4.5 4.5L19 7.5",
  "download": "M12 4v10.5M7.5 10 12 14.5l4.5-4.5M4.5 15v2.5a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V15",
  "external": "M7.5 16.5 16.5 7.5M9 7.5h7.5V15",
  "search": "M10.5 4.5a6 6 0 1 0 0 12 6 6 0 0 0 0-12zM15 15l4.5 4.5",
  "calendar": "M6.5 5.5h11A2.5 2.5 0 0 1 20 8v9.5a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5V8a2.5 2.5 0 0 1 2.5-2.5zM4 10h16M8.5 3.5v4M15.5 3.5v4",
  "warning": "M10.27 4.5a2 2 0 0 1 3.46 0l7.36 12.75a2 2 0 0 1-1.73 3H4.64a2 2 0 0 1-1.73-3zM12 9.5v4M12 16.75h.01",
  "error": "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17zM12 7.75v5M12 16.25h.01"
};
function ysIcon(name, extraClass) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  const attrs = {
    class: extraClass ? "ys-icon " + extraClass : "ys-icon",
    viewBox: "0 0 24 24", width: "16", height: "16", fill: "none",
    stroke: "currentColor", "stroke-width": "2",
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true"
  };
  for (const key in attrs) svg.setAttribute(key, attrs[key]);
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", YS_ICONS[name]);
  svg.appendChild(path);
  return svg;
}

// tone: "ok" (a check) or "bad" (an error mark); none for plain notices. A detail (the server's own
// words, see toastError) goes under the line and keeps the toast up longer, so there is time to read it.
function toast(message, tone, detail) {
  const el = $("toast");
  el.className = "ys-toast" + (tone === "bad" ? " ys-toast--bad" : "") + (detail ? " has-detail" : "");
  fill(el, tone === "ok" ? ysIcon("check") : tone === "bad" ? ysIcon("error") : null,
    detail ? h("span", {class: "toast-body"}, message, detail) : message);
  el.hidden = false;
  placeToast(el, true);
  clearTimeout(toast.timer);
  toast.timer = setTimeout(hideToast, detail ? 6000 : 3200);
}
// When its time is up, or at once when it is tapped (boot wires the click): on a phone the toast can
// cover a sheet's foot controls, and they are reachable again as soon as it goes.
function hideToast() {
  const el = $("toast");
  clearTimeout(toast.timer);
  el.hidden = true;
  placeToast(el);
}
// An open dialog sits in the browser's top layer and makes the rest of the page inert: a toast left
// outside it is covered, and one merely raised above it is seen but tapped through to the control
// beneath. So while a dialog is open the toast lives inside it, shown as a popover on top, and goes
// back to the page when the dialog closes. With two open (a #/record/ link over 记一笔), the one in use
// is the one holding focus, as the other is inert; with focus in neither, the last one. Without popovers
// it stays where it always was. Only a fresh toast rises in: one that just changes place stays still.
function placeToast(el, fresh = false) {
  if (!el.showPopover) return;
  const open = [...document.querySelectorAll("dialog:modal")];
  const dlg = el.hidden ? null : open.find(d => d.contains(document.activeElement)) || open.at(-1);
  const host = dlg || document.body;
  if (el.parentNode !== host) {
    host.append(el);  // moving an open popover closes it
    dlg?.addEventListener("close", () => placeToast(el), {once: true});
  }
  el.popover = "manual";
  if (el.matches(":popover-open")) el.hidePopover();
  if (el.hidden) return;
  el.showPopover();
  if (!fresh) el.getAnimations().forEach(a => a.finish());  // shown again, it would replay its entry transition
}
function issues(list) {
  const el = $("issues");
  el.replaceChildren(...list.map(([kind, text]) => h("div", {class: "ys-alert " + (kind === "critical" ? "ys-alert--bad" : "ys-alert--warn")},
    ysIcon(kind === "critical" ? "error" : "warning"), h("div", {class: "ys-alert-body"}, text))));
  el.hidden = !list.length;
}
function showError(el, err) {
  const [problem, fix] = explain(err);
  fill(el, ysIcon("error"), h("span", {class: "form-error-body"},
    h("span", {class: "form-error-title"}, problem), h("span", null, fix), errorDetail(err)));
  el.hidden = false;
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
  // JSON media types only (application/json, …+json, which error bodies are); NDJSON and files stay a blob
  const ctype = (res.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
  const data = ctype === "application/json" || ctype.endsWith("+json") ? await res.json() : await res.blob();
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

// The API answers in English, for agents. The page names the problem and what to do about it in
// its own words, and keeps the server's message underneath as a quiet detail.
const NOT_FOUND = {record: "这条记录", food: "这个食物", "active token": "这个 token", attachment: "这个附件", import: "这次导入",
  metric: "这个指标", category: "这个类别", sample: "这个数据点"};
const FIELD_NAMES = {count: "根数", score: "心情", value: "数值", amount: "份量", grams: "克数", name: "名称", text: "内容",
  items: "饮食明细", food_id: "食物", unit: "单位"};
const fieldName = k => FIELD_NAMES[k] || NUTRIENTS.find(([n]) => n === k)?.[1] || FIELDS[k]?.[0] || k;
function explain(err) {
  const msg = String(err?.message ?? err ?? ""), status = err?.status;
  if (!status) {
    return err instanceof TypeError || /failed to fetch|load failed|networkerror/i.test(msg)
      ? ["连不上服务器", "检查网络，或刷新页面重新登录。"] : ["出了点问题", "刷新页面再试一次。"];
  }
  if (status === 401 || status === 403) {
    if (/scope/.test(msg)) return ["这个 token 只能读", "换一个读写或管理权限的 token。"];
    return BASE ? ["没有通过登录", "请从 yishuapp.com 重新打开。"] : ["本机打开需要 token", "地址后面加 #token=lifeos_…"];
  }
  if (status === 404) return [`${NOT_FOUND[msg.match(/^(.+?) '/)?.[1]] || "要找的内容"}不存在`, "可能已被删除。"];
  if (status === 409 && / is deleted/.test(msg)) return ["这条记录已删除", "先恢复它，再修改。"];
  if (status === 413) return ["文件太大", "超过 100 MB 的文件请用 scp 传到小主机后运行 lifeos import apple-health。"];
  if (status === 400 || status === 422) {
    // a meal item that names a library food (categories.py): its amount has to tell how much of that food it is
    const item = msg.match(/^data\['items'\]\[\d+\](?:\['food_id'\])?: (.+)$/)?.[1] || "";
    if (/^food .+ not found$/.test(item)) return ["食物库里没有这个食物", "核对食物编号，或给这一项填上热量再保存。"];
    if (/^unit .+ needs an amount: /.test(item)) return ["份量缺少数量", "填上数量或克数再保存。"];
    if (/^amount \S+ has no unit/.test(item)) return ["份量没写单位", "写上单位再保存：按份算就写“份”，或用食物库里一份的单位。"];
    if (/ cannot be matched with this food's serving /.test(item)) return ["份量和食物库里的一份对不上", "填上克数再保存，或改用食物库里一份的单位，按份算就写“份”。"];
    if (/its serving has no weight/.test(item)) return ["食物库里的一份没写克数", "份量改用食物库里一份的单位，按份算就写“份”。"];
    if (/nutrients per 100 g/.test(item)) return ["算不出这一项有多少克", "这个食物按每 100 g 算营养，填上克数再保存。"];
    const extra = msg.match(/^data\['items'\]\[\d+\]\[(['"])(.+?)\1\]: not a meal item field/)?.[2];
    if (extra) return [`饮食明细里不认识 ${extra}`, "去掉这一项再保存，它不会计入热量和营养。"];
    // jsonschema: "data['count']: 0 is less than the minimum of 1", "data: 'text' is a required property"
    const m = msg.split("; ")[0].match(/^data((?:\[[^\]]*\])*): (.*)$/);
    if (m) {
      const key = m[2].match(/^'(\w+)' is a required property/)?.[1] || [...m[1].matchAll(/\['(\w+)'\]/g)].at(-1)?.[1];
      // inside a meal item a key is a nutrient or an item field, never a category field of the same name
      const inItem = /^\['items'\]/.test(m[1]) && key !== "items";
      const label = !key ? "这一项" : inItem ? NUTRIENTS.find(([n]) => n === key)?.[1] || FIELD_NAMES[key] || key : fieldName(key);
      const n = m[2].match(/ of (-?[\d.]+)$/)?.[1];
      const problem = /less than the minimum/.test(m[2]) ? `${label}至少是 ${n}` : /greater than the maximum/.test(m[2]) ? `${label}最多是 ${n}`
        : /required property/.test(m[2]) ? `${label}还没填` : /is not of type/.test(m[2]) ? `${label}的格式不对` : `${label}填得不对`;
      return [problem, "改好后再保存。"];
    }
    if (/needs a name/.test(msg)) return ["名称还没填", "填上名称再保存。"];
    if (/per_serving or per_100g/.test(msg)) return ["还没有营养数据", "每份或每 100 g 的营养至少填一项再保存。"];
    if (/per_serving needs serving/.test(msg)) return ["不知道一份是多少", "填上一份的数量和单位再保存。"];
    if (/unknown timezone/.test(msg)) return ["时区写法不对", "用 Asia/Tokyo 这样的名称。"];
    if (/end is before start/.test(msg)) return ["结束时间早于开始时间", "改好时间再保存。"];
    if (/value must be a number|needs metric and value/.test(msg)) return ["数值还没填对", "填一个数字再保存。"];
    if (/^an active token named .+ exists/.test(msg)) return ["这个名称已经在用", "先吊销同名的 token，或换一个名称。"];
    if (/^empty upload$/.test(msg)) return ["上传的文件是空的", "选好导出的文件再上传。"];
    if (/^name may contain letters, digits/.test(msg)) return ["名称只能用字母、数字、- 和 _", "改好后再生成。"];
    return ["有一项填得不对", "改好后再保存。"];
  }
  if (status >= 500) return ["服务器出错了", "稍后刷新页面再试。"];
  return ["请求没有成功", "刷新页面再试一次。"];
}
const errorLine = err => explain(err).join("，");
const errorDetail = (err, cls) => {
  const text = String(err?.message ?? err ?? "");
  return text ? h("span", {class: "error-detail" + (cls ? " " + cls : ""), lang: /[\u3000-\u9fff]/.test(text) ? null : "en"}, text) : null;
};
// A failed action as a toast: the problem and what to do, with the server's own words under them.
const toastError = err => toast(errorLine(err), "bad", errorDetail(err));

// ---------------------------------------------------------------------------------------------
// Formatting

const WEEK = "日一二三四五六";
const fmt = (n, digits = 0) => n == null || Number.isNaN(Number(n)) ? "—"
  : Number(n).toLocaleString("zh-CN", {maximumFractionDigits: digits});
const parseDay = d => { const [y, m, dd] = d.split("-").map(Number); return new Date(Date.UTC(y, m - 1, dd)); };
const shiftDay = (d, n) => { const t = parseDay(d); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };
const dayLabel = (d, week = true) => {
  const t = parseDay(d), year = state.today && d.slice(0, 4) !== state.today.slice(0, 4) ? `${t.getUTCFullYear()}年` : "";
  return `${year}${t.getUTCMonth() + 1}月${t.getUTCDate()}日` + (week ? ` 周${WEEK[t.getUTCDay()]}` : "");
};
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
  exercise_goal_min: ["锻炼目标", "分钟"], stand_hours: ["站立", "小时"], stand_goal_hours: ["站立目标", "小时"],
  brand: ["品牌", ""], trigger: ["场景", ""]};
const SMOKE_KIND = {cigarette: "香烟", heated: "加热烟", e_cigarette: "电子烟", cigar: "雪茄", other: "其他"};
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
  if (rec.category === "smoking" && d.count != null) return [d.count, "根"];
  return null;
}
// A row's second line as facts (factRow); names and notes can be long, so they may wrap inside themselves.
function recordSub(rec) {
  const d = rec.data || {}, long = text => ({text, wrap: true});
  if (rec.category === "meal") return (d.items || []).map(i => long(i.name + (i.amount ? ` ${fmt(i.amount, 1)}${i.unit || ""}` : "")));
  if (rec.category === "workout") {
    return [d.active_kcal != null && `${fmt(d.active_kcal)} kcal`, d.distance_km && `${fmt(d.distance_km, 2)} km`,
      d.avg_hr && `平均心率 ${fmt(d.avg_hr)}`];
  }
  if (rec.category === "journal") return [long((d.text || "").slice(0, 80))];
  if (rec.category === "mood") return [long(rec.note || "")];
  if (rec.category === "smoking") return [d.trigger && long(d.trigger), rec.note && long(rec.note)];
  return [long(rec.note || "")];
}

// ---------------------------------------------------------------------------------------------
// Charts: thin marks, hairline grid, one axis, crosshair or per-bar tooltip, table view beside.

const tickLabel = ticks => {
  const step = ticks.length > 1 ? Math.abs(ticks[1] - ticks[0]) : 1;
  const digits = step >= 1 ? 0 : (String(+step.toFixed(10)).split(".")[1] || "").length;
  return t => step < 1 ? fmt(t, digits) : compact(t);
};
// integer: counted things (cigarettes, steps) tick in whole numbers, never at 2.5.
function niceScale(min, max, count = 4, zero = false, integer = false) {
  if (zero) min = Math.min(0, min);
  if (min === max) { const pad = Math.abs(min) * 0.1 || 1; min -= pad; max += pad; }
  const raw = integer ? Math.max(1, (max - min) / count) : (max - min) / count, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(m => m >= raw && (!integer || Number.isInteger(m)));
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return {lo, hi, ticks};
}
const compact = n => Math.abs(n) >= 10000 ? fmt(n / 1000, 1) + "K" : fmt(n, Math.abs(n) < 10 ? 1 : 0);
// About how wide a 12px chart label is (Han 12px, figures 7, separators 3.5), to size the right gutter.
const labelWidth = text => [...text].reduce((w, c) => w + (/[\u3000-\u9fff]/.test(c) ? 12 : /[\s,.]/.test(c) ? 3.5 : 7), 0);

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
  // The goal's label sits in the right gutter at the end of its line, beside the end labels, never over the series.
  const goalText = o.goal != null ? "目标 " + o.yFmt(o.goal) : null;
  const H = o.height || 200, padL = 40, padR = Math.max(48, goalText ? Math.ceil(labelWidth(goalText)) + 12 : 0), padT = 10, padB = 24;
  const pts = o.series.flatMap(sr => sr.points.filter(p => p.y != null));
  if (!pts.length) { host.replaceChildren(h("p", {class: "ys-empty"}, "这段时间没有数据。")); return; }
  const xs = [...new Set(o.series.flatMap(sr => sr.points.map(p => p.x)))].sort((a, b) => a - b);
  const minX = xs[0], maxX = xs[xs.length - 1];
  const ys = pts.flatMap(p => [p.y, p.lo, p.hi]).concat(o.goal ?? []).filter(v => v != null);
  const {lo, hi, ticks} = niceScale(Math.min(...ys), Math.max(...ys), 4, o.zero), yTick = tickLabel(ticks);
  const X = x => padL + (maxX === minX ? (W - padL - padR) / 2 : (x - minX) / (maxX - minX) * (W - padL - padR));
  const Y = y => padT + (1 - (y - lo) / (hi - lo)) * (H - padT - padB);
  const svg = s("svg", {viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.label, tabindex: 0});
  for (const t of ticks) {
    svg.append(s("line", {class: "grid", x1: padL, x2: W - padR, y1: Y(t), y2: Y(t)}),
      s("text", {class: "tick", x: padL - 6, y: Y(t) + 4, "text-anchor": "end"}, yTick(t)));
  }
  const tickXs = o.xTicks || xTickIndices(xs.length, Math.max(2, Math.floor((W - padL - padR) / 70))).map(i => xs[i]);
  tickXs.forEach((x, k) => svg.append(s("text", {class: "tick", x: X(x), y: H - 6,
    "text-anchor": tickXs.length === 1 ? "middle" : k === 0 ? "start" : k === tickXs.length - 1 ? "end" : "middle"}, o.xFmt(x))));
  if (o.goal != null) svg.append(s("line", {class: "goal", x1: padL, x2: W - padR, y1: Y(o.goal), y2: Y(o.goal)}));
  for (const m of o.markers || []) {
    svg.append(s("line", {class: "marker", x1: X(m.x), x2: X(m.x), y1: padT, y2: H - padB}),
      s("text", {class: "goal-label", x: X(m.x) + 4, y: padT + 10}, m.label));
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
      svg.append(s("circle", {class: "dot", cx: X(last.x), cy: Y(last.y), r: 4, style: {fill: sr.color}}));
      ends.push({y: Y(last.y), x: X(last.x), text: o.yFmt(last.y)});
    }
  }
  const placed = [];
  ends.sort((a, b) => a.y - b.y).forEach((e, i, all) => {
    if (i && Math.abs(e.y - all[i - 1].y) < 14) return;  // colliding end labels: legend and tooltip carry it
    svg.append(s("text", {class: "end-label", x: e.x + 8, y: e.y + 4}, e.text));
    if (e.x >= W - padR - 48) placed.push(e.y + 4);  // an end label at the right edge shares the gutter
  });
  if (goalText) {
    // level with its line; when an end label is there, the nearest clear height within 20px of it
    const want = Y(o.goal) + 4, clear = y => y >= padT + 8 && y <= H - padB + 4 && placed.every(p => Math.abs(p - y) >= 13);
    const y = [...Array(41).keys()].map(d => want + (d % 2 ? (d + 1) / 2 : -d / 2)).find(clear);
    if (y != null) svg.append(s("text", {class: "goal-label", x: W - padR + 8, y}, goalText));
  }
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
  const goalText = o.goal ? (o.goalLabel || "目标") + " " + compact(o.goal) : null;
  const H = o.height || 200, padL = 40, padR = goalText ? Math.ceil(labelWidth(goalText)) + 12 : 12, padT = 10, padB = 24;
  const n = o.points.length;
  if (!o.points.some(p => p.y != null)) { host.replaceChildren(h("p", {class: "ys-empty"}, "这段时间没有数据。")); return; }
  const max = Math.max(...o.points.map(p => p.y || 0), o.goal || 0) || 1;  // all zeros (a smoke-free week) still draws
  const {hi, ticks} = niceScale(0, max, 4, true, o.integer), yTick = tickLabel(ticks);
  const slot = (W - padL - padR) / n, bw = Math.max(1, Math.min(24, slot - 2));
  const Y = y => padT + (1 - y / hi) * (H - padT - padB), base = Y(0);
  const svg = s("svg", {viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.label, tabindex: 0});
  for (const t of ticks) {
    svg.append(s("line", {class: "grid", x1: padL, x2: W - padR, y1: Y(t), y2: Y(t)}),
      s("text", {class: "tick", x: padL - 6, y: Y(t) + 4, "text-anchor": "end"}, yTick(t)));
  }
  const bars = o.points.map((p, i) => {
    const x = padL + i * slot + (slot - bw) / 2, top = Y(p.y || 0), hgt = base - top, r = Math.min(4, bw / 2, hgt);
    const d = hgt <= 0 ? "" : `M${x},${base}V${top + r}Q${x},${top} ${x + r},${top}H${x + bw - r}Q${x + bw},${top} ${x + bw},${top + r}V${base}Z`;
    const bar = s("path", {class: "bar", d, style: {fill: o.color}});
    svg.append(bar);
    return bar;
  });
  if (goalText) {
    svg.append(s("line", {class: "goal", x1: padL, x2: W - padR, y1: Y(o.goal), y2: Y(o.goal)}),
      s("text", {class: "goal-label", x: W - padR + 8, y: Y(o.goal) + 4}, goalText));
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
  resizeTimer = setTimeout(() => {
    state.charts.forEach(draw => draw());
    document.querySelectorAll(".seg-scroller").forEach(segFade);
  }, 150);
});

// Blue to the goal; orange past it where going over is the bad direction (fat, smoking).
function meter(ratio, overIsBad = false) {
  const span = h("span", {class: "ys-meter-fill"});
  span.style.width = Math.min(100, Math.max(0, ratio * 100)) + "%";
  return h("div", {class: "ys-meter" + (overIsBad && ratio > 1 ? " ys-meter--serious" : ""), role: "presentation"}, span);
}
// notes: the line under the value, as facts (factRow)
function tile(label, value, unit, notes, ratio, overIsBad) {
  const sub = factRow(notes || []);
  return h("div", {class: "ys-tile tile"}, h("span", {class: "ys-tile-label"}, label),
    h("span", {class: "ys-tile-value value"}, value, unit ? h("span", {class: "ys-tile-unit"}, unit) : null),
    sub.childElementCount ? h("span", {class: "ys-tile-sub"}, sub) : null, ratio != null ? meter(ratio, overIsBad) : null);
}
const SLEEP_STAGES = [["deep_min", "深睡", "stage-deep"], ["core_min", "核心", "stage-core"], ["rem_min", "快速眼动", "stage-rem"],
  ["awake_min", "清醒", "stage-awake"]];
function sleepBar(sl) {
  const parts = SLEEP_STAGES.filter(([k]) => sl[k] > 0);
  const total = parts.reduce((a, [k]) => a + sl[k], 0);
  if (!total) return null;
  const bar = h("div", {class: "sleepbar", role: "img", "aria-label": parts.map(([k, l]) => `${l} ${hm(sl[k])}`).join("，")},
    parts.map(([k, , cls]) => { const sp = h("span", {class: cls}); sp.style.flex = `${sl[k]} 1 0`; return sp; }));
  const legend = h("ul", {class: "legend"}, parts.map(([k, l, cls]) => h("li", null, h("i", {class: cls}), `${l} ${hm(sl[k])}`)),
    sl.asleep_unstaged_min ? h("li", null, `未分期 ${hm(sl.asleep_unstaged_min)}`) : null);
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
  view.setAttribute("aria-busy", "true");
  try {
    state.charts = [];
    const content = await views[name](name === "day" ? arg : name === "record" ? null : arg);
    fill(view, content);
    if (name === "record" && arg) openRecord(arg);
  } catch (err) {
    view.replaceChildren(h("section", {class: "ys-card"}, h("div", {class: "ys-empty ys-empty--error state-error"}, failure(err))));
  } finally {
    view.classList.remove("loading");
    view.removeAttribute("aria-busy");
    if ($("bootSkeleton")) $("bootSkeleton").hidden = true;
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

  const dateInput = h("input", {class: "ys-input", type: "date", value: day, max: state.today, "aria-label": "跳到日期",
    onchange: e => e.target.value && go(e.target.value)});
  const daybar = h("div", {class: "daybar"},
    button(ysIcon("chevron-left"), () => go(shiftDay(day, -1)), "ys-btn ys-btn--icon ys-btn--quiet", {"aria-label": "前一天"}),
    h("span", {class: "daybar-date"}, dayLabel(day)),
    button(ysIcon("chevron-right"), () => go(shiftDay(day, 1)), "ys-btn ys-btn--icon ys-btn--quiet", {"aria-label": "后一天", disabled: day >= state.today}),
    h("div", {class: "daybar-end"},
      day !== state.today ? button("回到今天", () => go(state.today), "ys-btn ys-btn--sm") : null,
      h("span", {class: "ys-date ys-date--icon"}, dateInput, ysIcon("calendar"))));

  const tiles = [];
  const kcal = intake.kcal || 0;
  if (act.steps != null) {
    tiles.push(tile("步数", fmt(act.steps), "", [goals.steps && `目标 ${fmt(goals.steps)}`, act.distance_km && `${fmt(act.distance_km, 1)} km`],
      goals.steps ? act.steps / goals.steps : null));
  }
  const exGoal = rings.exercise_goal_min || goals.exercise_min;
  if (act.exercise_min != null) tiles.push(tile("锻炼", fmt(act.exercise_min), "分钟", [exGoal && `目标 ${fmt(exGoal)}`], exGoal ? act.exercise_min / exGoal : null));
  if (act.stand_hours != null) tiles.push(tile("站立", fmt(act.stand_hours), "小时", [rings.stand_goal_hours && `目标 ${fmt(rings.stand_goal_hours)}`],
    rings.stand_goal_hours ? act.stand_hours / rings.stand_goal_hours : null));
  if (sum.sleep?.asleep_min) {
    tiles.push(tile("睡眠", fmt(sum.sleep.asleep_min / 60, 1), "小时", win ? [`${clock(win.start)} 入睡`, `${clock(win.end)} 醒来`] : null,
      goals.sleep_hours ? sum.sleep.asleep_min / 60 / goals.sleep_hours : null));
  }
  if (act.resting_hr != null) tiles.push(tile("静息心率", fmt(act.resting_hr), "次/分", [act.hrv_ms && `心率变异性 ${fmt(act.hrv_ms)} ms`]));
  if (sum.body.weight_kg != null) {
    tiles.push(tile("体重", fmt(sum.body.weight_kg, 1), "kg", [{text: sum.body.weight_date === day ? "当天测量" : `${relDay(sum.body.weight_date)}测量`, wrap: true}]));
  }

  // Meals
  const meals = recs.filter(r => r.category === "meal");
  const macro = (key, label) => {
    const v = intake[key] || 0, g = goals[key];
    return h("div", {class: "macro"}, h("span", {class: "macro-label"}, label),
      h("span", {class: "macro-value"}, h("b", null, fmt(v)), g ? ` / ${fmt(g)} g` : " g"), meter(g ? v / g : 0, true));
  };
  const mealGroup = group("饮食", meals.length ? factRow([`${meals.length} 次`, `${fmt(kcal)} kcal`]) : null, h("div", {class: "ys-card"},
    h("div", {class: "macros"}, macro("protein_g", "蛋白质"), macro("fat_g", "脂肪"), macro("carbs_g", "碳水")),
    meals.length ? recordRows(meals, {mealLabel: true})
      : h("p", {class: "ys-empty ys-empty--compact"}, "还没有记录。对 agent 说一句“我中午吃了……”，就会出现在这里。")));

  const right = [];
  if (sum.sleep?.asleep_min) {
    right.push(group("睡眠", win ? `${clock(win.start)} – ${clock(win.end)}` : null, h("div", {class: "ys-card sleep-body"},
      h("p", {class: "sleep-total"}, h("span", {class: "value"}, hm(sum.sleep.asleep_min)), " 睡着"), sleepBar(sum.sleep))));
  }
  const workouts = recs.filter(r => r.category === "workout");
  if (workouts.length) right.push(group("运动", `${workouts.length} 次`, recordRows(workouts, {range: true})));
  const others = recs.filter(r => !["meal", "workout", "activity_summary", "smoking"].includes(r.category));
  right.push(group("其他记录", null, others.length ? recordRows(others, {chip: true})
    : emptyCard("心情、日记、用药、症状、花销都会出现在这里。")));

  const hero = [day === state.today || sum.smoking ? smokingCard(day, sum.smoking) : null, energyCard(day, sum)].filter(Boolean);
  return [daybar, hero.length ? h("div", {class: "ys-cols day-hero"}, hero) : null,
    tiles.length ? group("当天概览", null, h("div", {class: "ys-tiles day-tiles"}, tiles)) : null,
    h("div", {class: "ys-cols day-cols"}, h("div", {class: "ys-stack"}, mealGroup), h("div", {class: "ys-stack"}, right))];
}

// One record per cigarette. +1 counts at once and settles when the server answers; −1 takes back
// the day's latest entry (one off its count, or the whole record when it holds a single one).
function smokingCard(day, initial) {
  const live = day === state.today;
  let sm = initial || {cigarettes: 0, entries: [], daily_max: state.profile.goals?.cigarettes_max ?? null};
  let pending = 0, busy = false;
  const host = h("section", {class: "ys-card smoke", "aria-label": "抽烟"});
  const draw = () => {
    const n = sm.cigarettes + pending, max = sm.daily_max;
    const focused = host.contains(document.activeElement) && document.activeElement.getAttribute("aria-label");
    const last = sm.last_at && live
      ? [`上一根 ${sm.last_at.slice(0, 10) === day ? "" : relDay(sm.last_at.slice(0, 10)) + " "}${clock(sm.last_at)}`, ago(sm.last_at)] : [];
    // the day's facts; with nothing tracked yet, a hint instead
    const notes = initial || n ? factRow([...last, sm.yesterday != null && `${live ? "昨天" : "前一天"} ${sm.yesterday} 根`,
      sm.avg_prev_7d != null && `前 7 天日均 ${fmt(sm.avg_prev_7d, 1)} 根`,
      max != null && `上限 ${max} 根` + (n > max ? `，超出 ${n - max}` : "")])
      : "抽一根点一下 +1，也可以跟 agent 说“抽了一根”。";
    fill(host,
      h("div", {class: "ys-card-head"},
        h("h2", {class: "ys-card-title"}, live ? "今天抽烟" : "抽烟"),
        h("div", {class: "ys-card-aside"},
          h("a", {href: "#/trends", class: "ys-link", onclick: () => { state.trends.metric = "smoking"; }}, "趋势", ysIcon("chevron-right")))),
      h("div", {class: "smoke-body"},
        h("div", {class: "smoke-main"},
          h("p", {class: "value figure"}, String(n), h("span", {class: "unit"}, "根")),
          live ? h("div", {class: "smoke-actions"},
            n > 0 ? button("−1", undo, "ys-btn ys-btn--lg", {"aria-label": "撤销一根", disabled: pending > 0 || busy}) : null,
            button("+1 根", add, "ys-btn ys-btn--lg ys-btn--primary", {"aria-label": "记一根烟", disabled: busy})) : null),
        max != null ? meter(max > 0 ? n / max : n > 0 ? 2 : 0, true) : null,
        typeof notes === "string" || notes.childElementCount ? h("p", {class: "caption smoke-note"}, notes) : null),
      sm.entries.length ? h("div", {class: "smoke-times", "aria-label": "每一根的时间"}, sm.entries.map(e =>
        h("button", {class: "ys-chip", type: "button", onclick: () => openRecord(e.id)}, clock(e.start), e.count > 1 ? ` ×${e.count}` : null))) : null);
    if (focused) host.querySelector(`[aria-label="${focused}"]`)?.focus();  // keep keyboard focus on +1 / −1
  };
  async function add() {
    pending++; draw();
    let record;
    try {
      ({record} = await api("/records", {method: "POST", body: {category: "smoking", source: "manual", data: {count: 1}}}));
    } catch (err) { pending--; draw(); toastError(err); return; }
    pending--;
    if (record.local_date !== day) { toast("已记下", "ok"); await loadContext(); rerender(); return; }  // past midnight since the page loaded
    sm = {...sm, cigarettes: sm.cigarettes + 1, last_at: record.start, entries: [...sm.entries, {id: record.id, start: record.start, count: 1}]};
    draw();
    toast(`记下了，今天 ${sm.cigarettes + pending} 根`, "ok");
  }
  // Works from a fresh read, not the card's copy: an agent may have changed or added entries since it was drawn.
  async function undo() {
    if (pending || busy) return;
    busy = true; draw();
    const reload = async () => {
      sm = (await api("/summary/day?date=" + day)).smoking || {cigarettes: 0, entries: [], daily_max: sm.daily_max};
    };
    try {
      const fresh = await api("/summary/day");
      if (fresh.date !== day) { busy = false; toast("已经是新的一天了"); await loadContext(); rerender(); return; }
      const entries = fresh.smoking?.entries || [];
      const last = entries.filter(e => Date.parse(e.start) <= Date.now()).at(-1) || entries.at(-1);
      if (last) {
        if (last.count > 1) await api("/records/" + last.id, {method: "PATCH", body: {data: {count: last.count - 1}}});
        else await api("/records/" + last.id, {method: "DELETE"});
      }
      await reload();
      toast(last ? `撤销了一根，今天 ${sm.cigarettes} 根` : "今天还没有记录", last ? "ok" : undefined);
    } catch (err) {
      toastError(err);
      await reload().catch(() => {});
    }
    busy = false; draw();
  }
  draw();
  return host;
}

// The day's energy bar: eating pushes it right, resting and active burn pull it left. Its line
// is the kcal goal minus an ordinary day's burn, so eating the goal on an ordinary day lands on it.
const signed = n => (n > 0 ? "+" : n < 0 ? "−" : "") + fmt(Math.abs(n));
const hourLabel = x => { const m = Math.round(x * 60); return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };
function dayStartMs(day) {  // midnight of `day` in the profile timezone
  const name = new Intl.DateTimeFormat("en-US", {timeZone: state.tz, timeZoneName: "longOffset"})
    .formatToParts(new Date(day + "T12:00:00Z")).find(p => p.type === "timeZoneName")?.value || "GMT";
  const m = name.match(/([+-]\d{2}):?(\d{2})?/);
  return Date.parse(`${day}T00:00:00${m ? `${m[1]}:${m[2] || "00"}` : "Z"}`);
}
// The day's burn as facts (factRow), the first one led by `lead` (消耗：, 消耗 ).
const burnFacts = (e, lead) => [e.basal_kcal != null && `静息 ${fmt(e.basal_kcal)}`
  + (!e.final && e.basal_expected_kcal ? `（全天约 ${fmt(e.basal_expected_kcal)}）` : ""), `活动 ${fmt(e.active_kcal)}`]
  .filter(Boolean).map((fact, i) => i ? fact : lead + fact);

function energyCard(day, sum) {
  const e = sum.energy;
  if (!e) return null;
  const live = !e.final, which = live ? "今天" : "这天";
  const head = h("div", {class: "ys-card-head"}, h("h2", {class: "ys-card-title"}, live ? "今日热量" : "热量"));
  if (!e.logged || e.balance_kcal == null) {
    return h("section", {class: "ys-card energy"}, head, h("div", {class: "energy-empty"},
      h("p", {class: "body-text"}, !e.logged ? `${which}没记饮食，算不出收支。对 agent 说一句“我中午吃了……”就会算上。`
        : "还没有消耗数据，Apple 健康同步之后才算得出来。"),
      h("p", {class: "caption"}, factRow(burnFacts(e, "消耗：")))));
  }
  // Only an overshoot carries: a day that ended under its line starts the next from 0.
  const c = e.carry_kcal, p = e.previous_over_kcal ?? c, prev = live ? "昨天" : "前一天";
  const carry = p == null ? null : p === 0 ? `${prev}正好在目标线上`
    : p < 0 ? `${prev}比目标多减了 ${fmt(-p)}，${which}从 0 开始` : `${prev}离目标差 ${fmt(p)}，${which}从 +${fmt(p)} 开始`;
  const b = e.balance_kcal, end = live ? e.balance_tonight_kcal : b, t = e.target_kcal;
  const R = Math.max(Math.abs(b), Math.abs(end ?? b), Math.abs(t ?? 0), 300) * 1.3, pos = x => 50 + x / R * 50;
  const zone = t != null && b <= t ? "good" : b <= 0 ? "under" : "over";
  const tonight = live && end != null;
  // Marks (目标, 今晚) sit above the bar, the axis words below it; two marks close together lean apart.
  const marks = [t != null && {cls: "target", x: pos(t), text: "目标"}, tonight && {cls: "tonight", x: pos(end), text: "今晚"}].filter(Boolean);
  if (marks.length === 2 && Math.abs(marks[0].x - marks[1].x) < 14) {
    const [l, r] = marks[0].x <= marks[1].x ? marks : [marks[1], marks[0]];
    l.anchor = "anchor-end"; r.anchor = "anchor-start";
  }
  const bar = h("div", {class: "ebar-wrap"},
    marks.length ? h("div", {class: "ebar-marks", "aria-hidden": "true"}, marks.map(m =>
      h("span", {class: ["mark", m.cls, m.anchor].filter(Boolean).join(" "), style: {left: m.x + "%"}}, m.text))) : null,
    h("div", {class: "ebar", role: "img", "aria-label": `${live ? "现在" : "全天"} ${signed(b)} kcal`
      + (t != null ? `，目标线 ${signed(t)}` : "") + (tonight ? `，到今晚预计 ${signed(end)}` : "")},
      h("span", {class: `fill ${zone} ${b < 0 ? "neg" : "pos"}`, style: {left: Math.min(50, pos(b)) + "%", width: Math.abs(pos(b) - 50) + "%"}}),
      h("span", {class: "tick", style: {left: "50%"}}),
      t != null ? h("span", {class: "tick target", style: {left: pos(t) + "%"}}) : null,
      tonight ? h("span", {class: "tick tonight", style: {left: pos(end) + "%"}}) : null),
    h("div", {class: "ebar-axis", "aria-hidden": "true"}, h("span", null, "在减"), h("span", null, "维持"), h("span", null, "在长")));
  const over = e.over_target_kcal;
  let note;
  if (t == null) note = sum.goals?.kcal == null ? "在“设置”里填每天的热量目标，就会画出目标线。" : "还没有过去 30 天的消耗数据，暂时画不出目标线。";
  else if (over == null) note = "缺少静息消耗的数据，还估不出今晚会落在哪。";
  else if (live) {
    note = over > 0 && e.walk_minutes >= 1 ? `回到目标线约要走 ${hm(e.walk_minutes)}（约 ${fmt(e.walk_steps)} 步）；每走 30 分钟往回扣约 ${fmt(30 * e.walk_kcal_per_min)}。`
      : over > 0 ? "今晚基本落在目标线上。" : over === 0 ? "今晚正好落在目标线上。" : `今晚会落在目标线以下，还能再吃约 ${fmt(-over)} kcal。`;
  } else {
    note = (over === 0 ? "这天正好落在目标线上" : over > 0 ? `这天比目标线多了 ${fmt(over)} kcal` : `这天比目标线还多减了 ${fmt(-over)} kcal`)
      + (c ? `（含前一天带进的 ${signed(c)}）` : "") + "。";
  }
  const detail = h("div", {class: "energy-detail"});
  const more = disclosure("一天的变化", detail, {ontoggle: () => { if (more.open && !detail.childElementCount) energyDetail(detail, day); }});
  const sub = factRow([tonight && `到今晚预计 ${signed(end)}`, t != null && `目标线 ${signed(t)}`]);
  return h("section", {class: "ys-card energy"}, head,
    h("div", {class: "energy-body"},
      carry ? h("p", {class: "caption energy-carry"}, carry) : null,
      h("p", {class: "energy-now"}, h("span", {class: "lead"}, live ? "现在" : "全天"), h("span", {class: "value figure"}, signed(b)), h("span", {class: "unit"}, "kcal")),
      sub.childElementCount ? h("p", {class: "caption energy-sub"}, sub) : null,
      bar, h("p", {class: "energy-note"}, note)),
    more);
}

// The day's balance hour by hour: meals step it up, each hour's resting and active burn bring it down.
// It reads the day afresh, since the card may be older than the latest sync, and places everything by
// time since midnight in the profile timezone, so a meal logged at +08:00 on a trip lines up too.
async function energyDetail(host, day) {
  host.replaceChildren(h("p", {class: "caption"}, "正在读取…"));
  let sum, basal, active;
  try {
    [sum, basal, active] = await Promise.all([api("/summary/day?date=" + day), ...["basal_energy", "active_energy"].map(m =>
      api(`/metrics/${m}/series?` + q({from: day, to: day, bucket: "hour"})).then(r => r.points).catch(() => []))]);
  } catch (err) {
    const [problem, fix] = explain(err);
    host.replaceChildren(h("p", {class: "inline-error"}, ysIcon("error"),
      h("span", null, h("span", {class: "bad"}, problem + "。"), fix, errorDetail(err))));
    return;
  }
  const e = sum.energy;
  if (!e?.logged || e.balance_kcal == null) { host.replaceChildren(h("p", {class: "caption"}, "这天的数据不够画曲线。")); return; }
  const live = !e.final, start = dayStartMs(day), hoursIn = iso => Math.min(24, Math.max(0, (Date.parse(iso) - start) / 3.6e6));
  const B = Array(24).fill(0), A = Array(24).fill(0);
  for (const [arr, pts] of [[B, basal], [A, active]]) for (const p of pts) arr[Math.min(23, Math.floor(hoursIn(p.bucket)))] += p.value;
  const unsynced = (e.basal_kcal || 0) - B.reduce((s, v) => s + v, 0);
  if (!live && unsynced > 1) B.forEach((_, i) => { B[i] += unsynced / 24; });  // a day the average stood in for
  const nowH = live ? hoursIn(new Date().toISOString()) : 24;
  const lastH = Math.max(-1, ...[...B.keys()].filter(i => B[i] || A[i]));
  const span = i => live && i === lastH ? Math.max(0.05, Math.min(1, nowH - lastH)) : 1;  // the hour still being synced
  const cum = (arr, x) => arr.reduce((s, v, i) => s + v * Math.min(1, Math.max(0, (x - i) / span(i))), 0);
  const meals = sum.nutrition.meals.map(m => ({x: hoursIn(m.start), kcal: m.kcal || 0}));
  const at = x => (e.carry_kcal || 0) + meals.reduce((s, m) => s + (m.x <= x ? m.kcal : 0), 0) - cum(B, x) - cum(A, x);
  const xs = [nowH];
  for (let x = 0; x < nowH; x += 0.25) xs.push(x);
  for (const m of meals) if (m.x < nowH) xs.push(Math.max(0, m.x - 0.01), m.x);
  const solid = [...new Set(xs)].sort((a, b) => a - b).map(x => ({x, y: Math.round(at(x))}));
  solid[solid.length - 1].y = e.balance_kcal;  // end on the card's own number
  const series = [{label: "收支", color: "var(--ys-series-1)", points: solid}];
  if (live && e.balance_tonight_kcal != null) {
    series.push({label: "预计", color: "var(--ys-tertiary)", points: [{x: nowH, y: e.balance_kcal}, {x: 24, y: e.balance_tonight_kcal}]});
  }
  const plot = chart(el => lineChart(el, {label: "一天里的热量收支", unit: "kcal", yFmt: signed, zero: true, goal: e.target_kcal ?? null,
    xFmt: x => `${String(x).padStart(2, "0")}:00`, xTicks: [0, 6, 12, 18, 24], xTip: hourLabel, series, height: 180}));
  const byMeal = {};
  for (const m of sum.nutrition.meals) byMeal[m.meal_type] = (byMeal[m.meal_type] || 0) + (m.kcal || 0);
  // 吃进 1,954（早餐 300 · 午餐 620 · 晚餐 1,034）: each meal in the brackets is a fact too, so the line breaks between them
  const eaten = Object.entries(byMeal).map(([k, v]) => `${MEAL[k] || "饮食"} ${fmt(v)}`);
  const intakeFacts = (eaten.length ? eaten : [""]).map((fact, i, all) => (i ? "" : `吃进 ${fmt(e.intake_kcal)}（`) + fact + (i === all.length - 1 ? "）" : ""));
  fill(host, plot,
    series.length > 1 ? h("p", {class: "caption"}, "蓝线是已经发生的，灰线是之后不再吃也不再动的话到今晚的走向。") : null,
    h("p", {class: "body-text"}, factRow(intakeFacts)),
    h("p", {class: "body-text"}, factRow([...burnFacts(e, "消耗 "), e.carry_kcal && `${live ? "昨天" : "前一天"}带进 ${signed(e.carry_kcal)}`])));
}

const workoutName = d => (ACTIVITY[d.activity_type] && d.activity_type !== "other" ? ACTIVITY[d.activity_type] : d.activity_name || "运动")
  + (d.indoor ? "（室内）" : "");
function shortTitle(rec) {
  if (rec.category === "workout") return workoutName(rec.data || {});
  if (rec.category === "mood") return (rec.data?.emotions || []).join("、") || "心情";
  if (rec.category === "smoking" && rec.title.startsWith("抽烟 ")) return SMOKE_KIND[rec.data?.kind] || "香烟";
  const prefix = catTitle(rec.category) + "：";
  return rec.title.startsWith(prefix) ? rec.title.slice(prefix.length) : rec.title;
}
function recordRows(recs, opts = {}) {
  return h("ul", {class: "ys-list rows"}, recs.map(rec => {
    const side = recordSide(rec);
    const label = opts.mealLabel ? (MEAL[rec.data?.meal_type] || "饮食") : null;
    const title = opts.mealLabel ? (rec.data.items || []).map(i => i.name).join("、") || rec.title : shortTitle(rec);
    // Items without kcal count as 0 in the totals, so say so rather than show a short total as if it were whole.
    const noKcal = rec.category === "meal" ? (rec.data?.items || []).filter(i => typeof i.kcal !== "number").length : 0;
    const sub = factRow([...recordSub(rec), noKcal && {text: `${noKcal} 项未填热量`, cls: "warn"}]);
    return h("li", null, h("button", {class: "ys-row ys-row--top row" + (rec.deleted_at ? " deleted" : ""), type: "button", onclick: () => openRecord(rec.id)},
      h("span", {class: "ys-row-lead"}, h("time", {class: "ys-row-time"}, clock(rec.start)),
        opts.range && rec.end ? h("time", {class: "ys-row-time"}, clock(rec.end)) : null,
        label ? h("span", {class: "ys-tag chip"}, label) : null),
      h("span", {class: "ys-row-text"},
        h("span", {class: "ys-row-title"}, opts.chip ? h("span", {class: "ys-tag chip"}, catTitle(rec.category)) : null,
          h("span", {class: "title"}, title)),
        sub.childElementCount ? h("span", {class: "ys-row-sub sub"}, sub) : null),
      side ? h("span", {class: "ys-row-trail" + (String(side[1]).startsWith("/") ? " tight" : "")},
        h("span", {class: "ys-row-value"}, side[0]), side[1]) : null,
      ysIcon("chevron-right", "ys-row-chevron")));
  }));
}

// --- records list ------------------------------------------------------------------------------

async function viewRecords() {
  const f = state.records;
  const cats = await api("/categories");
  const listHost = h("div", {class: "records-list"});
  const filterButtons = segScroller(h("div", {class: "ys-seg ys-seg--inline seg-scroll", role: "group", "aria-label": "按类别筛选"},
    [["", "全部"], ...cats.categories.filter(c => c.records > 0).map(c => [c.name, c.title])].map(([name, title]) =>
      h("button", {type: "button", "aria-pressed": String(f.category === name), onclick: () => { f.category = name; rerender(); }}, title))));
  let timer;
  const search = h("input", {class: "ys-input", type: "search", placeholder: "搜索标题、备注、内容", value: f.q, "aria-label": "搜索记录",
    oninput: e => { clearTimeout(timer); timer = setTimeout(() => { f.q = e.target.value.trim(); load(true); }, 300); }});
  const deleted = h("label", {class: "ys-check-label push"}, h("input", {class: "ys-check", type: "checkbox", checked: f.deleted,
    onchange: e => { f.deleted = e.target.checked; load(true); }}), "显示已删除");
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
      shown.length ? h("div", {class: "day-groups"}, [...groups].map(([d, rs]) => h("section", {class: "day-group"},
        h("div", {class: "ys-section-head"}, h("h3", {class: "ys-section-title"},
          h("a", {href: d === state.today ? "#/today" : "#/day/" + d}, relDay(d), ysIcon("chevron-right")))),
        recordRows(rs, {chip: !f.category, range: f.category === "workout"}))))
        : emptyCard(f.q ? "没有找到匹配的记录。" : "还没有记录。"),
      cursor ? h("div", {class: "more-row"}, button("加载更多", () => load(false), "ys-btn more")) : null);
  }
  await load(true);
  revealPressed(filterButtons);
  return [h("div", {class: "filter-stack"}, filterButtons,
    h("div", {class: "toolbar-row"}, h("label", {class: "ys-search"}, ysIcon("search"), search), deleted)), listHost];
}

// --- record detail -------------------------------------------------------------------------------

async function openRecord(id) {
  const dlg = $("recordDialog"), body = $("recordBody");
  if (openRecord.shown !== id) { $("recordTitle").textContent = ""; $("recordMeta").textContent = ""; }  // no stale title while the next one loads
  openRecord.shown = id;
  body.replaceChildren(h("p", {class: "loading-line"}, "正在读取…"));
  openModal(dlg);
  let rec;
  try { rec = await api("/records/" + encodeURIComponent(id)); }
  catch (err) { body.replaceChildren(h("div", {class: "ys-empty ys-empty--error state-error"}, failure(err))); return; }
  $("recordTitle").textContent = rec.title;
  $("recordMeta").replaceChildren(factRow([{text: catTitle(rec.category), wrap: true}, `${relDay(rec.local_date)} ${clock(rec.start)}${rec.end ? "–" + clock(rec.end) : ""}`,
    {text: who(rec) + (/[A-Za-z0-9]$/.test(who(rec) || "") ? " 记录" : "记录"), wrap: true}]));  // a space between Latin and Han only
  const parts = [], main = [];
  if (rec.deleted_at) parts.push(h("div", {class: "ys-alert ys-alert--warn"}, ysIcon("warning"), h("div", {class: "ys-alert-body"}, "这条记录已删除，不计入统计。")));
  if (rec.category === "meal") main.push(mealDetail(rec));
  else if (rec.category === "workout") main.push(...workoutDetail(rec));
  else if (rec.category === "journal") main.push(h("p", {class: "journal"}, rec.data.text || ""));
  const facts = factList(rec.data, rec.category);
  if (facts) main.push(facts);
  if (main.length) parts.push(h("div", {class: "block"}, main));
  const extra = Object.fromEntries(["other_stats", "metadata", "sets"].filter(k => rec.data[k] && Object.keys(rec.data[k]).length).map(k => [k, rec.data[k]]));
  if (Object.keys(extra).length) parts.push(disclosure("更多数据", h("pre", {class: "json ys-mono"}, JSON.stringify(extra, null, 2))));
  if (rec.attachments?.length) parts.push(attachmentSection(rec));

  const note = h("textarea", {class: "ys-input", rows: 2, placeholder: "备注", "aria-label": "备注"});
  note.value = rec.note || "";
  const title = h("input", {class: "ys-input", type: "text", value: rec.title, "aria-label": "标题"});
  parts.push(h("div", {class: "block"}, h("h3", {class: "block-title"}, "标题和备注"), title, note,
    h("div", {class: "actions"}, button("保存", async () => {
      await guarded(() => api("/records/" + rec.id, {method: "PATCH", body: {title: title.value.trim() || null, note: note.value}}));
      toast("已保存", "ok"); openRecord(rec.id); rerender();
    }, "ys-btn ys-btn--sm"))));

  const raw = h("textarea", {rows: 8, class: "ys-input ys-mono", spellcheck: "false", "aria-label": "编辑数据（JSON）"});
  raw.value = JSON.stringify(rec.data, null, 2);
  parts.push(disclosure("编辑数据（JSON）", [raw,
    h("div", {class: "actions"}, button("保存数据", async () => {
      let data;
      try { data = JSON.parse(raw.value); } catch { toast("不是有效的 JSON", "bad"); return; }
      const patch = {...data};
      for (const k of Object.keys(rec.data)) if (!(k in data)) patch[k] = null;  // removed keys
      await guarded(() => api("/records/" + rec.id, {method: "PATCH", body: {data: patch}}));
      toast("已保存", "ok"); openRecord(rec.id); rerender();
    }, "ys-btn ys-btn--sm"), h("span", {class: "caption"}, "饮食的合计会自动重算。"))]));

  const history = h("ol", {class: "history"}, h("li", null, h("span", {class: "muted"}, "正在读取…")));
  parts.push(disclosure("修改历史", history));
  api("/changes?" + q({entity_id: rec.id, limit: 50})).then(r => history.replaceChildren(...(r.changes.length ? r.changes.map(c =>
    h("li", null, h("time", null, stamp(c.at)), h("span", null, `${c.actor} ${ACTIONS[c.action] || c.action}`)))
    : [h("li", {class: "history-empty"}, "还没有变更。")])))
    .catch(err => history.replaceChildren(h("li", null, h("span"), h("span", {class: "bad"}, errorLine(err), errorDetail(err)))));

  parts.push(h("div", {class: "record-foot"}, rec.deleted_at
    ? button("恢复", async () => { await guarded(() => api(`/records/${rec.id}/restore`, {method: "POST"})); toast("已恢复", "ok"); openRecord(rec.id); rerender(); })
    : button("删除", async () => {
      if (!confirm("删除这条记录？之后可以在“记录”里勾选“显示已删除”找回。")) return;
      await guarded(() => api("/records/" + rec.id, {method: "DELETE"}));
      toast("已删除", "ok"); dlg.close(); rerender();
    }, "ys-btn ys-btn--danger")));
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
  return h("dl", {class: "ys-facts facts" + (rows.length > 6 ? " two" : "")}, rows.flatMap(([k, v]) => {
    const [label, unit] = category === "smoking" && k === "count" ? ["数量", "根"] : FIELDS[k] || [k, ""];
    const digits = DIGITS[k] ?? (Math.abs(v) >= 10 ? 0 : 1);
    const text = Array.isArray(v) ? v.join("、") : typeof v === "boolean" ? (v ? "是" : "否") : typeof v === "number" ? fmt(v, digits)
      : category === "smoking" && k === "kind" ? SMOKE_KIND[v] || v : v;
    return [h("dt", null, label), h("dd", null, text, unit ? h("span", {class: "muted"}, " " + unit) : null)];
  }));
}

function mealDetail(rec) {
  const d = rec.data, cols = NUTRIENTS.slice(0, 4);
  // 热量 trails each row on a phone; 份量 and the macros wrap underneath
  const nutrients = vals => cols.map(([k, l, u], n) => n === 0 ? prKey(fmt(vals?.[k], 1), u, "ys-cell-num") : prMore(l, fmt(vals?.[k], 1), u, "ys-cell-num"));
  const portion = i => {  // "1碗（550 g）", as the food list writes a serving
    const g = i.grams != null ? `${fmt(i.grams)} g` : null;
    if (i.amount == null) return g || "—";
    const a = `${fmt(i.amount, 1)}${i.unit || ""}`;
    // "200g" already says its grams; grams an agent sent that differ from it stay: "200g（250 g）"
    const same = /^\s*(g|grams?|克)\s*$/i.test(i.unit || "") && fmt(i.grams, 1) === fmt(i.amount, 1);
    return g && !same ? `${a}（${g}）` : a;
  };
  const table = phoneRows(h("table", {class: "ys-table"},
    h("thead", null, h("tr", null, h("th", null, "食物"), h("th", null, "份量"), cols.map(([, l, u]) => h("th", {class: "ys-cell-num"}, `${l}（${u}）`)))),
    h("tbody", null, (d.items || []).map(i => h("tr", null, h("td", {class: "ys-cell-main pr-head"}, i.name, i.confidence ? h("span", {class: "item-note"}, ` 估算·${{low: "低", medium: "中", high: "高"}[i.confidence] || i.confidence}`) : null),
      prMore("份量", portion(i), null, "ys-cell-nowrap"), nutrients(i)))),
    h("tfoot", null, h("tr", null, h("td", {colspan: 2, class: "pr-head"}, "合计"), nutrients(d.totals)))));
  const more = NUTRIENTS.slice(4).filter(([k]) => d.totals?.[k] != null);
  const kind = factRow([MEAL[d.meal_type], d.estimated ? "数值为估算" : null]);
  return [kind.childElementCount ? h("p", {class: "caption"}, kind) : null,
    h("div", {class: "table-group"}, h("div", {class: "ys-table-wrap"}, table)),
    more.length ? h("p", {class: "caption"}, factRow(more.map(([k, l, u]) => `${l} ${fmt(d.totals[k], 1)} ${u}`))) : null,
    d.estimation_note ? h("p", {class: "caption"}, "估算依据：" + d.estimation_note) : null];
}

function workoutDetail(rec) {
  const host = h("div", {class: "workout-detail"}, h("p", {class: "caption"}, "正在读取心率…"));
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
    const table = h("details", {class: "ys-disclosure sub-disclosure"},
      h("summary", null, h("span", null, "心率数据表"), ysIcon("chevron-right", "ys-disclosure-chevron")),
      h("div", {class: "ys-disclosure-body"}, h("div", {class: "table-group"}, h("div", {class: "ys-table-wrap"}, h("table", {class: "ys-table"},
        h("thead", null, h("tr", null, h("th", null, "时间"), h("th", {class: "ys-cell-num"}, "平均"), h("th", {class: "ys-cell-num"}, "范围"))),
        h("tbody", null, points.map(p => h("tr", null, h("td", {class: "ys-cell-nowrap"}, new Date(p.x).toLocaleTimeString("zh-CN", {timeZone: state.tz})),
          h("td", {class: "ys-cell-num"}, fmt(p.y)), h("td", {class: "ys-cell-num"}, p.lo != null ? `${fmt(p.lo)}–${fmt(p.hi)}` : "")))))))));
    host.replaceChildren(h("div", {class: "block-head"}, h("h3", {class: "block-title"}, "心率（次/分）"),
      rec2 ? h("span", {class: "caption"}, "结束后两分钟的恢复也画在里面") : null),
    chart(el => lineChart(el, {label: "运动中的心率", series: [{label: "心率", color: "var(--ys-series-1)", points}], unit: "次/分",
      yFmt: v => fmt(v), xFmt: mins, xTip: x => new Date(x).toLocaleTimeString("zh-CN", {timeZone: state.tz}),
      markers: endTs && rec2 ? [{x: endTs, label: "结束"}] : [], xTicks, height: 180})), table);
  }).catch(() => host.replaceChildren());
  return [h("p", {class: "caption"}, workoutName(rec.data)), host];
}

function attachmentSection(rec) {
  const thumbs = h("div", {class: "thumbs"}), files = h("ul", {class: "files"});
  for (const a of rec.attachments) {
    if (a.mime.startsWith("image/")) {
      const img = h("img", {alt: a.filename});
      api("/attachments/" + a.id).then(blob => { img.src = URL.createObjectURL(blob); });
      thumbs.append(img);
    } else {
      files.append(h("li", null, h("span", {class: "size"}, `${fmt(a.size / 1024)} KB`),
        h("button", {class: "ys-link", type: "button", onclick: () => download("/attachments/" + a.id, a.filename)}, ysIcon("download"), a.filename)));
    }
  }
  return h("div", {class: "block"}, h("h3", {class: "block-title"}, "附件"), thumbs.children.length ? thumbs : null, files.children.length ? files : null);
}

async function download(path, filename) {
  try {
    const data = await api(path);
    const blob = data instanceof Blob ? data : new Blob([JSON.stringify(data, null, 2)], {type: "application/json"});
    const a = h("a", {href: URL.createObjectURL(blob), download: filename});
    document.body.append(a); a.click(); a.remove();
  } catch (err) { toastError(err); }
}
async function guarded(fn) {
  try { return await fn(); } catch (err) { toastError(err); throw err; }
}

// --- trends ----------------------------------------------------------------------------------

const UNIT_LABELS = {"count": "", "count/min": "次/分", "min": "分钟", "hr": "小时", "degC": "°C", "mL": "ml",
  "km/hr": "km/h", "appleEffortScore": "", "dBASPL": "dB"};
const TREND_METRICS = [["energy", "热量"], ["body_mass", "体重"], ["step_count", "步数"], ["sleep", "睡眠"],
  ["exercise_time", "锻炼"], ["resting_heart_rate", "静息心率"], ["heart_rate_variability", "心率变异性"], ["smoking", "抽烟"]];
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
  const days = (await api("/summary/days?" + q({from, to}))).days, smoking = days.some(d => d.cigarettes != null);
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
  } else if (t.metric === "smoking") {
    rows = days.map(d => ({date: d.date, value: d.cigarettes ?? null}));
    kind = "bar"; unit = "根"; title = "抽烟"; digits = week ? 1 : 0; goal = state.profile.goals?.cigarettes_max;
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

  const metricSeg = segScroller(h("div", {class: "ys-seg ys-seg--inline seg-scroll", role: "group", "aria-label": "指标"},
    TREND_METRICS.filter(([k]) => ["energy", "sleep"].includes(k) || metricMeta[k] || k === "smoking" && (t.metric === k || smoking)).map(([k, l]) =>
      h("button", {type: "button", "aria-pressed": String(t.metric === k), onclick: () => { t.metric = k; rerender(); }}, l))));
  const head = h("div", {class: "trend-controls"},
    h("div", {class: "ys-seg", role: "group", "aria-label": "时间范围"}, RANGES.map(([n, l]) =>
      h("button", {type: "button", "aria-pressed": String(t.range === n), onclick: () => { t.range = n; rerender(); }}, l))),
    metricSeg,
    h("span", {class: "ys-select"}, h("select", {"aria-label": "其他指标", onchange: e => { if (e.target.value) { t.metric = e.target.value; rerender(); } }},
      h("option", {value: ""}, "其他指标…"),
      state.metrics.filter(m => !TREND_METRICS.some(([k]) => k === m.name)).map(m =>
        h("option", {value: m.name, selected: t.metric === m.name}, m.title))), ysIcon("chevron-down")));
  revealPressed(metricSeg);

  const mean = xs => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  const values = kind === "energy" ? rows.map(r => r.intake).filter(v => v != null) : rows.map(r => r.value).filter(v => v != null);
  const avg = mean(values), per = week ? "周均" : "日均";
  const burnedVals = kind === "energy" ? rows.map(r => r.burned).filter(v => v != null) : [];
  const stat = (label, value, u) => h("div", {class: "stat"}, h("dt", null, label), h("dd", null, value, u ? h("span", {class: "unit"}, u) : null));
  const stats = kind === "energy" ? h("dl", {class: "stats"},
    stat(`${per}摄入`, values.length ? yFmt(avg) : "—"),
    stat(`${per}消耗`, burnedVals.length ? yFmt(mean(burnedVals)) : "—"),
    goal ? stat("目标", yFmt(goal)) : null)
    : values.length ? h("dl", {class: "stats"},
      stat(per, yFmt(avg), unit),
      stat("最高", yFmt(Math.max(...values))),
      stat("最低", yFmt(Math.min(...values))))
      : h("p", {class: "stats-empty"}, "没有数据");
  const counted = kind !== "energy" && values.length ? `${values.length} ${week ? "周" : "天"}有数据` : null;

  let plot, legend = null, tableRows;
  if (kind === "energy") {
    const x = r => parseDay(r.date).getTime();
    plot = chart(el => lineChart(el, {label: "每天摄入与消耗的热量", unit, yFmt, zero: true, goal: goal || null,
      xFmt: v => shortDay(new Date(v).toISOString().slice(0, 10)), xTip: v => label({date: new Date(v).toISOString().slice(0, 10)}),
      series: [{label: "摄入", color: "var(--ys-series-1)", points: rows.map(r => ({x: x(r), y: r.intake}))},
        {label: "消耗", color: "var(--ys-series-2)", points: rows.map(r => ({x: x(r), y: r.burned}))}]}));
    legend = h("ul", {class: "legend"}, h("li", null, h("i", {class: "line series-1"}), "摄入"), h("li", null, h("i", {class: "line series-2"}), "消耗（活动 + 静息）"));
    tableRows = rows.map(r => [label(r), yFmt(r.intake), yFmt(r.burned)]);
  } else if (kind === "bar") {
    plot = chart(el => barChart(el, {label: title, unit, yFmt, color: "var(--ys-series-1)", goal, goalLabel: t.metric === "smoking" ? "上限" : null,
      integer: t.metric === "smoking" || digits === 0, points: rows.map(r => ({label: shortDay(r.date), tip: label(r), y: r.value}))}));
    tableRows = rows.map(r => [label(r), yFmt(r.value)]);
  } else {
    plot = chart(el => lineChart(el, {label: title, unit, yFmt, xFmt: v => shortDay(new Date(v).toISOString().slice(0, 10)),
      xTip: v => label({date: new Date(v).toISOString().slice(0, 10)}),
      series: [{label: title, color: "var(--ys-series-1)", points: rows.map(r => ({x: parseDay(r.date).getTime(), y: r.value}))}]}));
    tableRows = rows.map(r => [label(r), yFmt(r.value)]);
  }
  const table = disclosure("数据表", h("div", {class: "ys-table-wrap"}, h("table", {class: "ys-table"},
    h("thead", null, h("tr", null, h("th", null, week ? "周" : "日期"), kind === "energy" ? [h("th", {class: "ys-cell-num"}, "摄入"), h("th", {class: "ys-cell-num"}, "消耗")]
      : h("th", {class: "ys-cell-num"}, unit || "数值"))),
    h("tbody", null, tableRows.slice().reverse().map(cells => h("tr", null, h("td", {class: "ys-cell-nowrap"}, cells[0]), cells.slice(1).map(c => h("td", {class: "ys-cell-num"}, c))))))));

  const chartCard = h("section", {class: "ys-card chart-card"},
    h("div", {class: "ys-card-head"}, h("h2", {class: "ys-card-title"}, title + (unit ? `（${unit}${week ? "，周平均" : ""}）` : week ? "（周平均）" : "")),
      counted ? h("div", {class: "ys-card-aside"}, counted) : null),
    h("div", {class: "chart-card-body"}, stats, legend, plot), table);

  const filled = days.filter(d => Object.keys(d.intake).length || d.cigarettes > 0
    || ["steps", "active_kcal", "exercise_min", "sleep_hours", "resting_hr", "weight_kg"].some(k => d[k] != null));
  const openDay = d => () => { location.hash = d.date === state.today ? "#/today" : "#/day/" + d.date; };
  // on a phone each day is a row: the date, its intake trailing, the rest as phrases underneath
  const overview = t.range <= 30 && filled.length ? card("每日概览", `${filled.length} 天有数据`, h("div", {class: "ys-table-wrap"}, phoneRows(h("table", {class: "ys-table ys-table--hover"},
    h("thead", null, h("tr", null, ["日期", "摄入", "步数", "活动", "锻炼", "睡眠", "静息心率", "体重", smoking && "抽烟"].filter(Boolean)
      .map((l, i) => h("th", {class: i ? "ys-cell-num" : null}, l)))),
    h("tbody", null, filled.slice().reverse().map(d => h("tr", rowAction(openDay(d)),
      h("td", {class: "ys-cell-nowrap pr-head"}, relDay(d.date)), prKey(fmt(d.intake.kcal), "kcal", "ys-cell-num", true),
      prMore("步数", fmt(d.steps), null, "ys-cell-num"), prMore("活动", fmt(d.active_kcal), null, "ys-cell-num"),
      prMore("锻炼", fmt(d.exercise_min), null, "ys-cell-num"), prMore("睡眠", fmt(d.sleep_hours, 1), null, "ys-cell-num"),
      prMore("静息心率", fmt(d.resting_hr), null, "ys-cell-num"), prMore("体重", fmt(d.weight_kg, 1), null, "ys-cell-num"),
      smoking ? prMore("抽烟", fmt(d.cigarettes), null, "ys-cell-num") : null))))))) : null;
  return [head, chartCard, overview];
}

// A segmented control wider than its row scrolls sideways inside a .seg-scroller, which on a phone runs
// to the screen edges; an end with more segments beyond it fades out, so the overflow reads as scrolling.
function segScroller(seg) {
  const box = h("div", {class: "seg-scroller", onscroll: () => segFade(box)}, seg);
  return box;
}
function segFade(box) {
  const more = box.scrollWidth - box.clientWidth;
  box.classList.toggle("fade-start", more > 1 && box.scrollLeft > 1);
  box.classList.toggle("fade-end", more > 1 && box.scrollLeft < more - 1);
}
// The pressed segment stays in sight, clear of the faded ends (once the control is in the page).
function revealPressed(box) {
  requestAnimationFrame(() => {
    const on = box.querySelector('[aria-pressed="true"]');
    if (on && box.scrollWidth > box.clientWidth) {
      const b = box.getBoundingClientRect(), r = on.getBoundingClientRect(), edge = 40;
      if (r.right > b.right - edge) box.scrollLeft += r.right - (b.right - edge);
      else if (r.left < b.left + edge) box.scrollLeft -= b.left + edge - r.left;
    }
    segFade(box);
  });
}

// --- foods -----------------------------------------------------------------------------------

async function viewFoods() {
  const list = h("div");
  async function load() {
    const {foods} = await api("/foods?" + q({q: state.foods, limit: 200}));
    // on a phone each food is a row: its name, the kcal per serving trailing, the rest as phrases underneath
    list.replaceChildren(foods.length ? h("section", {class: "ys-card"}, h("div", {class: "ys-table-wrap"}, phoneRows(h("table", {class: "ys-table ys-table--hover"},
      h("thead", null, h("tr", null, h("th", null, "食物"), h("th", null, "一份"), h("th", {class: "ys-cell-num"}, "每份热量"),
        h("th", {class: "ys-cell-num"}, "每 100 g"), h("th", {class: "ys-cell-num"}, "用过"), h("th", null, "来源"))),
      h("tbody", null, foods.map(f => h("tr", rowAction(() => editFood(f)),
        // the name with its aliases is one fact, the brand a second one
        h("td", {class: "ys-cell-main pr-head"}, factRow([{nodes: [h("span", {class: "food-name"}, f.name),
          f.aliases?.length ? h("span", {class: "food-extra"}, " " + f.aliases.join("、")) : null], wrap: true},
          f.brand && {nodes: [h("span", {class: "food-extra food-brand"}, f.brand)], wrap: true}])),
        prMore("一份", f.serving ? `${fmt(f.serving.amount, 1)} ${f.serving.unit || ""}${f.serving.grams ? `（${fmt(f.serving.grams)} g）` : ""}` : "—", null, "ys-cell-nowrap"),
        prKey(fmt(f.per_serving?.kcal), "kcal", "ys-cell-num", true), prMore("每 100 g", fmt(f.per_100g?.kcal), "kcal", "ys-cell-num"),
        prMore("用过", fmt(f.use_count), "次", "ys-cell-num"), prMore("来源", FOOD_SOURCES[f.source] || f.source, null, "ys-cell-nowrap"))))))))
      : emptyCard(state.foods ? "没有找到。" : "还没有食物。agent 记录饮食时遇到常吃的东西会存进来，也可以手动新建。"));
  }
  let timer;
  const search = h("input", {class: "ys-input", type: "search", placeholder: "搜索食物", value: state.foods, "aria-label": "搜索食物",
    oninput: e => { clearTimeout(timer); timer = setTimeout(() => { state.foods = e.target.value.trim(); load(); }, 300); }});
  await load();
  return [h("div", {class: "toolbar-row"}, h("label", {class: "ys-search"}, ysIcon("search"), search),
    button([ysIcon("plus"), "新建食物"], () => editFood(null), "ys-btn push")), list];
}
const FOOD_SOURCES = {estimate: "估算", label: "包装标签", user: "自己填写", database: "数据库"};

function field(label, input, cls) { return h("label", {class: "ys-field" + (cls ? " " + cls : "")}, h("span", {class: "ys-field-label"}, label), input); }
const numInput = (name, value, step = "any") => h("input", {class: "ys-input", type: "number", name, step, min: 0, value: value ?? "",
  inputmode: String(step) === "1" ? "numeric" : "decimal"});
const textInput = (name, value, attrs = {}) => h("input", {class: "ys-input", type: "text", name, value: value ?? "", ...attrs});
const textArea = (attrs, value) => h("textarea", {class: "ys-input", ...attrs}, value);
const selectBox = (attrs, options) => h("span", {class: "ys-select"}, h("select", attrs, options), ysIcon("chevron-down"));
const formHeading = text => h("h3", {class: "form-heading"}, text);

function editFood(food) {
  const f = food || {};
  $("foodTitle").textContent = food ? "编辑食物" : "新建食物";
  $("foodError").hidden = true;
  $("foodDelete").hidden = !food;
  const fields = [
    field("名称", textInput("name", f.name, {required: true}), "wide-phone"), field("别名（用顿号或逗号分隔）", textInput("aliases", (f.aliases || []).join("、")), "wide-phone"),
    field("品牌", textInput("brand", f.brand)),
    field("来源", selectBox({name: "source"}, Object.entries(FOOD_SOURCES).map(([v, l]) => h("option", {value: v, selected: (f.source || "user") === v}, l)))),
    formHeading("一份是多少"),
    field("数量", numInput("serving_amount", f.serving?.amount)), field("单位（碗、个、杯…）", textInput("serving_unit", f.serving?.unit)),
    field("约多少克", numInput("serving_grams", f.serving?.grams)),
    formHeading("每份营养"), ...NUTRIENTS.slice(0, 7).map(([k, l, u]) => field(`${l}（${u}）`, numInput("ps_" + k, f.per_serving?.[k]))),
    formHeading("每 100 g 营养"), ...NUTRIENTS.slice(0, 7).map(([k, l, u]) => field(`${l}（${u}）`, numInput("p100_" + k, f.per_100g?.[k]))),
    field("备注", textArea({name: "note", rows: 2}, f.note || ""), "full")];
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
      $("foodDialog").close(); toast("已保存", "ok"); rerender();
    } catch (err) { showError($("foodError"), err); }
  };
  $("foodDelete").onclick = async () => {
    if (!confirm(`删除“${f.name}”？已有的饮食记录不受影响。`)) return;
    await guarded(() => api("/foods/" + food.id, {method: "DELETE"}));
    $("foodDialog").close(); toast("已删除", "ok"); rerender();
  };
  openModal($("foodDialog"));
}

// --- settings --------------------------------------------------------------------------------

const GOALS = [["kcal", "热量", "kcal"], ["protein_g", "蛋白质", "g"], ["fat_g", "脂肪", "g"], ["carbs_g", "碳水", "g"],
  ["steps", "步数", "步"], ["exercise_min", "锻炼", "分钟"], ["sleep_hours", "睡眠", "小时"], ["water_ml", "饮水", "ml"],
  ["weight_kg", "目标体重", "kg"], ["cigarettes_max", "抽烟上限", "根/天"]];
const SCOPES = {read: "只读", write: "读写", admin: "管理"};

async function viewSettings() {
  const [{profile}, tokens, imports, changes] = await Promise.all([api("/profile"), api("/tokens"), api("/imports?limit=10"), api("/changes?limit=30")]);
  const p = profile, goals = p.goals || {}, diet = p.diet || {};
  const form = h("form", {class: "ys-form-grid settings-form", onsubmit: async e => {
    e.preventDefault();
    const el = form.elements, v = n => el[n].value.trim(), num = n => v(n) === "" ? null : Number(v(n));
    const list = n => v(n).split(/[、,，\n]+/).map(x => x.trim()).filter(Boolean);
    const newGoals = {...goals};
    for (const [k] of GOALS) newGoals[k] = num("goal_" + k);  // goals merge on the server; null drops one
    const body = {name: v("name") || null, birthday: v("birthday") || null, sex: v("sex") || null, height_cm: num("height_cm"),
      timezone: v("timezone") || null, currency: v("currency").toUpperCase() || null,
      goals: newGoals,
      diet: {...diet, allergies: list("allergies"), preferences: list("preferences"), avoid: list("avoid")}, notes: v("notes") || null};
    await guarded(() => api("/profile", {method: "PATCH", body}));
    await loadContext(); toast("已保存", "ok");
  }},
  field("称呼", textInput("name", p.name)), field("生日", h("input", {class: "ys-input", type: "date", name: "birthday", value: p.birthday || ""})),
  field("性别", textInput("sex", p.sex)), field("身高（cm）", numInput("height_cm", p.height_cm)),
  field("时区", textInput("timezone", p.timezone, {placeholder: "Asia/Tokyo"})), field("默认币种", textInput("currency", p.currency, {placeholder: "JPY"})),
  formHeading("每日目标"), GOALS.map(([k, l, u]) => field(`${l}（${u}）`, numInput("goal_" + k, goals[k]))),
  formHeading("饮食"), field("过敏", textInput("allergies", (diet.allergies || []).join("、")), "wide-phone"),
  field("偏好", textInput("preferences", (diet.preferences || []).join("、")), "wide-phone"), field("不吃", textInput("avoid", (diet.avoid || []).join("、")), "wide-phone"),
  field("给 agent 的备注", textArea({name: "notes", rows: 3}, p.notes || ""), "full"),
  h("div", {class: "form-foot"}, h("span", {class: "caption"}, "agent 估算饮食时会参考这些信息。"), h("button", {class: "ys-btn ys-btn--primary", type: "submit"}, "保存")));

  // as the server checks it (str.isalnum, - and _); the hyphen is escaped because browsers compile
  // pattern with the v flag, where a bare "-" in a class is a syntax error
  const tokenName = textInput("token_name", "", {placeholder: "例如 claude-phone", pattern: "[\\p{L}\\p{N}_\\-]+", title: "只能用字母、数字、- 和 _", "aria-label": "agent 名称",
    autocapitalize: "off", autocomplete: "off", spellcheck: "false"});
  const tokenScope = h("select", {"aria-label": "权限"}, Object.entries(SCOPES).map(([v, l]) => h("option", {value: v, selected: v === "write"}, l)));
  const tokenList = h("ul", {class: "ys-list token-list"}, tokens.tokens.slice().reverse().map(t => h("li", null, h("div", {class: "ys-row"},
    h("span", {class: "ys-row-text"}, h("span", {class: "ys-row-title"}, t.name),
      h("span", {class: "ys-row-sub"}, factRow([SCOPES[t.scope], `最近使用 ${t.last_used_at ? ago(t.last_used_at) : "从未"}`]))),
    h("span", {class: "ys-row-trail"}, h("span", null, t.revoked_at ? "已吊销" : "有效"),
      t.revoked_at ? null : button("吊销", async () => {
        if (!confirm(`吊销“${t.name}”？用它的 agent 会立刻失去访问权限。`)) return;
        await guarded(() => api("/tokens/" + encodeURIComponent(t.name), {method: "DELETE"}));
        toast("已吊销", "ok"); rerender();
      }, "ys-btn ys-btn--sm ys-btn--danger"))))));
  const external = (href, text) => h("a", {class: "ys-link", href, target: "_blank", rel: "noopener"}, text, ysIcon("external"));
  const agents = card("接入的 agent", null, h("div", {class: "card-stack"},
    h("p", {class: "body-text muted"}, "每个 agent 用自己的 token，写下的每条记录都能看出是谁写的。把 API 地址和 token 交给它；不支持 skill 的 agent 让它先读说明书。"),
    h("div", {class: "secret"}, h("code", null, AGENT_API), button("复制地址", () => copy(AGENT_API), "ys-btn ys-btn--sm")),
    h("p", {class: "link-row"}, external(AGENT_API + "/manual", "agent 说明书"), external(REPO + "/tree/main/skills/lifeos", "skill（GitHub）"))),
    tokenList,
    h("form", {class: "list-form", onsubmit: async e => {
      e.preventDefault();
      const name = tokenName.value.trim();
      if (!name) return;
      const res = await guarded(() => api("/tokens", {method: "POST", body: {name, scope: tokenScope.value}}));
      showToken(res); tokenName.value = ""; rerender();
    }}, tokenName, h("span", {class: "ys-select"}, tokenScope, ysIcon("chevron-down")), h("button", {class: "ys-btn", type: "submit"}, "生成 token")));

  const KIND = {apple_health: "健康导出文件", health_auto_export: "Health Auto Export"};
  const STATUS = {done: "完成", failed: "失败", running: "进行中", queued: "排队中"};
  const importRows = imports.imports.map(j => {
    const st = j.stats || {};
    const what = [st.samples_added != null && `${fmt(st.samples_added)} 个新数据点`, st.samples_updated && `${fmt(st.samples_updated)} 个更新`,
      st.workouts && `${st.workouts} 次运动`, st.activity_days && `${st.activity_days} 天圆环`].filter(Boolean).join("，");
    const word = STATUS[j.status] || j.status;
    // A finished import stays plain text; one still running keeps its blue dot; only a failed one gets a red pill.
    const status = j.status === "failed" ? h("span", {class: "ys-pill ys-pill--bad"}, word)
      : j.status === "running" || j.status === "queued" ? h("span", {class: "ys-status ys-status--info"}, word) : h("span", null, word);
    return h("li", null, h("div", {class: "ys-row ys-row--top"},
      h("span", {class: "ys-row-text"}, h("span", {class: "ys-row-title"}, KIND[j.kind] || j.kind),
        h("span", {class: "ys-row-sub"}, factRow([ago(j.created_at), j.error ? {text: j.error, cls: "bad", wrap: true} : {text: what || "—", wrap: true}]))),
      h("span", {class: "ys-row-trail"}, status)));
  });
  const fileInput = h("input", {type: "file", accept: ".zip,.xml", "aria-label": "选择导出文件"});
  const uploadState = h("span", {class: "upload-state", role: "status"});
  const uploadBtn = button("上传并导入", async () => {
    const file = fileInput.files[0];
    if (!file) { toast("先选择导出的 zip 文件", "bad"); return; }
    if (BASE && file.size > 100 * 1024 * 1024) { toast("超过 100 MB，请用 scp 传到小主机后运行 lifeos import apple-health", "bad"); return; }
    uploadBtn.disabled = true; uploadState.textContent = "上传中…";
    try {
      const job = await api("/imports/apple-health", {method: "POST", body: file, raw: true, type: file.name.endsWith(".xml") ? "application/xml" : "application/zip"});
      uploadState.textContent = "导入中…";
      for (;;) {
        await new Promise(r => setTimeout(r, 2000));
        const j = await api("/imports/" + job.import_id);
        if (j.status === "done") { toast(`导入完成：${fmt(j.stats.samples_added)} 个新数据点，${j.stats.workouts} 次运动`, "ok"); break; }
        if (j.status === "failed") { toast("导入失败：" + j.error, "bad"); break; }
        uploadState.textContent = `导入中… 已读 ${fmt(j.stats.samples_read || 0)} 个数据点`;
      }
      rerender();
    } catch (err) { toastError(err); } finally { uploadBtn.disabled = false; uploadState.textContent = ""; }
  }, "ys-btn ys-btn--sm");
  const importCard = card("Apple 健康", null, h("div", {class: "card-stack"},
    h("p", {class: "body-text muted"}, "手机上的 Health Auto Export 会自动推送；以前的历史可在“健康” App → 头像 → 导出所有健康数据，再上传这里。超过 100 MB 的文件请用 scp 传到小主机后运行 lifeos import apple-health。"),
    h("div", {class: "upload"}, fileInput, uploadBtn, uploadState)),
    importRows.length ? h("ul", {class: "ys-list import-list"}, importRows) : h("p", {class: "ys-empty ys-empty--compact"}, "还没有导入过。"));

  const exportCard = card("导出", null, h("div", {class: "card-stack"},
    h("p", {class: "body-text muted"}, "所有数据都存在小主机的 /var/lib/lifeos，每晚自动备份。这里可以下载一份带走。"),
    h("div", {class: "actions"}, button([ysIcon("download"), "全部记录（JSON）"], () => download("/export", `lifeos-${state.today}.json`), "ys-btn ys-btn--sm"),
      button([ysIcon("download"), "全部数据点（NDJSON）"], () => download("/export/samples.ndjson", `lifeos-samples-${state.today}.ndjson`), "ys-btn ys-btn--sm"))));

  const changeCard = card("最近变更", null, changes.changes.length ? h("ol", {class: "history"}, changes.changes.map(c =>
    h("li", null, h("time", null, ago(c.at)), c.entity === "import" ? h("span", null, `${c.actor} 导入了 Apple 健康数据`) : h("span", null, `${c.actor} ${ACTIONS[c.action] || c.action} `,
      c.entity === "record" ? h("button", {class: "ys-link", type: "button", onclick: () => openRecord(c.entity_id)}, c.after?.title || c.before?.title || "记录")
        : h("span", {class: "muted"}, ENTITY[c.entity] || c.entity, c.entity === "food" ? " " + (c.after?.name || c.before?.name || "") : "")))))
    : h("p", {class: "ys-empty ys-empty--compact"}, "还没有变更。"));
  changeCard.classList.add("changes");
  return [h("div", {class: "ys-cols settings-cols"}, h("div", {class: "ys-stack"}, card("个人资料与目标", null, form)),
    h("div", {class: "ys-stack"}, agents, importCard, exportCard, changeCard))];
}
const ENTITY = {profile: "个人资料", food: "食物", sample: "数据点", category: "类别", token: "token", import: "导入", metric: "指标", attachment: "附件"};

function showToken(res) {
  $("tokenBody").replaceChildren(
    h("p", {class: "token-who"}, `“${res.name}”（${SCOPES[res.scope]}）`),
    h("div", {class: "ys-field"}, h("span", {class: "ys-field-label"}, "LIFEOS_TOKEN"), h("div", {class: "secret"}, h("code", null, res.token), button("复制", () => copy(res.token), "ys-btn ys-btn--sm"))),
    h("div", {class: "ys-field"}, h("span", {class: "ys-field-label"}, "LIFEOS_URL"), h("div", {class: "secret"}, h("code", null, AGENT_API), button("复制", () => copy(AGENT_API), "ys-btn ys-btn--sm"))),
    h("p", {class: "caption"}, "把这两个值交给 agent。请求时带上 Authorization: Bearer 加 token。"));
  openModal($("tokenDialog"));
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("已复制", "ok"); } catch { toast("复制失败，请手动选择", "bad"); }
}

// --- quick add ---------------------------------------------------------------------------------

const ADD_KINDS = [["meal", "饮食"], ["weight", "体重"], ["water", "饮水"], ["smoking", "抽烟"], ["mood", "心情"], ["journal", "日记"]];

function openAdd() {
  $("addError").hidden = true;
  $("addKinds").replaceChildren(...ADD_KINDS.map(([k, l]) => h("button", {type: "button", role: "tab", "aria-selected": String(state.addKind === k),
    onclick: () => { state.addKind = k; openAdd(); }}, l)));
  const when = field("时间", h("input", {class: "ys-input", type: "datetime-local", name: "start", value: nowLocal(), required: true}));
  const number = attrs => h("input", {class: "ys-input", type: "number", inputmode: attrs.step === "0.1" ? "decimal" : "numeric", required: true, ...attrs});
  const fields = {
    meal: () => [field("餐次", selectBox({name: "meal_type"}, [h("option", {value: ""}, "按时间自动"), Object.entries(MEAL).map(([v, l]) => h("option", {value: v}, l))])), when,
      h("div", {class: "full item-rows", id: "itemRows"}, itemRow()),
      h("div", {class: "full"}, button([ysIcon("plus"), "再加一项"], () => $("itemRows").append(itemRow()), "ys-link")),
      field("备注", textArea({name: "note", rows: 2}), "full")],
    weight: () => [field("体重（kg）", number({name: "value", step: "0.1", min: 20, max: 300})), when],
    water: () => [field("饮水（ml）", number({name: "value", step: "1", min: 1, value: 250})), when],
    smoking: () => [field("根数", number({name: "count", min: 1, max: 100, step: 1, value: 1})), when,
      field("场景（可选）", textInput("trigger", "", {placeholder: "饭后、压力大"}), "full"), field("备注", textArea({name: "note", rows: 2}), "full")],
    mood: () => [field("心情（1 很差 – 10 很好）", number({name: "score", min: 1, max: 10, step: 1, value: 6})), when,
      field("情绪（顿号分隔）", textInput("emotions", "", {placeholder: "平静、有点累"}), "full"), field("备注", textArea({name: "note", rows: 2}), "full")],
    journal: () => [field("标题（可选）", textInput("title", ""), "full"), when, field("内容", textArea({name: "text", rows: 6, required: true}), "full")],
  };
  fill($("addFields"), fields[state.addKind]());
  openModal($("addDialog"));
}
function itemRow() {
  const row = h("div", {class: "item-row"},
    field("食物", textInput("item_name", "", {required: true, placeholder: "牛肉面"})), field("份量", textInput("item_amount", "", {placeholder: "1 碗"})),
    field("热量", numInput("item_kcal", "")), field("蛋白质", numInput("item_protein_g", "")), field("脂肪", numInput("item_fat_g", "")),
    field("碳水", numInput("item_carbs_g", "")),
    button(ysIcon("close"), () => { if ($("itemRows").children.length > 1) row.remove(); }, "ys-btn ys-btn--icon ys-btn--quiet", {"aria-label": "删除这一项"}));
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
    } else if (state.addKind === "smoking") {
      await api("/records", {method: "POST", body: {category: "smoking", start, source: "manual", note: v("note") || null,
        data: {count: Number(v("count")), ...(v("trigger") ? {trigger: v("trigger")} : {})}}});
    } else if (state.addKind === "mood") {
      const emotions = v("emotions").split(/[、,，\s]+/).filter(Boolean);
      await api("/records", {method: "POST", body: {category: "mood", start, source: "manual", note: v("note") || null,
        data: {score: Number(v("score")), ...(emotions.length ? {emotions} : {})}}});
    } else {
      await api("/records", {method: "POST", body: {category: "journal", start, source: "manual", title: v("title") || null, data: {text: v("text")}}});
    }
    $("addDialog").close();
    toast("已记下", "ok");
    rerender();
  } catch (err) {
    showError($("addError"), err);
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
  $("subtitle").replaceChildren(factRow([dayLabel(ctx.today), last ? `Apple 健康 ${ago(last.created_at)}同步` : "还没有导入 Apple 健康数据"]));
  const warn = [];
  if (last?.status === "failed") warn.push(["critical", `最近一次导入失败：${last.error}`]);
  if (!ctx.profile.goals?.kcal) warn.push(["warning", "还没有设置每日目标。到“设置”里填上热量和营养目标，今天的页面会显示还剩多少。"]);
  issues(warn);
}

// A tab opened before a deploy, or on an earlier day, reloads itself when you come back to it.
// While a dialog is open or something is being typed it only says so, to keep what is there.
const VERSION = document.querySelector('meta[name="lifeos-version"]')?.content;
let lastCheck = Date.now();
async function checkForUpdate() {
  if (document.visibilityState !== "visible" || Date.now() - lastCheck < 30000) return;
  lastCheck = Date.now();
  let current = null;
  try {
    const res = await fetch(BASE + "/version", {cache: "no-store", credentials: "same-origin"});
    if (res.ok) current = (await res.json()).web;
  } catch { return; }
  const newVersion = current && VERSION && current !== VERSION;
  const newDay = state.today && nowLocal().slice(0, 10) !== state.today;
  if (!newVersion && !newDay) return;
  const busy = [...document.querySelectorAll("dialog")].some(d => d.open) || document.activeElement?.matches("input, textarea, select");
  if (!busy) location.reload();
  else issues([["warning", newVersion ? "页面有新版本，忙完刷新一下就能看到。" : "日期已经变了，忙完刷新一下就能看到今天的。"]]);
}

async function boot() {
  if (!BASE) $("homeLink").hidden = true;
  document.addEventListener("visibilitychange", checkForUpdate);
  window.addEventListener("pageshow", e => { if (e.persisted) { lastCheck = 0; checkForUpdate(); } });
  for (const dlg of document.querySelectorAll("dialog")) {
    dlg.querySelector(".close-dialog")?.addEventListener("click", () => dlg.close());
  }
  $("recordDialog").addEventListener("close", () => { if (location.hash.startsWith("#/record/")) history.replaceState(null, "", "#/records"); });
  $("addButton").addEventListener("click", openAdd);
  $("addForm").addEventListener("submit", submitAdd);
  $("toast").addEventListener("click", hideToast);
  try {
    await loadContext();
  } catch (err) {
    $("subtitle").textContent = "";
    if ($("bootSkeleton")) $("bootSkeleton").hidden = true;
    const [problem, fix] = explain(err);
    issues([["critical", err.status === 401 || err.status === 403
      ? (BASE ? "没有通过登录，请从 yishuapp.com 重新打开。" : "本机打开需要 token：地址后面加 #token=lifeos_…")
      : [h("span", {class: "ys-alert-title"}, problem + "。"), fix, errorDetail(err)]]]);
    return;
  }
  window.addEventListener("hashchange", route);
  route();
}
boot();
