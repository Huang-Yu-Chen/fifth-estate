"use strict";
// 第五權 標註站: the page invited labelers use. Everything it shows arrives from the server already
// blinded (no outlet, URL, date, model scores or other labelers' labels); this file only renders it.
// Text is always inserted as text nodes, never as HTML.

const SCORES = [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5];
const FIELDS = [
  { id: "scope", label: "範圍", kind: "choice", help: "scope", options: [["國內政治", "國內政治"], ["外交", "外交"], ["兩岸", "兩岸"], ["不在範圍", "不在範圍"]] },
  { id: "lean", label: "綠藍傾向", kind: "scale", help: "lean", ends: ["強烈支持民進黨", "無明顯傾向", "強烈支持國民黨"] },
  { id: "china_relevance", label: "與中國相關程度", kind: "choice", help: "china", options: [[0, "0 無關"], [1, "1 有提及，非主軸"], [2, "2 是主軸"]] },
  { id: "china", label: "抗中友中", kind: "scale", ends: ["強烈抗中", "中立", "強烈友中"] },
  { id: "tone", label: "語氣", kind: "scale", help: "tone", ends: ["非常負面", "中性", "非常正面"] },
  { id: "news_type", label: "新聞類型", kind: "choice", help: "news_type", options: [["新聞報導", "新聞報導"], ["新聞分析", "新聞分析"], ["評論", "評論"]] },
];
const NUMERIC = new Set(["lean", "china_relevance", "china", "tone"]);

const app = document.getElementById("app");
const dialog = document.getElementById("codebook");
const state = { me: null, set: null, codebooks: {}, dirty: false, seq: 0 }; // seq: the latest navigation; older, slower ones don't render

class AuthError extends Error {}

async function api(path, { method = "GET", body } = {}) {
  const opts = { method, credentials: "same-origin", cache: "no-store", headers: {} };
  if (method !== "GET") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body ?? {});
  }
  const res = await fetch(path, opts);
  const data = (res.headers.get("content-type") || "").includes("application/json") ? await res.json() : null;
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `伺服器回應 ${res.status}`);
  return data;
}

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c);
  return el;
}

