(function () {
  const els = {
    tokenInput: document.getElementById("tokenInput"),
    saveTokenBtn: document.getElementById("saveTokenBtn"),
    refreshBtn: document.getElementById("refreshBtn"),
    serverState: document.getElementById("serverState"),
    serverPath: document.getElementById("serverPath"),
    heroStatus: document.getElementById("heroStatus"),
    heroLogs: document.getElementById("heroLogs"),
    heroAssets: document.getElementById("heroAssets"),
    statusValue: document.getElementById("statusValue"),
    statusMeta: document.getElementById("statusMeta"),
    logCountValue: document.getElementById("logCountValue"),
    logCountMeta: document.getElementById("logCountMeta"),
    playerCountValue: document.getElementById("playerCountValue"),
    playerCountMeta: document.getElementById("playerCountMeta"),
    assetValue: document.getElementById("assetValue"),
    assetMeta: document.getElementById("assetMeta"),
    searchInput: document.getElementById("searchInput"),
    categoryFilter: document.getElementById("categoryFilter"),
    trafficChips: document.getElementById("trafficChips"),
    autoRefreshInput: document.getElementById("autoRefreshInput"),
    clearLogsBtn: document.getElementById("clearLogsBtn"),
    logList: document.getElementById("logList"),
    detailPane: document.getElementById("detailPane"),
    detailTitle: document.getElementById("detailTitle"),
    detailMeta: document.getElementById("detailMeta"),
    detailBody: document.getElementById("detailBody"),
    closeDetailBtn: document.getElementById("closeDetailBtn"),
    playersTable: document.getElementById("playersTable"),
    toast: document.getElementById("toast"),
    clearModal: document.getElementById("clearModal"),
    clearBodiesInput: document.getElementById("clearBodiesInput"),
    cancelClearBtn: document.getElementById("cancelClearBtn"),
    confirmClearBtn: document.getElementById("confirmClearBtn"),
  };

  const state = {
    entries: [],
    selectedId: "",
    detailTab: "all",
    refreshTimer: 0,
    toastTimer: 0,
    debounceTimer: 0,
  };

  function readInitialToken() {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get("token");
    if (urlToken) {
      localStorage.setItem("saovsAdminToken", urlToken);
      return urlToken;
    }
    return localStorage.getItem("saovsAdminToken") || "";
  }

  els.tokenInput.value = readInitialToken();

  function adminToken() {
    return els.tokenInput.value.trim();
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"]/g, function (char) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
      }[char];
    });
  }

  function showToast(message) {
    window.clearTimeout(state.toastTimer);
    els.toast.textContent = message;
    els.toast.classList.add("show");
    state.toastTimer = window.setTimeout(function () {
      els.toast.classList.remove("show");
    }, 2600);
  }

  async function apiGet(path) {
    const headers = {};
    if (adminToken()) {
      headers["X-Admin-Token"] = adminToken();
    }
    const response = await fetch(path, { headers });
    if (response.status === 401) {
      throw new Error("Admin token required");
    }
    if (!response.ok) {
      throw new Error(path + " returned " + response.status);
    }
    return response.json();
  }

  async function apiPost(path, payload) {
    const headers = { "Content-Type": "application/json" };
    if (adminToken()) {
      headers["X-Admin-Token"] = adminToken();
    }
    const response = await fetch(path, {
      method: "POST",
      headers,
      body: JSON.stringify(payload || {}),
    });
    if (response.status === 401) {
      throw new Error("Admin token required");
    }
    if (!response.ok) {
      throw new Error(path + " returned " + response.status);
    }
    return response.json();
  }

  function compactPath(path) {
    if (!path) return "Server event";
    if (path.length <= 72) return path;
    return "..." + path.slice(-69);
  }

  function prettyBytes(bytes) {
    const size = Number(bytes || 0);
    if (size < 1024) return size + " B";
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
    return (size / 1024 / 1024).toFixed(1) + " MB";
  }

  function prettyTime(value) {
    if (!value) return "-";
    const cleaned = String(value).replace("+00:00", "Z");
    const parsed = new Date(cleaned);
    if (Number.isNaN(parsed.getTime())) {
      return String(value).replace("T", " ").slice(0, 19);
    }
    return parsed.toLocaleString();
  }

  function shortTime(value) {
    if (!value) return "event";
    const parsed = new Date(String(value).replace("+00:00", "Z"));
    if (Number.isNaN(parsed.getTime())) return String(value).slice(11, 19) || "event";
    return parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function statusClass(status) {
    const code = Number(status);
    if (!status || Number.isNaN(code)) return "";
    return code >= 400 ? " error" : "";
  }

  function updateOverview(health, users, logs) {
    const ok = health && health.ok;
    els.serverState.textContent = ok ? "Online" : "Locked";
    els.serverPath.textContent = health && health.serverRoot ? health.serverRoot : "-";
    els.heroStatus.textContent = ok ? "Online" : "Locked";
    els.statusValue.textContent = ok ? "OK" : "Needs Token";
    els.statusMeta.textContent = health && health.assetBase ? health.assetBase : "Admin API unavailable";

    const visible = logs && Number.isFinite(logs.shown) ? logs.shown : 0;
    const available = logs && Number.isFinite(logs.available) ? logs.available : 0;
    const fileSize = logs && logs.logFile ? prettyBytes(logs.logFile.size) : "0 B";
    els.heroLogs.textContent = String(visible);
    els.logCountValue.textContent = String(visible);
    els.logCountMeta.textContent = available + " matching - " + fileSize;

    const playerList = users && Array.isArray(users.users) ? users.users : [];
    const sessions = playerList.reduce(function (sum, user) {
      return sum + Number(user.session_count || 0);
    }, 0);
    els.playerCountValue.textContent = String(playerList.length);
    els.playerCountMeta.textContent = sessions + " sessions";

    const contentOk = health && health.contentRootExists;
    els.heroAssets.textContent = contentOk ? "Ready" : "Missing";
    els.assetValue.textContent = contentOk ? "Ready" : "Missing";
    els.assetMeta.textContent = health && health.contentRoot ? health.contentRoot : "-";
  }

  function renderTrafficChips(payload) {
    const counts = payload && payload.summary && payload.summary.categoryCounts ? payload.summary.categoryCounts : {};
    const order = ["auth", "api", "asset", "error", "server"];
    const total = payload && payload.summary ? Number(payload.summary.total || 0) : 0;
    const chips = ['<span class="traffic-chip">Total <strong>' + escapeHtml(total) + "</strong></span>"];
    order.forEach(function (name) {
      chips.push(
        '<span class="traffic-chip">' +
        escapeHtml(name) +
        " <strong>" +
        escapeHtml(counts[name] || 0) +
        "</strong></span>"
      );
    });
    els.trafficChips.innerHTML = chips.join("");
  }

  function renderLogs(payload) {
    const entries = payload && Array.isArray(payload.entries) ? payload.entries : [];
    state.entries = entries;

    if (!entries.length) {
      els.logList.innerHTML = '<div class="empty-state">No matching logs.</div>';
      renderDetail(null);
      return;
    }

    els.logList.innerHTML = entries.map(function (entry) {
      const category = entry.category || "server";
      const selected = entry.id === state.selectedId ? " active" : "";
      const status = entry.status || "event";
      const key = entry.responseKey || entry.requestKey || "";
      return [
        '<button class="log-row' + selected + '" type="button" data-log-id="' + escapeHtml(entry.id) + '">',
        '  <span class="log-time">' + escapeHtml(shortTime(entry.timestamp)) + '<br>' + escapeHtml(entry.remote || "") + "</span>",
        '  <span class="log-main">',
        '    <span class="log-summary">',
        '      <span class="method-badge">' + escapeHtml(entry.method || "EVT") + "</span>",
        '      <span class="category-badge ' + escapeHtml(category) + '">' + escapeHtml(category) + "</span>",
        key ? '      <span class="key-badge">' + escapeHtml(key) + "</span>" : "",
        "    </span>",
        '    <span class="log-path">' + escapeHtml(compactPath(entry.path || entry.summary)) + "</span>",
        '    <span class="log-preview">' + escapeHtml(entry.preview || "") + "</span>",
        "  </span>",
        '  <span class="status-badge' + statusClass(status) + '">' + escapeHtml(status) + "</span>",
        "</button>",
      ].join("");
    }).join("");

    if (state.selectedId) {
      renderDetail(entries.find(function (entry) { return entry.id === state.selectedId; }) || entries[0]);
    } else {
      renderDetail(entries[0]);
    }
  }

  function lineClass(line) {
    if (line.indexOf("[TIME]") === 0) return "line-time";
    if (line.indexOf("[REQUEST]") === 0 || line.indexOf("[BODY") === 0 || line.indexOf("[SAOVS FRAME]") === 0 || line.indexOf("[HEADERS]") === 0) {
      return "line-request";
    }
    if (line.indexOf("[RESPONSE]") === 0 || line.indexOf("[SAOVS RESPONSE") === 0 || line.indexOf("[ASSET]") === 0 || line.indexOf("[OFFLINE API]") === 0) {
      return "line-response";
    }
    if (line.indexOf("[HTTP ERROR]") === 0 || line.indexOf("[EXCEPTION]") === 0) return "line-error";
    return "";
  }

  function filterDetail(detail) {
    const lines = String(detail || "").split("\n");
    if (state.detailTab === "all") return lines;

    const requestMarkers = ["[TIME]", "[REMOTE]", "[REQUEST]", "[ARGS]", "[HEADERS]", "[BODY", "[SAOVS FRAME]"];
    const responseMarkers = ["[SAOVS RESPONSE", "[RESPONSE]", "[SAOVS RESPONSE KEY]", "[ASSET]", "[OFFLINE API]", "[CATCH-ALL", "[ACCOUNT]", "[HTTP ERROR]", "[EXCEPTION]"];
    const markers = state.detailTab === "request" ? requestMarkers : responseMarkers;

    return lines.filter(function (line) {
      const trimmed = line.trim();
      return markers.some(function (marker) {
        return trimmed.indexOf(marker) === 0;
      });
    });
  }

  function renderDetail(entry) {
    if (!entry) {
      state.selectedId = "";
      els.detailTitle.textContent = "No Log Selected";
      els.detailMeta.innerHTML = "";
      els.detailBody.textContent = "";
      return;
    }

    state.selectedId = entry.id;
    els.detailTitle.textContent = entry.summary || entry.path || "Server event";
    els.detailMeta.innerHTML = [
      '<span class="category-badge ' + escapeHtml(entry.category || "server") + '">' + escapeHtml(entry.category || "server") + "</span>",
      '<span class="method-badge">' + escapeHtml(entry.method || "EVT") + "</span>",
      '<span class="status-badge' + statusClass(entry.status) + '">' + escapeHtml(entry.status || "event") + "</span>",
      entry.requestKey ? '<span class="key-badge">request ' + escapeHtml(entry.requestKey) + "</span>" : "",
      entry.responseKey ? '<span class="key-badge">reply ' + escapeHtml(entry.responseKey) + "</span>" : "",
      '<span class="key-badge">' + escapeHtml(prettyTime(entry.timestamp)) + "</span>",
    ].join("");

    const lines = filterDetail(entry.detail);
    els.detailBody.innerHTML = lines.map(function (line) {
      const cls = lineClass(line);
      return '<span class="' + cls + '">' + escapeHtml(line) + "</span>";
    }).join("\n");

    Array.from(document.querySelectorAll(".log-row")).forEach(function (row) {
      row.classList.toggle("active", row.dataset.logId === entry.id);
    });
  }

  function renderPlayers(payload) {
    const users = payload && Array.isArray(payload.users) ? payload.users : [];
    if (!users.length) {
      els.playersTable.innerHTML = '<div class="empty-state">No players yet.</div>';
      return;
    }

    const rows = users.map(function (user) {
      return [
        '<div class="player-row">',
        '  <span>' + escapeHtml(user.id) + "</span>",
        '  <strong class="truncate">' + escapeHtml(user.user_name) + "</strong>",
        '  <span class="truncate">' + escapeHtml(user.user_code) + "</span>",
        '  <span>' + escapeHtml(user.session_count || 0) + " sessions</span>",
        "</div>",
      ].join("");
    }).join("");

    els.playersTable.innerHTML = [
      '<div class="player-row header">',
      "  <span>ID</span><span>Name</span><span>User Code</span><span>Sessions</span>",
      "</div>",
      rows,
    ].join("");
  }

  function logQueryString() {
    const params = new URLSearchParams();
    params.set("limit", "250");
    const category = els.categoryFilter.value;
    const query = els.searchInput.value.trim();
    if (category && category !== "all") params.set("category", category);
    if (query) params.set("q", query);
    return params.toString();
  }

  async function refreshAll(showDoneToast) {
    els.refreshBtn.disabled = true;
    try {
      const query = logQueryString();
      const results = await Promise.all([
        apiGet("/admin/health"),
        apiGet("/admin/users"),
        apiGet("/admin/api/logs?" + query),
      ]);
      updateOverview(results[0], results[1], results[2]);
      renderTrafficChips(results[2]);
      renderLogs(results[2]);
      renderPlayers(results[1]);
      if (showDoneToast) showToast("Dashboard refreshed.");
    } catch (error) {
      els.serverState.textContent = "Locked";
      els.statusValue.textContent = "Auth";
      els.statusMeta.textContent = error.message;
      els.heroStatus.textContent = "Locked";
      renderTrafficChips({ summary: { total: 0, categoryCounts: {} } });
      els.logList.innerHTML = '<div class="error-state">' + escapeHtml(error.message) + "</div>";
      els.playersTable.innerHTML = '<div class="error-state">' + escapeHtml(error.message) + "</div>";
      if (showDoneToast) showToast(error.message);
    } finally {
      els.refreshBtn.disabled = false;
    }
  }

  function scheduleRefresh() {
    window.clearTimeout(state.debounceTimer);
    state.debounceTimer = window.setTimeout(function () {
      refreshAll(false);
    }, 260);
  }

  function setClearModal(open) {
    els.clearModal.classList.toggle("open", open);
    els.clearModal.setAttribute("aria-hidden", open ? "false" : "true");
  }

  els.saveTokenBtn.addEventListener("click", function () {
    localStorage.setItem("saovsAdminToken", adminToken());
    showToast("Token saved.");
    refreshAll(false);
  });

  els.refreshBtn.addEventListener("click", function () {
    refreshAll(true);
  });

  els.searchInput.addEventListener("input", scheduleRefresh);
  els.categoryFilter.addEventListener("change", scheduleRefresh);

  els.logList.addEventListener("click", function (event) {
    const row = event.target.closest(".log-row");
    if (!row) return;
    const entry = state.entries.find(function (item) { return item.id === row.dataset.logId; });
    renderDetail(entry);
  });

  els.closeDetailBtn.addEventListener("click", function () {
    renderDetail(null);
  });

  Array.from(document.querySelectorAll(".detail-tab")).forEach(function (tab) {
    tab.addEventListener("click", function () {
      state.detailTab = tab.dataset.detailTab || "all";
      Array.from(document.querySelectorAll(".detail-tab")).forEach(function (item) {
        item.classList.toggle("active", item === tab);
      });
      const entry = state.entries.find(function (item) { return item.id === state.selectedId; });
      renderDetail(entry || null);
    });
  });

  Array.from(document.querySelectorAll("[data-scroll-target]")).forEach(function (button) {
    button.addEventListener("click", function () {
      const target = document.getElementById(button.dataset.scrollTarget);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      Array.from(document.querySelectorAll(".nav-item")).forEach(function (item) {
        item.classList.toggle("active", item === button);
      });
    });
  });

  els.clearLogsBtn.addEventListener("click", function () {
    setClearModal(true);
  });

  els.cancelClearBtn.addEventListener("click", function () {
    setClearModal(false);
  });

  els.clearModal.addEventListener("click", function (event) {
    if (event.target === els.clearModal) setClearModal(false);
  });

  els.confirmClearBtn.addEventListener("click", async function () {
    els.confirmClearBtn.disabled = true;
    try {
      await apiPost("/admin/api/logs/clear", {
        requestBodies: els.clearBodiesInput.checked,
      });
      state.selectedId = "";
      setClearModal(false);
      showToast("Logs cleared.");
      await refreshAll(false);
    } catch (error) {
      showToast(error.message);
    } finally {
      els.confirmClearBtn.disabled = false;
    }
  });

  state.refreshTimer = window.setInterval(function () {
    if (els.autoRefreshInput.checked && !document.hidden) {
      refreshAll(false);
    }
  }, 5000);

  refreshAll(false);
})();
