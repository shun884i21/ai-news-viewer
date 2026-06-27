// AIニュースビューア：news.json を読んで「日付ごとの5本＋今後の展望」を表示する
const WD = ["日", "月", "火", "水", "木", "金", "土"];

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtDate(iso) {
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  if (isNaN(d)) return { full: iso, wd: "" };
  return { full: `${d.getMonth() + 1}月${d.getDate()}日`, wd: WD[d.getDay()] + "曜日", y: d.getFullYear() };
}

const extLink = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>`;
const scope = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l18-5-7 16-3-7-8-4z"/></svg>`;

function itemCard(it) {
  const link = it.url
    ? `<a class="read" href="${esc(it.url)}" target="_blank" rel="noopener">元記事を読む ${extLink}</a>`
    : "";
  return `
    <article class="card">
      <div class="meta">
        <span class="src">${esc(it.source || "ニュース")}</span>
        <span class="date">${esc(it.publishedAt || "")}</span>
      </div>
      <h3>${esc(it.title || "")}</h3>
      ${it.summary ? `<p>${esc(it.summary)}</p>` : ""}
      ${link}
    </article>`;
}

function dayBlock(day) {
  const { full, wd } = fmtDate(day.date);
  const items = (day.items || []).map(itemCard).join("");
  const outlook = day.outlook
    ? `<div class="outlook"><div class="lab">${scope} 今後の展望</div><p>${esc(day.outlook)}</p></div>`
    : "";
  const note = day.note ? `<div class="note">${esc(day.note)}</div>` : "";
  const sample = day.sample ? `<span class="badge">サンプル</span>` : "";
  return `
    <section class="day" id="day-${esc(day.date)}">
      <div class="day-head"><span class="d">${full}</span><span class="wd">${wd}</span>${sample}</div>
      ${items}
      ${outlook}
      ${note}
    </section>`;
}

function render(data) {
  const feed = document.getElementById("feed");
  const sel = document.getElementById("jumpSel");
  const days = (data.days || []).slice().sort((a, b) => (a.date < b.date ? 1 : -1));

  if (data.updatedAt) {
    const u = new Date(data.updatedAt);
    if (!isNaN(u)) document.getElementById("updated").textContent =
      `更新 ${u.getMonth() + 1}/${u.getDate()} ${String(u.getHours()).padStart(2, "0")}:${String(u.getMinutes()).padStart(2, "0")}`;
  }

  if (!days.length) {
    feed.innerHTML = `<div class="empty"><div class="big">📰</div>まだニュースがありません。<br>毎朝7時に最初の記事が入ります。</div>`;
    sel.style.display = "none";
    return;
  }

  feed.innerHTML = days.map(dayBlock).join("");
  sel.innerHTML = days.map((d) => {
    const f = fmtDate(d.date);
    return `<option value="day-${esc(d.date)}">${f.y}年 ${f.full}（${f.wd.replace("曜日", "")}）</option>`;
  }).join("");
  sel.onchange = () => {
    const el = document.getElementById(sel.value);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  document.getElementById("foot").textContent =
    `${days.length}日分・The Verge / TechCrunch / VentureBeat / MIT Tech Review / Bloomberg`;
}

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
