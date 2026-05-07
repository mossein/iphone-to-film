/* ═══════════════════════════════════════════════════════════════
   FILM — Spectral Emulation  ·  Client
   ═══════════════════════════════════════════════════════════════ */

const S = {
    imageId: null,
    imageSrc: null,
    stock: 'portra400',
    category: 'pro_color',
    printStock: null,
    params: {},
    stocks: {},
    categories: {},
    galleryStocks: [],
    debounce: null,
    batchFiles: [],
    batchPaths: [],
    batchStock: 'portra400',
    batchCategory: 'pro_color',
};

/* ─── Boot ───────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', async () => {
    const [stockData, printData, galData] = await Promise.all([
        fetch('/api/stocks').then(r => r.json()),
        fetch('/api/print-stocks').then(r => r.json()),
        fetch('/api/gallery-stocks').then(r => r.json()),
    ]);
    S.stocks = stockData.stocks;
    S.categories = stockData.categories;
    S.galleryStocks = galData;

    // Populate print stocks
    const pSel = qs('#print-stock');
    for (const [k, name] of Object.entries(printData)) {
        const o = document.createElement('option');
        o.value = k; o.textContent = name;
        pSel.appendChild(o);
    }

    renderCats('#cat-tabs', S.category, key => { S.category = key; renderStocks('#stock-list', S.stock, selectStock); });
    renderStocks('#stock-list', S.stock, selectStock);
    renderCats('#batch-cat-tabs', S.batchCategory, key => { S.batchCategory = key; renderStocks('#batch-stock-list', S.batchStock, selectBatchStock); });
    renderStocks('#batch-stock-list', S.batchStock, selectBatchStock);

    setupUpload();
    setupCompare();
    setupParams();
    setupViews();
    setupBatch();
    setupGalleryBatch();

    // Pull the initial stock's per-stock baseline values into the Look sliders.
    syncStockDefaults(S.stock);
});

/* ─── Helpers ────────────────────────────────────────── */
const qs = s => document.querySelector(s);
const qa = s => document.querySelectorAll(s);

/* ─── Views ──────────────────────────────────────────── */

function setupViews() {
    qa('.vs-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const v = btn.dataset.view;
            qa('.vs-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            showView(v);
        });
    });
}

function showView(name) {
    qs('#landing').style.display = 'none';
    qa('.view').forEach(v => v.style.display = 'none');
    const el = qs(`#view-${name}`);
    if (el) el.style.display = '';

    // Cancel gallery thumbnails when leaving gallery
    if (name !== 'gallery' && galleryAbort) {
        galleryAbort.abort();
        galleryAbort = null;
    }

    if (name === 'gallery' && S.imageId) loadGallery();
}

function enterApp(view = 'compare') {
    qs('#landing').style.display = 'none';
    qa('.vs-btn').forEach(b => b.classList.remove('active'));
    qs(`.vs-btn[data-view="${view}"]`).classList.add('active');
    showView(view);
}

/* ─── Upload ─────────────────────────────────────────── */

function setupUpload() {
    const drop = qs('#drop-zone');
    const inp = qs('#file-input');

    drop.addEventListener('click', () => inp.click());
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('dragover'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
    drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('dragover'); if (e.dataTransfer.files.length) doUpload(e.dataTransfer.files[0]); });
    inp.addEventListener('change', () => { if (inp.files.length) doUpload(inp.files[0]); });

    // Nav upload trigger
    const navInp = qs('#file-input');
    qs('#nav-upload-trigger').addEventListener('click', () => navInp.click());
}

async function doUpload(file) {
    const form = new FormData();
    form.append('file', file);
    const resp = await fetch('/api/upload', { method: 'POST', body: form });
    if (!resp.ok) { alert('Upload failed'); return; }
    const data = await resp.json();
    if (!data.id) { alert('Upload failed — no image ID returned'); return; }
    S.imageId = data.id;
    // Use server URL (handles HEIC conversion) instead of local blob
    S.imageSrc = data.url || `/api/original/${data.id}`;

    const origImg = qs('#img-original');
    origImg.onerror = () => {
        // Fallback: try object URL if server URL fails
        origImg.src = URL.createObjectURL(file);
    };
    origImg.src = S.imageSrc;
    enterApp('compare');
    requestPreview();
}