const enc = encodeURIComponent;
const signed = (v) => (v > 0 ? `+${v}` : v < 0 ? `−${-v}` : "0");
const when = (iso) => new Date(iso).toLocaleString("zh-TW", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const blocks = (bs) => bs.map((b) => (b.type === "ul" ? h("ul", {}, b.items.map((t) => h("li", {}, t))) : h("p", {}, b.text)));

function render(...nodes) {
  app.replaceChildren(...nodes.flat());
  window.scrollTo(0, 0);
}
const loading = () => h("p", { class: "muted center" }, "載入中…");

let toastTimer;
function toast(text) {
  const t = document.getElementById("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 1800);
}

// Time the article is actually on screen (paused while the tab is hidden).
const timer = {
  total: 0,
  since: null,
  start() { this.total = 0; this.since = document.hidden ? null : performance.now(); },
  pause() { if (this.since != null) { this.total += performance.now() - this.since; this.since = null; } },
  resume() { if (this.since == null) this.since = performance.now(); },
  seconds() { this.pause(); const s = Math.round(this.total / 1000); this.resume(); return s; },
};
document.addEventListener("visibilitychange", () => (document.hidden ? timer.pause() : timer.resume()));

function setNav(me) {
  document.getElementById("nav").hidden = !me;
  document.getElementById("who").textContent = me ? me.name : "";
}

function gate(message) {
  state.me = null;
  setNav(null);
  render(h("section", { class: "card narrow" },
    h("h1", {}, "第五權 標註站"),
    h("p", {}, message || "這裡只開放受邀的標註者。請用管理者私下寄給你的個人連結開啟。")));
}

async function join(token) {
  history.replaceState(null, "", location.pathname); // drop the token from the address bar at once
  try {
    await api("/api/login", { method: "POST", body: { token } });
    location.hash = "#/";
  } catch {
    gate("這個連結無效或已停用，請向管理者索取新的連結。");
  }
}

async function me() {
  state.me = await api("/api/me");
  setNav(state.me);
  return state.me;
}

async function codebook(setName) {
  state.codebooks[setName] ??= await api(`/api/sets/${enc(setName)}/codebook`);
  return state.codebooks[setName];
}

async function openCodebook() {
  const name = state.set || state.me?.sets?.[0]?.set;
  const body = document.getElementById("codebook-body");
  if (!name) body.replaceChildren(h("p", { class: "muted" }, "目前沒有開放的標註組。"));
  else {
    const cb = await codebook(name);
    body.replaceChildren(h("p", { class: "note" }, cb.note), ...cb.sections.flatMap((s) => [h("h3", {}, s.title), ...blocks(s.blocks)]));
  }
  dialog.showModal();
}

async function home() {
  const id = state.seq;
  state.set = null;
  const m = await me();
  if (id !== state.seq) return;
  const cards = m.sets.map((s) => {
    const started = s.done + s.skipped > 0;
    const design = s.needed == null ? `共 ${s.items} 篇，每位標註者都標全部。`
      : `共 ${s.items} 篇，每篇由 ${s.coders_per_article} 位標註者各自評分；` + (s.needed > 0 ? `全組還需要 ${s.needed} 份標註。` : "全組已經標完。");
    return h("section", { class: "card" },
      h("h2", {}, s.title),
      h("p", { class: "stats" }, `你已標 ${s.done} 篇` + (s.skipped ? `、跳過 ${s.skipped} 篇` : "")),
      h("p", { class: "muted" }, design),
      h("div", { class: "actions" },
        h("a", { class: "button primary", href: `#/s/${enc(s.set)}` }, started ? "繼續標註" : "開始標註"),
        started && h("a", { class: "button", href: `#/s/${enc(s.set)}/mine` }, "我的標註")));
  });
  render(
    h("section", { class: "card intro" },
      h("h1", {}, `${m.name}，你好`),
      h("p", {}, "謝謝你協助檢查第五權的 AI 標註。請依照編碼手冊，為每篇新聞評分。"),
      h("ul", {},
        h("li", {}, "這是盲測：你看不到媒體名稱、網址、日期，也看不到 AI 或其他標註者的評分。請只根據文本判斷。"),
        h("li", {}, "請不要上網搜尋文章出處，也不要和其他標註者討論個別文章。"),
        h("li", {}, "第一次標註前，請先讀一遍右上角的「編碼手冊」；每一題下方的「說明」也可以隨時打開。"),
        h("li", {}, "拿不準時選最接近的分數。文章殘缺、無法判斷時才按「跳過」。"))),
    cards.length ? cards : h("section", { class: "card muted" }, "目前沒有開放的標註組。有新的文章時，管理者會通知你。"));
}

async function labelNext(setName) {
  const id = state.seq;
  render(loading());
  const [cb, data, m] = await Promise.all([codebook(setName), api(`/api/sets/${enc(setName)}/next`, { method: "POST" }), me()]);
  if (id !== state.seq) return;
  const s = m.sets.find((x) => x.set === setName);
  if (!data.item) {
    return render(h("section", { class: "card narrow" },
      h("h1", {}, "目前沒有需要你標的文章了"),
      h("p", {}, "謝謝！" + (s?.needed ? `這一組還需要 ${s.needed} 份標註，會由其他標註者完成。` : "")),
      h("div", { class: "actions" },
        h("a", { class: "button primary", href: "#/" }, "回首頁"),
        h("a", { class: "button", href: `#/s/${enc(setName)}/mine` }, "我的標註"))));
  }
  work(setName, data.item, null, cb, s);
}

async function edit(setName, key) {
  const id = state.seq;
  render(loading());
  const [cb, data] = await Promise.all([codebook(setName), api(`/api/sets/${enc(setName)}/items/${enc(key)}`)]);
  if (id !== state.seq) return;
  work(setName, data.item, data.label || {}, cb, null);
}

async function mine(setName) {
  const id = state.seq;
  render(loading());
  const data = await api(`/api/sets/${enc(setName)}/mine`);
  if (id !== state.seq) return;
  render(h("section", { class: "card" },
    h("div", { class: "head" },
      h("h1", {}, "我的標註"),
      h("a", { class: "button primary", href: `#/s/${enc(setName)}` }, "繼續標註")),
    data.labels.length
      ? [h("p", { class: "muted" }, "點一篇可以修改你的評分。"),
         h("ul", { class: "mine" }, data.labels.map((r) => h("li", {},
           h("a", { href: `#/s/${enc(setName)}/edit/${enc(r.key)}` },
             h("span", { class: "t" }, r.title),
             h("span", { class: "meta" }, (r.skipped ? "已跳過 · " : "") + when(r.labeled_at))))))]
      : h("p", { class: "muted" }, "你還沒有標註這一組的文章。")));
}

function field(f, cb, label) {
  const inputs = [];
  const options = f.kind === "scale" ? SCORES.map((v) => [v, signed(v)]) : f.options;
  const row = h("div", { class: f.kind });
  for (const [v, text] of options) {
    const input = h("input", { type: "radio", name: f.id, value: String(v), "aria-label": `${f.label} ${text}` });
    input.checked = label != null && label[f.id] === v;
    inputs.push(input);
    row.append(h("label", { class: "opt" }, input, h("span", { "aria-hidden": "true" }, text)));
  }
  const section = f.help && cb.sections.find((s) => s.id === f.help);
  const note = h("p", { class: "field-note" });
  const error = h("p", { class: "field-error", hidden: true }, "請選擇一項");
  const el = h("fieldset", { class: "field" },
    h("legend", {}, f.label),
    row,
    f.kind === "scale" && h("div", { class: "ends", "aria-hidden": "true" }, f.ends.map((t) => h("span", {}, t))),
    note,
    error,
    section && h("details", { class: "help" }, h("summary", {}, "說明"), blocks(section.blocks)));
  return { el, inputs, note, error };
}

function value(form, name) {
  const el = form.querySelector(`input[name="${name}"]:checked`);
  if (!el) return null;
  return NUMERIC.has(name) ? Number(el.value) : el.value;
}

function work(setName, item, label, cb, progress) {
  const editing = label != null;
  const groups = {};
  const form = h("form", { class: "card form", novalidate: true });
  if (progress) {
    form.append(h("p", { class: "progress" }, `${progress.title} · 你已標 `, h("b", {}, String(progress.done)), " 篇"));
  } else if (editing) {
    form.append(h("p", { class: "progress" }, label.skipped ? "修改標註（這篇先前被你跳過）" : "修改先前的標註"));
  }
  for (const f of FIELDS) {
    groups[f.id] = field(f, cb, label?.skipped ? null : label);
    form.append(groups[f.id].el);
  }
  const note = h("textarea", { id: "note", rows: 2, maxlength: 2000, placeholder: "選填：判斷的理由、拿不準的地方" });
  note.value = label?.note || "";
  const formError = h("p", { class: "form-error", role: "alert", hidden: true });
  const save = h("button", { type: "submit", class: "button primary" }, editing ? "儲存修改" : "儲存，下一篇");
  const skipReason = h("textarea", { id: "skip-reason", rows: 2, maxlength: 500, placeholder: "例如：文章內容殘缺" });
  const skipBox = h("div", { class: "skip", hidden: true },
    h("label", { class: "text", for: "skip-reason" }, "跳過的原因（選填）"),
    skipReason,
    h("div", { class: "actions" },
      h("button", { type: "button", class: "button danger", onclick: () => submit({ skipped: true, skip_reason: skipReason.value.trim(), note: note.value.trim() }) }, "確定跳過"),
      h("button", { type: "button", class: "button quiet", onclick: () => (skipBox.hidden = true) }, "取消")));
  form.append(
    h("div", { class: "field" }, h("label", { class: "text", for: "note" }, "備註"), note),
    h("div", { class: "actions" },
      save,
      h("button", { type: "button", class: "button", onclick: () => { skipBox.hidden = false; skipReason.focus(); } }, editing ? "改成跳過" : "跳過這篇"),
      editing && h("a", { class: "button quiet", href: `#/s/${enc(setName)}/mine` }, "取消")),
    skipBox,
    formError);

  // 抗中友中 is only asked when the article has something to do with China.
  const syncChina = () => {
    const off = value(form, "china_relevance") === 0;
    groups.china.el.classList.toggle("off", off);
    for (const input of groups.china.inputs) {
      input.disabled = off;
      if (off) input.checked = false;
    }
    groups.china.note.textContent = off ? "與中國無關，免填。" : "";
    if (off) {
      groups.china.error.hidden = true;
      groups.china.el.classList.remove("invalid");
    }
  };

  async function submit(body) {
    for (const b of form.querySelectorAll("button")) b.disabled = true;
    formError.hidden = true;
    try {
      await api(`/api/sets/${enc(setName)}/labels/${enc(item.key)}`, { method: "PUT", body: { ...body, seconds: timer.seconds() } });
      state.dirty = false;
      toast(body.skipped ? "已跳過" : editing ? "已修改" : "已儲存");
      if (editing) location.hash = `#/s/${enc(setName)}/mine`;
      else await route();
    } catch (err) {
      if (err instanceof AuthError) return gate("登入已失效，請重新用你的個人連結開啟。");
      formError.textContent = `沒有存到：${err.message}。請再試一次。`;
      formError.hidden = false;
      for (const b of form.querySelectorAll("button")) b.disabled = false;
    }
  }

  form.addEventListener("change", (e) => {
    state.dirty = true;
    if (e.target.name === "china_relevance") syncChina();
    const g = groups[e.target.name];
    if (g) { g.error.hidden = true; g.el.classList.remove("invalid"); }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const body = { note: note.value.trim() };
    for (const f of FIELDS) body[f.id] = value(form, f.id);
    const missing = FIELDS.filter((f) => body[f.id] == null && !(f.id === "china" && body.china_relevance === 0));
    for (const f of FIELDS) {
      const bad = missing.includes(f);
      groups[f.id].error.hidden = !bad;
      groups[f.id].el.classList.toggle("invalid", bad);
    }
    if (missing.length) {
      groups[missing[0].id].el.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    submit(body);
  });

  const article = h("article", { class: "card article" },
    h("p", { class: "eyebrow" }, "請讀完全文再評分"),
    h("h1", {}, item.title),
    item.body.split("\n").filter((p) => p.trim()).map((p) => h("p", {}, p)));
  render(h("div", { class: "work" }, article, form));
  syncChina();
  state.dirty = false;
  timer.start();
}

async function route() {
  const id = ++state.seq;
  const hash = location.hash;
  if (hash.startsWith("#join=")) return join(decodeURIComponent(hash.slice(6)));
  const m = hash.match(/^#\/s\/([^/]+)(?:\/(mine|edit)(?:\/([^/]+))?)?$/);
  try {
    if (!m) return await home();
    state.set = decodeURIComponent(m[1]);
    if (!state.me) await me();
    if (m[2] === "mine") return await mine(state.set);
    if (m[2] === "edit" && m[3]) return await edit(state.set, decodeURIComponent(m[3]));
    return await labelNext(state.set);
  } catch (err) {
    if (id !== state.seq) return;
    if (err instanceof AuthError) return gate();
    render(h("section", { class: "card narrow" },
      h("h1", {}, "出了點問題"),
      h("p", {}, err.message),
      h("div", { class: "actions" }, h("button", { type: "button", class: "button primary", onclick: route }, "再試一次"))));
  }
}

// Leaving a half-filled article: ask first.
window.addEventListener("beforeunload", (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ""; } });
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href^='#']");
  if (!a || !state.dirty) return;
  if (confirm("這篇還沒儲存，確定要離開嗎？")) state.dirty = false;
  else e.preventDefault();
});
document.getElementById("nav-codebook").addEventListener("click", () => openCodebook().catch((err) => toast(err.message)));
document.getElementById("codebook-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
window.addEventListener("hashchange", route);
route();
