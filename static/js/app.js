// == Helpers ==

function fmt(n) {
    if (n == null) return "\u2014";
    return "\u20b9 " + Number(n).toLocaleString("en-IN");
}

function fmtDelta(n) {
    if (n == null) return "\u2014";
    var sign = n > 0 ? "+" : "";
    return sign + "\u20b9 " + Number(n).toLocaleString("en-IN");
}

function fmtDate(iso) {
    if (!iso) return "\u2014";
    var d = new Date(iso);
    return d.toLocaleString("en-IN", {
        day: "2-digit", month: "short", year: "numeric",
        hour: "2-digit", minute: "2-digit", hour12: true,
        timeZone: "Asia/Kolkata"
    });
}

function fmtSize(kb) {
    if (kb > 1024) return (kb / 1024).toFixed(1) + " MB";
    return kb + " KB";
}

function showAlert(msg, type) {
    type = type || "info";
    var area = document.getElementById("alert-area");
    var id = "alert-" + Date.now();
    area.innerHTML =
        '<div id="' + id + '" class="tl-alert ' + type + '">' + msg +
        '<button class="tl-alert-close" onclick="this.parentElement.remove()">&times;</button></div>';
    setTimeout(function() {
        var el = document.getElementById(id);
        if (el) el.remove();
    }, 8000);
}

async function api(url, opts) {
    opts = opts || {};
    if (typeof CSRF_TOKEN !== "undefined" && CSRF_TOKEN) {
        opts.headers = opts.headers || {};
        if (typeof opts.headers === "object" && !(opts.headers instanceof Headers)) {
            opts.headers["X-CSRF-Token"] = CSRF_TOKEN;
        }
    }
    var resp = await fetch(url, opts);
    if (resp.status === 401) {
        window.location.href = "/login";
        return { ok: false, error: "Session expired" };
    }
    return resp.json();
}

// == State ==
var liveRates = null;
var storedRate = null;
var rateHistory = [];

// == Clock ==
function tickClock() {
    var el = document.getElementById("clock");
    if (el) {
        var now = new Date();
        el.textContent = now.toLocaleString("en-IN", {
            weekday: "short", day: "2-digit", month: "short", year: "numeric",
            hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true,
            timeZone: "Asia/Kolkata"
        });
    }
}
setInterval(tickClock, 1000);
tickClock();

// == Tab Switching (Hero & Bottom) ==
function initTabs() {
    // Hero tabs
    document.querySelectorAll(".htab").forEach(function(tab) {
        tab.addEventListener("click", function() {
            document.querySelectorAll(".htab").forEach(function(t) { t.classList.remove("active"); });
            document.querySelectorAll(".hero-tab-content").forEach(function(p) { p.classList.remove("active"); });
            tab.classList.add("active");
            var target = document.getElementById(tab.getAttribute("data-target"));
            if (target) target.classList.add("active");
        });
    });
    // Bottom tabs
    document.querySelectorAll(".tabs-nav .tab").forEach(function(tab) {
        tab.addEventListener("click", function() {
            var nav = tab.closest(".tabs-nav");
            var body = nav.nextElementSibling;
            nav.querySelectorAll(".tab").forEach(function(t) { t.classList.remove("active"); });
            body.querySelectorAll(".tab-panel").forEach(function(p) { p.classList.remove("active"); });
            tab.classList.add("active");
            var target = document.getElementById(tab.getAttribute("data-target"));
            if (target) target.classList.add("active");
        });
    });
}