/* ─── Categories & Stocks ────────────────────────────── */

function renderCats(containerSel, activeCat, onSelect) {
    const el = qs(containerSel);
    el.innerHTML = '';
    for (const [key, name] of Object.entries(S.categories)) {
        const btn = document.createElement('button');
        btn.className = 'cat-tab' + (key === activeCat ? ' active' : '');
        btn.textContent = name;
        btn.addEventListener('click', () => {
            el.querySelectorAll('.cat-tab').forEach(t => t.classList.remove('active'));
            btn.classList.add('active');
            onSelect(key);
        });
        el.appendChild(btn);
    }
}

function renderStocks(containerSel, activeKey, onSelect) {
    const el = qs(containerSel);
    el.innerHTML = '';
    const cat = containerSel.includes('batch') ? S.batchCategory : S.category;
    const stocks = S.stocks[cat] || [];
    for (const s of stocks) {
        const item = document.createElement('div');
        item.className = 'stk' + (s.key === activeKey ? ' active' : '');
        item.innerHTML = `<div class="stk-name">${s.name}</div><div class="stk-desc">${s.description}</div>`;
        item.addEventListener('click', () => onSelect(s.key, item));
        el.appendChild(item);
    }
}

function selectStock(key, el) {
    S.stock = key;
    qs('#stock-list').querySelectorAll('.stk').forEach(s => s.classList.remove('active'));
    if (el) el.classList.add('active');
    // Update compare label
    const info = S.galleryStocks.find(s => s.key === key);
    if (info) qs('#cmp-stock-label').textContent = info.name.toUpperCase();

    // Re-baseline the "Look" sliders to this stock's defaults so "no
    // override" means "stock as-authored." Drop any existing pipeline-level
    // overrides — they belonged to the previous stock.
    syncStockDefaults(key);
}

async function syncStockDefaults(key) {
    try {
        const resp = await fetch(`/api/stock-defaults/${key}`);
        if (!resp.ok) { if (S.imageId) requestPreview(); return; }
        const defaults = await resp.json();
        // Clear pipeline-level overrides — only the photochemical sliders
        // (exp_comp/sat/etc.) survive a stock change.
        const photochem = new Set(['exp_comp','sat','pre_flash_neg','black_offset','white_point','tint']);
        for (const k of Object.keys(S.params)) {
            if (!photochem.has(k)) delete S.params[k];
        }
        // Reset every slider whose data-param is in the defaults dict.
        qa('.prm').forEach(prm => {
            const k = prm.dataset.param;
            if (!(k in defaults)) return;
            const slider = prm.querySelector('input[type="range"]');
            const valEl = prm.querySelector('.prm-val');
            const decimals = parseInt(slider.dataset.decimals || '2');
            const v = Number(defaults[k]);
            slider.value = v;
            slider.dataset.stockDefault = String(v);
            valEl.textContent = v.toFixed(decimals);
        });
    } catch (e) { console.error('stock-defaults sync', e); }
    if (S.imageId) requestPreview();
}

function selectBatchStock(key, el) {
    S.batchStock = key;
    qs('#batch-stock-list').querySelectorAll('.stk').forEach(s => s.classList.remove('active'));
    if (el) el.classList.add('active');
    updateBatchButton();
}

/* ─── Parameters ─────────────────────────────────────── */

function setupParams() {
    qa('.prm').forEach(prm => {
        const slider = prm.querySelector('input[type="range"]');
        const valEl = prm.querySelector('.prm-val');
        const key = prm.dataset.param;
        // Initial baseline = the slider's HTML default. selectStock() may
        // overwrite stockDefault when a stock with non-global values is picked.
        slider.dataset.stockDefault = slider.value;
        const decimals = parseInt(slider.dataset.decimals || '1');

        slider.addEventListener('input', () => {
            const val = parseFloat(slider.value);
            const baseline = parseFloat(slider.dataset.stockDefault);
            if (Math.abs(val - baseline) > 1e-4) {
                S.params[key] = val;
            } else {
                delete S.params[key];
            }
            valEl.textContent = val.toFixed(decimals);
            if (S.imageId) {
                clearTimeout(S.debounce);
                S.debounce = setTimeout(requestPreview, 400);
            }
        });
    });

    qs('#print-stock').addEventListener('change', e => {
        S.printStock = e.target.value || null;
        if (S.imageId) requestPreview();
    });

    // Collapsible "Look" section
    qa('.section-head-toggle').forEach(head => {
        head.addEventListener('click', () => {
            const target = qs('#' + head.dataset.toggle);
            if (!target) return;
            target.classList.toggle('params-collapsed');
            const arrow = head.querySelector('.toggle-arrow');
            if (arrow) arrow.textContent = target.classList.contains('params-collapsed') ? '▾' : '▴';
        });
    });
}

