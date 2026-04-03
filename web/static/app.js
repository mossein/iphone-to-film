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
    const data = await resp.json();
    S.imageId = data.id;
    S.imageSrc = URL.createObjectURL(file);

    qs('#img-original').src = S.imageSrc;
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
        const defaultVal = parseFloat(slider.value);
        const decimals = parseInt(slider.dataset.decimals || '1');

        slider.addEventListener('input', () => {
            const val = parseFloat(slider.value);
            if (Math.abs(val - defaultVal) > 0.001) {
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
        qs('#img-processed').src = url;
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

function loadGallery() {
    const grid = qs('#gallery-grid');
    grid.innerHTML = '';
    qs('#gallery-count').textContent = S.galleryStocks.length;

    for (const stock of S.galleryStocks) {
        const card = document.createElement('div');
        card.className = 'gal-card loading';
        card.innerHTML = `
            <img src="" alt="${stock.name}">
            <div class="gal-info">
                <span class="gal-name">${stock.name}</span>
                <span class="gal-cat">${stock.category}</span>
            </div>`;
        card.addEventListener('click', () => {
            S.stock = stock.key;
            S.category = stock.category;
            renderCats('#cat-tabs', S.category, key => { S.category = key; renderStocks('#stock-list', S.stock, selectStock); });
            renderStocks('#stock-list', S.stock, selectStock);
            enterApp('compare');
            requestPreview();
        });
        grid.appendChild(card);

        // Lazy-load thumbnail
        const img = card.querySelector('img');
        fetch(`/api/thumbnail/${S.imageId}?stock=${stock.key}`)
            .then(r => r.blob())
            .then(blob => {
                img.src = URL.createObjectURL(blob);
                card.classList.remove('loading');
            })
            .catch(() => card.classList.remove('loading'));
    }
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
    const upData = await upResp.json();

    btn.textContent = 'Processing\u2026';
    const wrap = qs('#batch-progress-wrap');
    wrap.style.display = '';

    // Start batch process
    const p = new URLSearchParams({ stock: S.batchStock });
    for (const path of upData.paths) p.append('paths', path);
    const procResp = await fetch(`/api/batch/process?${p}`, { method: 'POST' });
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