// == Logout ==
async function doLogout() {
    await api("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
}

// == Ticker ==
function updateTicker() {
    var track = document.getElementById("ticker-track");
    if (!track) return;
    var items = [];
    if (liveRates) {
        if (liveRates["fine_gold"]) items.push("24KT: " + fmt(liveRates["fine_gold"]) + "/g");
        items.push("18KT: " + fmt(liveRates["18kt"]) + "/g");
        items.push("14KT: " + fmt(liveRates["14kt"]) + "/g");
        items.push("9KT: " + fmt(liveRates["9kt"]) + "/g");
        items.push("Session: " + (liveRates.session || "\u2014"));
        items.push("IBJA Date: " + (liveRates.date || "\u2014"));
    }
    if (storedRate) {
        items.push("Applied 18KT: " + fmt(storedRate.rate_18kt));
    }
    if (items.length === 0) {
        items.push("Loading market data\u2026");
    }
    // duplicate for seamless scroll
    var html = items.map(function(t) { return '<span class="ticker-item">' + t + '</span>'; }).join("");
    track.innerHTML = html + html;
}

// == Live Rates ==
async function fetchLiveRates() {
    var data = await api("/api/rates/current");
    if (!data.ok) {
        document.getElementById("hero-price").textContent = "Error";
        document.getElementById("hero-ts").textContent = data.error || "Failed to fetch";
        document.getElementById("live-18kt").textContent = "Error";
        return;
    }

    liveRates = data.rates;

    // Hero price block – show 24KT (fine gold 999) rate
    document.getElementById("hero-price").textContent = fmt(liveRates["fine_gold"] || liveRates["18kt"]);
    var sessionBadge = liveRates.session === "AM" ? "AM" : "PM";
    document.getElementById("hero-ts").textContent = sessionBadge + " session \u00b7 " + (liveRates.date || "\u2014") + " \u00b7 per gram, excl. GST";

    // Hero ranges
    var rangesEl = document.getElementById("hero-ranges");
    if (rangesEl) {
        rangesEl.innerHTML =
            '<div class="range-row"><span class="range-val">18 KT</span><div class="range-bar-wrap"><div class="range-bar-fill" style="width:100%"></div><span class="range-dot" style="left:100%"></span></div><span class="range-val hi">' + fmt(liveRates["18kt"]) + '</span></div>' +
            '<div class="range-row"><span class="range-val">14 KT</span><div class="range-bar-wrap"><div class="range-bar-fill" style="width:78%"></div><span class="range-dot" style="left:78%"></span></div><span class="range-val hi">' + fmt(liveRates["14kt"]) + '</span></div>' +
            '<div class="range-row"><span class="range-val">9 KT</span><div class="range-bar-wrap"><div class="range-bar-fill" style="width:50%"></div><span class="range-dot" style="left:50%"></span></div><span class="range-val hi">' + fmt(liveRates["9kt"]) + '</span></div>';
    }

    // Rate strip - Live card
    document.getElementById("live-18kt").textContent = fmt(liveRates["18kt"]);
    document.getElementById("live-karats").innerHTML =
        '<span class="kt-val">14KT: ' + fmt(liveRates["14kt"]) + '</span>' +
        '<span class="kt-val">9KT: ' + fmt(liveRates["9kt"]) + '</span>';
    document.getElementById("live-session").textContent = sessionBadge + " \u00b7 " + (liveRates.date || "");

    updateDelta();
    updateTicker();
    drawSparkline();
}

// == Stored Rates ==
async function fetchStoredRate() {
    var data = await api("/api/rates/stored");

    if (!data.ok || !data.rate) {
        document.getElementById("stored-18kt").textContent = "\u2014";
        document.getElementById("stored-karats").innerHTML = "";
        document.getElementById("stored-ts").textContent = "No baseline set yet";
        storedRate = null;
        updateDelta();
        return;
    }

    storedRate = data.rate;
    document.getElementById("stored-18kt").textContent = fmt(storedRate.rate_18kt);
    document.getElementById("stored-karats").innerHTML =
        '<span class="kt-val">14KT: ' + fmt(storedRate.rate_14kt) + '</span>' +
        '<span class="kt-val">9KT: ' + fmt(storedRate.rate_9kt) + '</span>';
    document.getElementById("stored-ts").textContent = fmtDate(storedRate.timestamp) + " \u00b7 " + (storedRate.session || "");

    updateDelta();
    updateTicker();
}

// == Delta ==
function updateDelta() {
    if (!liveRates || !storedRate) {
        document.getElementById("delta-18kt").textContent = "\u2014";
        document.getElementById("delta-karats").innerHTML = "";
        document.getElementById("delta-status").textContent = "Waiting for both rates\u2026";
        // Update hero change
        var hc = document.getElementById("hero-change");
        if (hc) hc.innerHTML = "";
        return;
    }

    var d9 = liveRates["9kt"] - (storedRate.rate_9kt || 0);
    var d14 = liveRates["14kt"] - storedRate.rate_14kt;
    var d18 = liveRates["18kt"] - storedRate.rate_18kt;
    // Proportional 24KT delta (drive off 18KT change, scaled to fine-gold price)
    var d24 = (liveRates["fine_gold"] && liveRates["18kt"])
        ? Math.round((liveRates["fine_gold"] / liveRates["18kt"]) * d18)
        : d18;
    var needUpdate = d9 !== 0 || d14 !== 0 || d18 !== 0;

    function cls(d) { return d > 0 ? "delta-positive" : d < 0 ? "delta-negative" : ""; }

    document.getElementById("delta-18kt").innerHTML = '<span class="' + cls(d18) + '">' + fmtDelta(d18) + '/g</span>';
    document.getElementById("delta-karats").innerHTML =
        '<span class="kt-val ' + cls(d14) + '">14KT: ' + fmtDelta(d14) + '</span>' +
        '<span class="kt-val ' + cls(d9) + '">9KT: ' + fmtDelta(d9) + '</span>';
    document.getElementById("delta-status").innerHTML = needUpdate
        ? '<span class="badge-sm badge-danger">Update Available</span>'
        : '<span class="badge-sm badge-success">Up to Date</span>';

    // Hero change — show 24KT delta
    var hc = document.getElementById("hero-change");
    if (hc) {
        hc.className = "price-change " + (d24 > 0 ? "up" : d24 < 0 ? "down" : "");
        hc.textContent = (d24 > 0 ? "\u25B2 " : d24 < 0 ? "\u25BC " : "") + fmtDelta(d24) + " from baseline";
    }

    // mc-badge
    var mcb = document.getElementById("mc-badge");
    if (mcb) {
        mcb.textContent = fmtDelta(d18);
        mcb.className = "mc-badge" + (d18 < 0 ? " up" : "");
    }
}

// == Sparkline Chart ==
function drawSparkline() {
    var canvas = document.getElementById("sparkline");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    // Use rate history if available, else generate from current rate
    var points = [];
    if (rateHistory.length > 1) {
        points = rateHistory.slice(-12).map(function(r) { return r.rate_18kt || 0; });
    } else if (liveRates) {
        // Generate slight variations for visual effect
        var base = liveRates["18kt"] || 0;
        for (var i = 0; i < 8; i++) {
            points.push(base + (Math.random() - 0.5) * base * 0.005);
        }
        points.push(base);
    }
    if (points.length < 2) return;

    var min = Math.min.apply(null, points);
    var max = Math.max.apply(null, points);
    var range = max - min || 1;
    var pad = 4;

    ctx.beginPath();
    ctx.strokeStyle = points[points.length - 1] >= points[0] ? "#1a7a44" : "#c0392b";
    ctx.lineWidth = 1.5;
    points.forEach(function(v, i) {
        var x = pad + (i / (points.length - 1)) * (W - 2 * pad);
        var y = H - pad - ((v - min) / range) * (H - 2 * pad);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Fill area
    var lastX = pad + ((points.length - 1) / (points.length - 1)) * (W - 2 * pad);
    ctx.lineTo(lastX, H);
    ctx.lineTo(pad, H);
    ctx.closePath();
    ctx.fillStyle = points[points.length - 1] >= points[0] ? "rgba(26,122,68,0.06)" : "rgba(192,57,43,0.06)";
    ctx.fill();

    // X-axis labels
    var xaxis = document.getElementById("mc-xaxis");
    if (xaxis && rateHistory.length > 1) {
        var labels = rateHistory.slice(-12);
        var first = labels[0], last = labels[labels.length - 1];
        xaxis.innerHTML = '<span>' + (first.timestamp ? new Date(first.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }) : "") + '</span><span>' +
            (last.timestamp ? new Date(last.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }) : "") + '</span>';
    }
}

// == Overview Panel ==
function updateOverview() {
    var el = document.getElementById("overview-content");
    if (!el) return;

    var html = '<table class="data-table"><thead><tr><th>Metric</th><th>Value</th><th>Status</th></tr></thead><tbody>';

    if (liveRates) {
        html += '<tr><td>Live 18KT</td><td class="mono fw">' + fmt(liveRates["18kt"]) + '</td><td><span class="badge-sm badge-success">LIVE</span></td></tr>';
        html += '<tr><td>Live 14KT</td><td class="mono fw">' + fmt(liveRates["14kt"]) + '</td><td><span class="badge-sm badge-success">LIVE</span></td></tr>';
        html += '<tr><td>Live 9KT</td><td class="mono fw">' + fmt(liveRates["9kt"]) + '</td><td><span class="badge-sm badge-success">LIVE</span></td></tr>';
        html += '<tr><td>Session</td><td class="mono">' + (liveRates.session || "\u2014") + '</td><td><span class="badge-sm badge-blue">' + (liveRates.session || "") + '</span></td></tr>';
    }
    if (storedRate) {
        html += '<tr><td>Applied 18KT</td><td class="mono fw">' + fmt(storedRate.rate_18kt) + '</td><td><span class="badge-sm badge-muted">Baseline</span></td></tr>';
        html += '<tr><td>Applied At</td><td class="mono">' + fmtDate(storedRate.timestamp) + '</td><td></td></tr>';
    }
    if (liveRates && storedRate) {
        var d = liveRates["18kt"] - storedRate.rate_18kt;
        html += '<tr><td>Delta 18KT</td><td class="mono fw ' + (d > 0 ? "delta-positive" : d < 0 ? "delta-negative" : "") + '">' + fmtDelta(d) + '/g</td><td>' +
            (d !== 0 ? '<span class="badge-sm badge-danger">CHANGE</span>' : '<span class="badge-sm badge-success">OK</span>') + '</td></tr>';
    }

    html += '</tbody></table>';
    el.innerHTML = html;
}

// == Rate Config ==

async function fetchConfig() {
    var data = await api("/api/config");
    if (!data.ok) return;

    var cfg = data.config;
    function setVal(id, val) {
        var el = document.getElementById(id);
        if (el) el.value = val;
    }

    setVal("cfg-i1i2", cfg.diamond_i1i2);
    setVal("cfg-si", cfg.diamond_si);
    setVal("cfg-colorstone", cfg.colorstone_rate);
    setVal("cfg-huid", cfg.huid_per_pc);
    setVal("cfg-cert", cfg.certification);
    setVal("cfg-making", cfg.making_charge);

    setVal("cmp-i1i2", cfg.cmp_diamond_i1i2);
    setVal("cmp-si", cfg.cmp_diamond_si);
    setVal("cmp-colorstone", cfg.cmp_colorstone_rate);
    setVal("cmp-huid", cfg.cmp_huid_per_pc);
    setVal("cmp-cert", cfg.cmp_certification);
    setVal("cmp-making", cfg.cmp_making_charge);

    var status = document.getElementById("config-status");
    if (status) {
        if (cfg.timestamp) {
            status.innerHTML = '\u2713 Last saved: ' + fmtDate(cfg.timestamp);
        } else {
            status.textContent = 'Using default values';
        }
    }
}

async function saveConfig() {
    var btn = document.getElementById("btn-save-config");
    function getVal(id) { return parseFloat((document.getElementById(id) || {}).value); }

    var payload = {
        diamond_i1i2: getVal("cfg-i1i2"),
        diamond_si: getVal("cfg-si"),
        colorstone_rate: getVal("cfg-colorstone"),
        huid_per_pc: getVal("cfg-huid"),
        certification: getVal("cfg-cert"),
        making_charge: getVal("cfg-making"),
        cmp_diamond_i1i2: getVal("cmp-i1i2"),
        cmp_diamond_si: getVal("cmp-si"),
        cmp_colorstone_rate: getVal("cmp-colorstone"),
        cmp_huid_per_pc: getVal("cmp-huid"),
        cmp_certification: getVal("cmp-cert"),
        cmp_making_charge: getVal("cmp-making")
    };

    for (var k in payload) {
        if (isNaN(payload[k]) || payload[k] < 0) {
            showAlert("Please enter a valid value for all fields.", "warning");
            return;
        }
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> Saving\u2026';

    try {
        var data = await api("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (data.ok) {
            showAlert('\u2713 ' + data.message, "success");
            await fetchConfig();
        } else {
            showAlert('\u2717 ' + data.error, "danger");
        }
    } catch (e) {
        showAlert('\u2717 ' + e.message, "danger");
    } finally {
        btn.disabled = false;
        btn.innerHTML = '\u2713 Save Charts';
    }
}

// == Run Pricing ==

async function runUpdate() {
    var activeCheck = await api("/api/upload/active");
    if (!activeCheck.ok || !activeCheck.upload) {
        showAlert('<b>Upload Required:</b> Please upload a CSV/XLSX file before running pricing.', "warning");
        return;
    }

    var btn = document.getElementById("btn-update");
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> Running\u2026';

    try {
        var data = await api("/api/update/run", { method: "POST" });

        if (!data.ok) {
            showAlert('<b>Error:</b> ' + data.error, "danger");
            return;
        }

        if (data.status === "baseline_set") {
            showAlert(data.message, "info");
        } else if (data.status === "no_change") {
            showAlert(data.message, "info");
        } else if (data.status === "updated") {
            showAlert(
                '<b>Success!</b> ' + data.message + '<br>' +
                '<small>18KT: ' + fmtDelta(data.delta_18kt) + '/g | ' +
                '14KT: ' + fmtDelta(data.delta_14kt) + '/g | ' +
                '9KT: ' + fmtDelta(data.delta_9kt) + '/g | ' +
                'Output: <b>' + data.output_file + '</b></small>',
                "success"
            );
            addLiveFeedItem("success", "Pricing run completed: " + data.output_file);
        }

        await Promise.all([fetchStoredRate(), fetchSheets(), fetchLogs(), fetchUploads(), fetchActiveFile()]);
        updateDelta();
    } catch (e) {
        showAlert('<b>Error:</b> ' + e.message, "danger");
    } finally {
        btn.disabled = false;
        btn.innerHTML = '\u25B6 Run Pricing';
    }
}

async function forceBaseline() {
    if (!confirm("Set current IBJA rate as baseline WITHOUT changing prices?")) return;

    try {
        var data = await api("/api/update/force", { method: "POST" });
        if (data.ok) {
            showAlert(data.message, "info");
            addLiveFeedItem("info", "Baseline set manually");
            await Promise.all([fetchStoredRate(), fetchHistory()]);
            updateDelta();
        } else {
            showAlert("Error: " + data.error, "danger");
        }
    } catch (e) {
        showAlert("Error: " + e.message, "danger");
    }
}

// == Diamond Update ==

async function runDiamondUpdate() {
    var i1i2Val = parseFloat(document.getElementById("diamond-i1i2").value);
    var siVal   = parseFloat(document.getElementById("diamond-si").value);
    var statusEl = document.getElementById("diamond-status");

    if (!i1i2Val || !siVal || i1i2Val <= 0 || siVal <= 0) {
        statusEl.innerHTML = '<span style="color:var(--red)">Please enter valid positive rates for both fields.</span>';
        return;
    }

    var btn = document.getElementById("btn-diamond-update");
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> Running\u2026';
    statusEl.innerHTML = '';

    try {
        var data = await api("/api/diamond/update", {
            method: "POST",
            body: JSON.stringify({ rate_i1i2: i1i2Val, rate_si: siVal })
        });

        if (!data.ok) {
            statusEl.innerHTML = '<span style="color:var(--red)"><b>Error:</b> ' + data.error + '</span>';
            return;
        }

        if (data.status === "no_change") {
            statusEl.innerHTML = '<span style="color:var(--blue)">' + data.message + '</span>';
        } else {
            statusEl.innerHTML = '<span style="color:var(--green)"><b>Done!</b> ' + data.message + ' \u2014 Output: <b>' + data.output_file + '</b></span>';
            showAlert('\u2666 <b>Diamond update complete.</b> ' + data.message, "success");
            addLiveFeedItem("success", "Diamond update: " + data.output_file);
        }

        await Promise.all([fetchSheets(), fetchDiamondLogs()]);
    } catch (e) {
        statusEl.innerHTML = '<span style="color:var(--red)">Error: ' + e.message + '</span>';
    } finally {
        btn.disabled = false;
        btn.innerHTML = '\u2666 Run Diamond Update';
    }
}

// == File Upload ==

async function uploadFile() {
    var input = document.getElementById("file-input");
    if (!input.files.length) return;

    var file = input.files[0];
    var infoEl = document.getElementById("active-file-info");
    infoEl.textContent = "Uploading\u2026";

    try {
        var formData = new FormData();
        formData.append("file", file);

        var resp = await fetch("/api/upload", {
            method: "POST",
            body: formData,
            headers: { "X-CSRF-Token": CSRF_TOKEN }
        });
        var data = await resp.json();

        if (data.ok) {
            showAlert('\u2713 ' + data.message, "success");
            addLiveFeedItem("success", "File uploaded: " + file.name);
            await fetchActiveFile();
            fetchUploads();
        } else {
            showAlert('\u2717 ' + data.error, "danger");
            infoEl.textContent = "Upload failed";
        }
    } catch (e) {
        showAlert('\u2717 ' + e.message, "danger");
        infoEl.textContent = "Upload error";
    }

    input.value = "";
}

async function fetchActiveFile() {
    var el = document.getElementById("active-file-info");
    if (!el) return;

    var data = await api("/api/upload/active");
    if (!data.ok) return;

    var btn = document.getElementById("btn-update");

    if (data.upload) {
        el.innerHTML = '<b>' + data.upload.original_name + '</b> <span style="color:var(--text-3)">(' + fmtDate(data.upload.timestamp) + ')</span>';
        if (btn) { btn.disabled = false; btn.title = ""; }
    } else {
        el.innerHTML = '<span style="color:var(--gold)">Upload a file before running pricing</span>';
        if (btn) { btn.disabled = true; btn.title = "Upload a CSV/XLSX file first"; }
    }
}

// == Icon helpers ==
var _DL_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
var _TRASH_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>';

// == Uploaded Files Tab ==

async function fetchUploads() {
    var container = document.getElementById("uploads-table");
    if (!container) return;

    var data = await api("/api/upload/list");
    if (!data.ok) {
        container.innerHTML = '<div class="empty-state" style="color:var(--red)">' + data.error + '</div>';
        return;
    }

    if (data.uploads.length === 0) {
        container.innerHTML = '<div class="empty-state">No files uploaded yet.</div>';
        return;
    }

    var html = '<div class="uf-table"><div class="uf-head"><span class="col-num">#</span><span class="col-name">Filename</span><span class="col-status">Status</span><span class="col-date">Uploaded</span><span class="col-actions">Actions</span></div><div class="uf-body">';

    data.uploads.forEach(function(u, i) {
        var statusBadge = u.is_active
            ? '<span class="badge-sm badge-success">Active</span>'
            : '<span class="badge-sm badge-muted">Used</span>';
        var deleteBtn = USER_ROLE === 'editor'
            ? '<button class="btn btn-gdanger btn-sm btn-icon" title="Delete" onclick="deleteUpload(\'' + u.filename.replace(/'/g, "\\'") + '\')">'+_TRASH_ICON+'</button>'
            : '';
        html += '<div class="uf-row' + (u.is_active ? ' uf-active' : '') + '">' +
            '<span class="col-num">' + (i + 1) + '</span>' +
            '<span class="col-name">' + (u.original_name || u.filename) + '</span>' +
            '<span class="col-status">' + statusBadge + '</span>' +
            '<span class="col-date">' + fmtDate(u.timestamp) + '</span>' +
            '<span class="col-actions"><div class="sh-actions"><a href="/api/upload/' + encodeURIComponent(u.filename) + '/download" class="btn btn-out btn-sm btn-icon" title="Download">'+_DL_ICON+'</a>' + deleteBtn + '</div></span></div>';
    });

    html += '</div></div>';
    container.innerHTML = html;
}

async function deleteUpload(filename) {
    if (!confirm('Delete uploaded file "' + filename + '"?')) return;
    var data = await api('/api/upload/' + encodeURIComponent(filename) + '/delete', { method: 'DELETE' });
    if (data.ok) {
        showAlert('Uploaded file deleted.', 'success');
        fetchUploads();
        fetchActiveFile();
    } else {
        showAlert(data.error || 'Delete failed.', 'danger');
    }
}

// == Sheets Table ==

async function fetchSheets() {
    var container = document.getElementById("sheets-table");
    var data = await api("/api/sheets");
    if (!data.ok) {
        container.innerHTML = '<div class="empty-state" style="color:var(--red)">' + data.error + '</div>';
        return;
    }

    if (data.sheets.length === 0) {
        container.innerHTML = '<div class="empty-state">No sheets generated yet. Upload a CSV and click "Run Pricing".</div>';
        return;
    }

    var html = '<div class="sh-table"><div class="sh-head"><span class="col-num">#</span><span class="col-name">Filename</span><span class="col-size">Size</span><span class="col-actions">Actions</span></div><div class="sh-body">';

    data.sheets.forEach(function(s, i) {
        var isLatest = i === 0;
        var latestBadge = isLatest ? ' <span class="badge-sm badge-success">Latest</span>' : '';
        var deleteBtn = USER_ROLE === 'editor'
            ? '<button class="btn btn-gdanger btn-sm btn-icon" title="Delete" onclick="deleteSheet(\'' + s.filename.replace(/'/g, "\\'") + '\')">'+_TRASH_ICON+'</button>'
            : '';

        html += '<div class="sh-row">' +
            '<span class="col-num">' + (i + 1) + '</span>' +
            '<span class="col-name"><a href="/api/sheets/' + encodeURIComponent(s.filename) + '/download" class="sh-name-link">' + s.filename + '</a>' + latestBadge + '</span>' +
            '<span class="col-size">' + fmtSize(s.size_kb) + '</span>' +
            '<span class="col-actions"><div class="sh-actions"><a href="/api/sheets/' + encodeURIComponent(s.filename) + '/download" class="btn btn-out btn-sm btn-icon" title="Download">'+_DL_ICON+'</a>' + deleteBtn + '</div></span></div>';
    });

    html += '</div></div>';
    container.innerHTML = html;
}

async function deleteSheet(filename) {
    if (!confirm('Delete "' + filename + '"?')) return;
    var data = await api('/api/sheets/' + encodeURIComponent(filename) + '/delete', { method: 'DELETE' });
    if (data.ok) {
        showAlert('File deleted.', 'success');
        fetchSheets();
    } else {
        showAlert(data.error || 'Delete failed.', 'danger');
    }
}

// == Rate History ==

async function fetchHistory() {
    var container = document.getElementById("history-table");
    var data = await api("/api/rates/history?limit=50");
    if (!data.ok) {
        container.innerHTML = '<div class="empty-state" style="color:var(--red)">' + data.error + '</div>';
        return;
    }
    if (data.history.length === 0) {
        container.innerHTML = '<div class="empty-state">No rate history yet.</div>';
        return;
    }

    rateHistory = data.history;

    var html = '<table class="data-table"><thead><tr>' +
        '<th>#</th><th>Timestamp</th><th>18 KT</th><th>14 KT</th><th>9 KT</th><th>Session</th></tr></thead><tbody>';

    data.history.forEach(function(r, i) {
        html += '<tr><td>' + (i + 1) + '</td>' +
            '<td class="mono">' + fmtDate(r.timestamp) + '</td>' +
            '<td class="mono fw">' + fmt(r.rate_18kt) + '</td>' +
            '<td class="mono fw">' + fmt(r.rate_14kt) + '</td>' +
            '<td class="mono fw">' + fmt(r.rate_9kt) + '</td>' +
            '<td><span class="badge-sm ' + (r.session === 'AM' ? 'badge-blue' : 'badge-purple') + '">' + (r.session || '\u2014') + '</span></td></tr>';
    });

    html += '</tbody></table>';
    container.innerHTML = html;
    drawSparkline();
}

// == Update Logs ==

async function fetchLogs() {
    var container = document.getElementById("logs-table");
    var data = await api("/api/logs?limit=50");
    if (!data.ok) {
        container.innerHTML = '<div class="empty-state" style="color:var(--red)">' + data.error + '</div>';
        return;
    }
    if (data.logs.length === 0) {
        container.innerHTML = '<div class="empty-state">No update logs yet.</div>';
        return;
    }

    var html = '<table class="data-table"><thead><tr>' +
        '<th>#</th><th>Timestamp</th><th>New 14KT</th><th>New 18KT</th>' +
        '<th>Variants</th><th>Output</th><th>Status</th></tr></thead><tbody>';

    data.logs.forEach(function(l, i) {
        var d14 = l.new_rate_14kt - l.old_rate_14kt;
        var d18 = l.new_rate_18kt - l.old_rate_18kt;
        html += '<tr><td>' + (i + 1) + '</td>' +
            '<td class="mono">' + fmtDate(l.timestamp) + '</td>' +
            '<td class="mono fw">' + fmt(l.new_rate_14kt) + ' <small class="' + (d14 >= 0 ? 'delta-positive' : 'delta-negative') + '">(' + fmtDelta(d14) + ')</small></td>' +
            '<td class="mono fw">' + fmt(l.new_rate_18kt) + ' <small class="' + (d18 >= 0 ? 'delta-positive' : 'delta-negative') + '">(' + fmtDelta(d18) + ')</small></td>' +
            '<td class="mono">' + (l.variants_updated ? l.variants_updated.toLocaleString() : 0) + ' / ' + l.products_updated + '</td>' +
            '<td class="mono" style="font-size:0.68rem">' + l.output_file + '</td>' +
            '<td><span class="badge-sm ' + (l.status === 'success' ? 'badge-success' : 'badge-danger') + '">' + l.status + '</span></td></tr>';
    });

    html += '</tbody></table>';
    container.innerHTML = html;

    // Populate live feed from logs
    populateLiveFeed(data.logs);
}

async function fetchDiamondLogs() {
    var container = document.getElementById("diamond-logs-table");
    if (!container) return;

    try {
        var data = await api("/api/diamond/logs");
    } catch (e) { return; }

    if (!data.ok || !data.logs || !data.logs.length) {
        container.innerHTML = '<div class="empty-state">No diamond update logs yet.</div>';
        return;
    }

    var html = '<table class="data-table"><thead><tr>' +
        '<th>#</th><th>Time</th><th>New I1-I2</th><th>New SI</th>' +
        '<th>Variants</th><th>Output</th><th>Status</th></tr></thead><tbody>';

    data.logs.forEach(function(l, i) {
        var di = l.new_rate_i1i2 - l.old_rate_i1i2;
        var ds = l.new_rate_si   - l.old_rate_si;
        html += '<tr><td>' + (i + 1) + '</td>' +
            '<td class="mono">' + fmtDate(l.timestamp) + '</td>' +
            '<td class="mono fw">' + fmt(l.new_rate_i1i2) + ' <small class="' + (di >= 0 ? 'delta-positive' : 'delta-negative') + '">(' + fmtDelta(di) + ')</small></td>' +
            '<td class="mono fw">' + fmt(l.new_rate_si) + ' <small class="' + (ds >= 0 ? 'delta-positive' : 'delta-negative') + '">(' + fmtDelta(ds) + ')</small></td>' +
            '<td class="mono">' + (l.variants_updated ? l.variants_updated.toLocaleString() : 0) + ' / ' + l.products_updated + '</td>' +
            '<td class="mono" style="font-size:0.68rem">' + l.output_file + '</td>' +
            '<td><span class="badge-sm ' + (l.status === 'success' ? 'badge-success' : 'badge-danger') + '">' + l.status + '</span></td></tr>';
    });

    html += '</tbody></table>';
    container.innerHTML = html;
}

// == Live Feed ==

function populateLiveFeed(logs) {
    var container = document.getElementById("lf-items");
    var countEl = document.getElementById("lf-count");
    if (!container || !logs || !logs.length) return;

    var items = logs.slice(0, 20);
    countEl.textContent = items.length;

    var html = '';
    items.forEach(function(l) {
        var dotClass = l.status === 'success' ? 'success' : 'fail';
        var badgeClass = l.status === 'success' ? 'badge-success' : 'badge-danger';
        html += '<div class="lf-item">' +
            '<div class="lf-top"><span class="lf-dot ' + dotClass + '"></span>' +
            '<span class="lf-ts">' + fmtDate(l.timestamp) + '</span>' +
            '<span class="lf-badge ' + badgeClass + '">' + l.status + '</span></div>' +
            '<div class="lf-text">18KT: ' + fmt(l.new_rate_18kt) + ' | Output: ' + l.output_file + '</div></div>';
    });

    container.innerHTML = html;
}

function addLiveFeedItem(type, text) {
    var container = document.getElementById("lf-items");
    var countEl = document.getElementById("lf-count");
    if (!container) return;

    // Remove empty state
    var empty = container.querySelector(".lf-empty");
    if (empty) empty.remove();

    var dotClass = type === 'success' ? 'success' : type === 'fail' ? 'fail' : 'info';
    var now = new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Kolkata" });

    var item = document.createElement("div");
    item.className = "lf-item";
    item.style.animation = "rise 0.3s ease";
    item.innerHTML = '<div class="lf-top"><span class="lf-dot ' + dotClass + '"></span>' +
        '<span class="lf-ts">' + now + '</span></div>' +
        '<div class="lf-text">' + text + '</div>';
    container.insertBefore(item, container.firstChild);

    // Update count
    var currentCount = parseInt(countEl.textContent) || 0;
    countEl.textContent = currentCount + 1;
}

// == Automation Toggle ==

async function loadAutomationStatus() {
    try {
        var res = await fetch("/api/automation/status");
        var data = await res.json();
        if (!data.ok) return;
        _applyAutomationUI(data);
    } catch(e) {
        console.warn("Automation status fetch failed:", e);
    }
}

function _applyAutomationUI(data) {
    var bar = document.getElementById("auto-bar");
    var label = document.getElementById("auto-bar-label");
    var input = document.getElementById("auto-toggle-input");
    if (!bar || !label) return;
    if (data.enabled) {
        bar.className = "auto-bar enabled";
        label.textContent = "Automation: Active \u2014 running on schedule";
        if (input) { input.checked = true; input.disabled = false; }
    } else {
        bar.className = "auto-bar paused";
        var who = data.paused_by ? " by " + data.paused_by : "";
        var when = data.paused_at ? " at " + data.paused_at : "";
        label.textContent = "Automation: Paused" + who + when;
        if (input) { input.checked = false; input.disabled = false; }
    }
}

async function toggleAutomation() {
    var input = document.getElementById("auto-toggle-input");
    if (input) input.disabled = true;
    try {
        var res = await fetch("/api/automation/toggle", {
            method: "POST",
            headers: { "X-CSRF-Token": CSRF_TOKEN, "Content-Type": "application/json" }
        });
        var data = await res.json();
        if (data.ok) {
            _applyAutomationUI(data);
        } else {
            showAlert("error", data.error || "Toggle failed");
            // Revert checkbox to previous state on failure
            if (input) input.checked = !input.checked;
        }
    } catch(e) {
        showAlert("error", "Network error");
        if (input) input.checked = !input.checked;
    } finally {
        if (input) input.disabled = false;
    }
}

// == Init ==

document.addEventListener("DOMContentLoaded", function() {
    initTabs();
    fetchLiveRates();
    fetchStoredRate();
    fetchSheets();
    fetchHistory();
    fetchLogs();
    fetchUploads();
    loadAutomationStatus();

    if (typeof USER_ROLE !== "undefined" && USER_ROLE === "editor") {
        fetchConfig();
        fetchActiveFile();
    }

    // Update overview when rates load
    var checkOverview = setInterval(function() {
        if (liveRates || storedRate) {
            updateOverview();
            clearInterval(checkOverview);
        }
    }, 500);

    // Also update overview after a delay
    setTimeout(updateOverview, 3000);
});