/* ─── Preview ────────────────────────────────────────── */

async function requestPreview() {
    if (!S.imageId) return;
    showLoader(true, 'Processing preview\u2026');

    const p = new URLSearchParams({ stock: S.stock });
    if (S.printStock) p.set('print_stock', S.printStock);
    for (const [k, v] of Object.entries(S.params)) p.set(k, v);

    try {
        const resp = await fetch(`/api/preview/${S.imageId}?${p}`);
        if (!resp.ok) throw new Error();
        const url = URL.createObjectURL(await resp.blob());
        const img = qs('#img-processed');
        if (img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);
        img.src = url;
    } catch (e) { console.error('Preview error', e); }
    showLoader(false);
}

function showLoader(on, text) {
    const el = qs('#cmp-loader');
    if (text) qs('#cmp-loader-text').textContent = text;
    el.classList.toggle('active', on);
}

/* ─── Compare Slider ─────────────────────────────────── */

function setupCompare() {
    const container = qs('#compare-container');
    const divider = qs('#cmp-divider');
    const original = qs('#img-original');
    let dragging = false;

    function update(x) {
        const r = container.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (x - r.left) / r.width));
        divider.style.left = (pct * 100) + '%';
        original.style.clipPath = `inset(0 ${(1 - pct) * 100}% 0 0)`;
    }

    container.addEventListener('mousedown', e => { dragging = true; update(e.clientX); e.preventDefault(); });
    window.addEventListener('mousemove', e => { if (dragging) update(e.clientX); });
    window.addEventListener('mouseup', () => dragging = false);
    container.addEventListener('touchstart', e => { dragging = true; update(e.touches[0].clientX); e.preventDefault(); }, { passive: false });
    window.addEventListener('touchmove', e => { if (dragging) update(e.touches[0].clientX); });
    window.addEventListener('touchend', () => dragging = false);

    // Export
    qs('#btn-export').addEventListener('click', startExport);
}

/* ─── Export ──────────────────────────────────────────── */

async function startExport() {
    const btn = qs('#btn-export');
    btn.disabled = true; btn.textContent = 'Processing\u2026';

    const prog = qs('#export-progress');
    const fill = qs('#export-fill');
    const step = qs('#export-step');
    const dl = qs('#export-downloads');
    prog.classList.add('active');
    dl.innerHTML = '';

    const p = new URLSearchParams({ image_id: S.imageId, stock: S.stock });
    if (S.printStock) p.set('print_stock', S.printStock);
    for (const [k, v] of Object.entries(S.params)) p.set(k, v);

    const resp = await fetch(`/api/process?${p}`, { method: 'POST' });
    if (!resp.ok) { btn.disabled = false; btn.textContent = 'Export Full Resolution'; prog.classList.remove('active'); alert('Export failed'); return; }
    const { job_id } = await resp.json();

    const poll = setInterval(async () => {
        const sr = await fetch(`/api/status/${job_id}`);
        const st = await sr.json();
        fill.style.width = st.progress + '%';
        step.textContent = st.step || '';

        if (st.status === 'done') {
            clearInterval(poll);
            prog.classList.remove('active');
            btn.disabled = false;
            btn.textContent = 'Export Full Resolution';
            step.textContent = '';
            dl.innerHTML = `
                <a class="btn-dl" href="/api/download/${job_id}?variant=clean" download>JPEG</a>
                <a class="btn-dl" href="/api/download/${job_id}?variant=tiff" download>TIFF (lossless)</a>
                <a class="btn-dl" href="/api/download/${job_id}?variant=bordered" download>Bordered</a>`;
        } else if (st.status === 'error') {
            clearInterval(poll);
            prog.classList.remove('active');
            btn.disabled = false;
            btn.textContent = 'Export Full Resolution';
            step.textContent = 'Error: ' + (st.error || 'Unknown');
        }
    }, 1000);
}

