// AIニュースビューア：news.json を表示。キーワード検索・お気に入り（localStorage）対応。
const WD = ["日", "月", "火", "水", "木", "金", "土"];
const FAV_KEY = "ai-news-favs";

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtDate(iso) {
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  if (isNaN(d)) return { full: iso, wd: "", y: "" };
  return { full: `${d.getMonth() + 1}月${d.getDate()}日`, wd: WD[d.getDay()] + "曜日", y: d.getFullYear() };
}

const extLink = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>`;
const scope = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l18-5-7 16-3-7-8-4z"/></svg>`;

let DATA = { days: [] };
let query = "";
let favOnly = false;
let favs = loadFavs();
let selectedDate = null; // 表示中の日付（既定は最新日＝今日）

function loadFavs() {
  try { return new Set(JSON.parse(localStorage.getItem(FAV_KEY) || "[]")); }
  catch { return new Set(); }
}
function saveFavs() {
  try { localStorage.setItem(FAV_KEY, JSON.stringify([...favs])); } catch {}
}
function favKey(date, it) { return it.url || `${date}::${it.title}`; }

function matches(it) {
  if (!query) return true;
  const hay = `${it.title || ""} ${it.summary || ""} ${it.source || ""}`.toLowerCase();
  return query.toLowerCase().split(/\s+/).filter(Boolean).every((t) => hay.includes(t));
}

function itemCard(date, it) {
  const key = favKey(date, it);
  const on = favs.has(key);
  const link = it.url
    ? `<a class="read" href="${esc(it.url)}" target="_blank" rel="noopener">元記事を読む ${extLink}</a>`
    : "";
  return `
    <article class="card">
      <div class="meta">
        <span class="src">${esc(it.source || "ニュース")}</span>
        <span class="right">
          <span class="date">${esc(it.publishedAt || "")}</span>
          <button class="fav" data-key="${esc(key)}" aria-pressed="${on}" aria-label="お気に入り" title="お気に入り">${on ? "★" : "☆"}</button>
        </span>
      </div>
      <h3>${esc(it.title || "")}</h3>
      ${it.summary ? `<p>${esc(it.summary)}</p>` : ""}
      ${link}
    </article>`;
}

function dayBlock(day, items, filtering) {
  const { full, wd } = fmtDate(day.date);
  const cards = items.map((it) => itemCard(day.date, it)).join("");
  const outlook = (!filtering && day.outlook)
    ? `<div class="outlook"><div class="lab">${scope} 今後の展望</div><p>${esc(day.outlook)}</p></div>`
    : "";
  const note = (!filtering && day.note) ? `<div class="note">${esc(day.note)}</div>` : "";
  const sample = day.sample ? `<span class="badge">サンプル</span>` : "";
  return `
    <section class="day" id="day-${esc(day.date)}">
      <div class="day-head"><span class="d">${full}</span><span class="wd">${wd}</span>${sample}</div>
      ${cards}${outlook}${note}
    </section>`;
}

function applyFilters() {
  const feed = document.getElementById("feed");
  const sel = document.getElementById("jumpSel");
  const jump = document.querySelector(".jump");
  const filtering = !!query || favOnly;
  const days = (DATA.days || []).slice().sort((a, b) => (a.date < b.date ? 1 : -1));

  // 日付ドロップダウンは常に全日分を用意（既定は最新日＝今日）
  sel.innerHTML = days.map((day) => {
    const f = fmtDate(day.date);
    return `<option value="${esc(day.date)}">${f.y}年 ${f.full}（${f.wd.replace("曜日", "")}）</option>`;
  }).join("");

  // 検索・お気に入り中は、日付をまたいで全該当記事を縦に並べて表示
  if (filtering) {
    const visible = [];
    for (const day of days) {
      let items = (day.items || []).filter(matches);
      if (favOnly) items = items.filter((it) => favs.has(favKey(day.date, it)));
      if (items.length) visible.push({ day, items });
    }
    if (!visible.length) {
      const msg = favOnly ? "お気に入りはまだありません。★を押して追加できます。"
        : `「${esc(query)}」に一致するニュースはありません。`;
      feed.innerHTML = `<div class="empty"><div class="big">${favOnly ? "★" : "🔍"}</div>${msg}</div>`;
    } else {
      feed.innerHTML = visible.map((v) => dayBlock(v.day, v.items, true)).join("");
    }
    jump.style.display = "none";
    const total = visible.reduce((n, v) => n + v.items.length, 0);
    document.getElementById("foot").textContent = `${total}件表示中`;
    return;
  }

  // 通常時：選択中の1日だけをパッと表示
  if (!days.length) {
    feed.innerHTML = `<div class="empty"><div class="big">📰</div>まだニュースがありません。<br>毎朝7時に最初の記事が入ります。</div>`;
    jump.style.display = "none";
    document.getElementById("foot").textContent = "";
    return;
  }
  // 選択中の日付が未設定/無効なら最新日（今日）にフォールバック
  if (!selectedDate || !days.some((d) => d.date === selectedDate)) {
    selectedDate = days[0].date;
  }
  sel.value = selectedDate;
  const day = days.find((d) => d.date === selectedDate);
  feed.innerHTML = dayBlock(day, day.items || [], false);
  jump.style.display = days.length > 1 ? "" : "none";
  document.getElementById("foot").textContent =
    `${days.length}日分・The Verge / TechCrunch / VentureBeat / MIT Tech Review / Bloomberg`;
}

function render(data) {
  DATA = data;
  if (data.updatedAt) {
    const u = new Date(data.updatedAt);
    if (!isNaN(u)) document.getElementById("updated").textContent =
      `更新 ${u.getMonth() + 1}/${u.getDate()} ${String(u.getHours()).padStart(2, "0")}:${String(u.getMinutes()).padStart(2, "0")}`;
  }
  applyFilters();
}

// イベント
document.getElementById("feed").addEventListener("click", (e) => {
  const btn = e.target.closest(".fav");
  if (!btn) return;
  const key = btn.dataset.key;
  if (favs.has(key)) favs.delete(key); else favs.add(key);
  saveFavs();
  btn.setAttribute("aria-pressed", favs.has(key));
  btn.textContent = favs.has(key) ? "★" : "☆";
  if (favOnly) applyFilters();
});

const searchEl = document.getElementById("search");
const clearEl = document.getElementById("clearSearch");
searchEl.addEventListener("input", () => {
  query = searchEl.value.trim();
  clearEl.hidden = !query;
  applyFilters();
});
clearEl.addEventListener("click", () => {
  searchEl.value = ""; query = ""; clearEl.hidden = true; searchEl.focus(); applyFilters();
});

const favToggle = document.getElementById("favToggle");
favToggle.addEventListener("click", () => {
  favOnly = !favOnly;
  favToggle.setAttribute("aria-pressed", favOnly);
  favToggle.textContent = (favOnly ? "★" : "☆") + " お気に入り";
  applyFilters();
});

document.getElementById("jumpSel").addEventListener("change", (e) => {
  selectedDate = e.target.value;
  applyFilters();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

fetch("news.json?" + Date.now())
  .then((r) => r.json())
  .then(render)
  .catch(() => {
    document.getElementById("feed").innerHTML =
      `<div class="empty"><div class="big">⚠️</div>ニュースを読み込めませんでした。</div>`;
  });

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}