/* ─── Gallery ────────────────────────────────────────── */

let galleryPrintStocks = {};
let galleryPrintFilter = 'all'; // 'all' = all prints (default), '' = default only, or a specific print key
let galleryAbort = null; // AbortController to cancel pending thumbnail fetches

async function loadGallery() {
    // Load print stocks for filter tabs if not yet loaded
    if (Object.keys(galleryPrintStocks).length === 0) {
        const data = await fetch('/api/print-stocks').then(r => r.json());
        galleryPrintStocks = data;
        buildGalleryPrintTabs();
    }
    renderGallery();
}

function buildGalleryPrintTabs() {
    const tabs = qs('#gallery-print-tabs');
    tabs.innerHTML = '';

    const options = [
        { key: '', label: 'Default' },
        { key: 'all', label: 'All Prints' },
        ...Object.entries(galleryPrintStocks).map(([k, name]) => ({
            key: k, label: name.replace(/\s*\(.*\)/, '') // strip parenthetical
        })),
    ];

    for (const opt of options) {
        const btn = document.createElement('button');
        btn.className = 'gal-print-tab' + (opt.key === galleryPrintFilter ? ' active' : '');
        btn.textContent = opt.label;
        btn.dataset.print = opt.key;
        btn.addEventListener('click', () => {
            galleryPrintFilter = opt.key;
            tabs.querySelectorAll('.gal-print-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderGallery();
        });
        tabs.appendChild(btn);
    }
}

function renderGallery() {
    // Cancel any in-flight thumbnail fetches
    if (galleryAbort) galleryAbort.abort();
    galleryAbort = new AbortController();

    const grid = qs('#gallery-grid');
    grid.innerHTML = '';

    if (galleryPrintFilter === 'all') {
        renderGalleryAllPrints(grid);
    } else {
        renderGalleryFiltered(grid, galleryPrintFilter || null);
    }
}

function renderGalleryFiltered(grid, printKey) {
    const stocks = S.galleryStocks;
    // If a specific print is selected, only show mixable stocks
    const filtered = printKey ? stocks.filter(s => MIXABLE_CATS.has(s.category)) : stocks;
    qs('#gallery-count').textContent = filtered.length;

    const printLabel = printKey ? galleryPrintStocks[printKey].replace(/\s*\(.*\)/, '') : '';
    for (const stock of filtered) {
        grid.appendChild(makeGalleryCard(stock, printKey, printLabel));
    }
}

// Categories that support print stock mixing (color negatives only)
const MIXABLE_CATS = new Set(['pro_color', 'consumer', 'cinema', 'vintage_cinema']);

function renderGalleryAllPrints(grid) {
    const stocks = S.galleryStocks;
    const printKeys = Object.keys(galleryPrintStocks);
    let totalCount = 0;

    // First: all stocks with default print
    const defaultHead = document.createElement('div');
    defaultHead.className = 'gallery-section-head';
    defaultHead.innerHTML = '<em>Default</em> print stock per film';
    grid.appendChild(defaultHead);

    for (const stock of stocks) {
        grid.appendChild(makeGalleryCard(stock, null, ''));
        totalCount++;
    }

    // Then: each print stock section (only color negatives — skip B&W, reversal, instant)
    for (const pk of printKeys) {
        const pname = galleryPrintStocks[pk].replace(/\s*\(.*\)/, '');
        const mixable = stocks.filter(s => MIXABLE_CATS.has(s.category));
        if (mixable.length === 0) continue;

        const head = document.createElement('div');
        head.className = 'gallery-section-head';
        head.innerHTML = `<em>${pname}</em>`;
        grid.appendChild(head);

        for (const stock of mixable) {
            grid.appendChild(makeGalleryCard(stock, pk, pname));
            totalCount++;
        }
    }

    qs('#gallery-count').textContent = totalCount;
}

function makeGalleryCard(stock, printKey, printLabel) {
    const card = document.createElement('div');
    card.className = 'gal-card loading';

    const subtitle = printLabel ? `${stock.category} · ${printLabel}` : stock.category;
    card.innerHTML = `
        <img src="" alt="${stock.name}">
        <button class="gal-batch-btn" title="Apply to batch of photos">📦</button>
        <div class="gal-info">
            <span class="gal-name">${stock.name}</span>
            <span class="gal-cat">${subtitle}</span>
        </div>`;

    card.addEventListener('click', () => {
        S.stock = stock.key;
        S.category = stock.category;
        if (printKey) {
            S.printStock = printKey;
            qs('#print-stock').value = printKey;
        } else {
            S.printStock = null;
            qs('#print-stock').value = '';
        }
        renderCats('#cat-tabs', S.category, key => { S.category = key; renderStocks('#stock-list', S.stock, selectStock); });
        renderStocks('#stock-list', S.stock, selectStock);
        enterApp('compare');
        requestPreview();
    });

    card.querySelector('.gal-batch-btn').addEventListener('click', e => {
        e.stopPropagation();  // don't trigger the card's "open in compare"
        startGalleryBatch(stock, printKey, printLabel);
    });

    // Lazy-load thumbnail (cancellable)
    const img = card.querySelector('img');
    const printParam = printKey ? `&print_stock=${printKey}` : '';
    const signal = galleryAbort ? galleryAbort.signal : undefined;
    fetch(`/api/thumbnail/${S.imageId}?stock=${stock.key}${printParam}`, { signal })
        .then(r => { if (!r.ok) throw new Error(); return r.blob(); })
        .then(blob => {
            if (img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);
            img.src = URL.createObjectURL(blob);
            card.classList.remove('loading');
        })
        .catch(e => {
            if (e.name !== 'AbortError') card.classList.remove('loading');
        });

    return card;
}

/* ─── Gallery → Batch ────────────────────────────────── */
// "Apply this look to a batch of photos" — triggered from the 📦 button on
// each gallery tile. Inherits the recipe of that tile (stock + print) plus
// any active photochemical / Look slider overrides, applies it to N photos.

let _galBatchPending = null;  // {stock, printKey, printLabel} while file picker is open

function startGalleryBatch(stock, printKey, printLabel) {
    _galBatchPending = { stock, printKey, printLabel };
    const inp = qs('#gal-batch-input');
    inp.value = '';
    inp.click();
}

function setupGalleryBatch() {
    const inp = qs('#gal-batch-input');
    inp.addEventListener('change', async () => {
        const files = Array.from(inp.files || []).filter(f => f.size > 0);
        if (!files.length || !_galBatchPending) return;
        const ctx = _galBatchPending;
        _galBatchPending = null;
        await runGalleryBatch(ctx, files);
    });
    qs('#gbp-close').addEventListener('click', () => {
        qs('#gal-batch-popup').style.display = 'none';
    });
}

async function runGalleryBatch({ stock, printKey, printLabel }, files) {
    const popup = qs('#gal-batch-popup');
    const recipeEl = qs('#gbp-recipe');
    const stepEl = qs('#gbp-step');
    const fill = qs('#gbp-fill');
    const dl = qs('#gbp-dl');

    popup.style.display = '';
    dl.style.display = 'none';
    fill.style.width = '0%';
    recipeEl.textContent = printLabel ? `${stock.name} · ${printLabel}` : stock.name;
    stepEl.textContent = `Uploading ${files.length} file${files.length !== 1 ? 's' : ''}…`;

    // Upload
    const form = new FormData();
    for (const f of files) form.append('files', f);
    const upResp = await fetch('/api/batch/upload', { method: 'POST', body: form });
    if (!upResp.ok) { stepEl.textContent = 'Upload failed'; return; }
    const upData = await upResp.json();
    if (!upData.paths || upData.paths.length === 0) {
        stepEl.textContent = 'No supported files in selection';
        return;
    }

    // Build params: this tile's stock+print + any active overrides from S.params
    const p = new URLSearchParams({ stock: stock.key });
    if (printKey) p.set('print_stock', printKey);
    for (const [k, v] of Object.entries(S.params)) p.set(k, v);
    for (const path of upData.paths) p.append('paths', path);

    stepEl.textContent = 'Processing…';
    const procResp = await fetch(`/api/batch/process?${p}`, { method: 'POST' });
    if (!procResp.ok) { stepEl.textContent = 'Process failed'; return; }
    const { batch_id } = await procResp.json();

    const poll = setInterval(async () => {
        try {
            const sr = await fetch(`/api/batch/status/${batch_id}`);
            const st = await sr.json();
            fill.style.width = (st.progress || 0) + '%';
            stepEl.textContent = st.current_file
                ? `${st.current}/${st.total} — ${st.current_file}`
                : (st.status === 'done' ? 'Done' : 'Processing…');
            if (st.status === 'done') {
                clearInterval(poll);
                fill.style.width = '100%';
                dl.href = `/api/batch/download/${batch_id}`;
                dl.style.display = '';
            } else if (st.status === 'error') {
                clearInterval(poll);
                stepEl.textContent = 'Error: ' + (st.error || 'unknown');
            }
        } catch (e) { /* keep polling on transient errors */ }
    }, 1500);
}


/* ─── Batch ──────────────────────────────────────────── */

function setupBatch() {
    const drop = qs('#batch-drop');
    const inp = qs('#batch-file-input');

    drop.addEventListener('click', () => inp.click());
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('dragover'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
    drop.addEventListener('drop', e => {
        e.preventDefault(); drop.classList.remove('dragover');
        addBatchFiles(e.dataTransfer.files);
    });
    inp.addEventListener('change', () => { addBatchFiles(inp.files); inp.value = ''; });

    qs('#batch-go').addEventListener('click', startBatch);
}

function addBatchFiles(fileList) {
    for (const f of fileList) {
        if (!f.type.startsWith('image/')) continue;
        S.batchFiles.push(f);
    }
    renderBatchList();
    updateBatchButton();
}

function renderBatchList() {
    const el = qs('#batch-file-list');
    el.innerHTML = '';
    S.batchFiles.forEach((f, i) => {
        const row = document.createElement('div');
        row.className = 'batch-file';
        row.innerHTML = `
            <span class="bf-name">${f.name}</span>
            <span class="bf-size">${(f.size / 1024 / 1024).toFixed(1)} MB</span>
            <button class="bf-remove">&times;</button>`;
        row.querySelector('.bf-remove').addEventListener('click', () => {
            S.batchFiles.splice(i, 1);
            renderBatchList();
            updateBatchButton();
        });
        el.appendChild(row);
    });
}

function updateBatchButton() {
    qs('#batch-go').disabled = S.batchFiles.length === 0;
}

async function startBatch() {
    const btn = qs('#batch-go');
    btn.disabled = true; btn.textContent = 'Uploading\u2026';

    // Upload all files
    const form = new FormData();
    for (const f of S.batchFiles) form.append('files', f);
    const upResp = await fetch('/api/batch/upload', { method: 'POST', body: form });
    if (!upResp.ok) { btn.disabled = false; btn.textContent = 'Process All'; alert('Batch upload failed'); return; }
    const upData = await upResp.json();

    btn.textContent = 'Processing\u2026';
    const wrap = qs('#batch-progress-wrap');
    wrap.style.display = '';

    // Start batch process
    const p = new URLSearchParams({ stock: S.batchStock });
    for (const path of upData.paths) p.append('paths', path);
    const procResp = await fetch(`/api/batch/process?${p}`, { method: 'POST' });
    if (!procResp.ok) { btn.disabled = false; btn.textContent = 'Process All'; wrap.style.display = 'none'; alert('Batch process failed'); return; }
    const { batch_id } = await procResp.json();

    const fill = qs('#batch-fill');
    const step = qs('#batch-step');

    const poll = setInterval(async () => {
        const sr = await fetch(`/api/batch/status/${batch_id}`);
        const st = await sr.json();
        fill.style.width = st.progress + '%';
        step.textContent = st.current_file ? `${st.current}/${st.total} — ${st.current_file}` : '';

        if (st.status === 'done') {
            clearInterval(poll);
            wrap.style.display = 'none';
            btn.disabled = false;
            btn.textContent = 'Process All';
            const dl = qs('#batch-dl');
            dl.href = `/api/batch/download/${batch_id}`;
            dl.style.display = '';
        }
    }, 1500);
}
