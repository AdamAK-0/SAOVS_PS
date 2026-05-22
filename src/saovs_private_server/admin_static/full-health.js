(function () {
  const els = {
    tokenInput: document.getElementById("tokenInput"),
    saveTokenBtn: document.getElementById("saveTokenBtn"),
    refreshBtn: document.getElementById("refreshBtn"),
    overallStatus: document.getElementById("overallStatus"),
    checkedAt: document.getElementById("checkedAt"),
    uptimeValue: document.getElementById("uptimeValue"),
    sessionsValue: document.getElementById("sessionsValue"),
    assetIndexValue: document.getElementById("assetIndexValue"),
    assetIndexMeta: document.getElementById("assetIndexMeta"),
    checkList: document.getElementById("checkList"),
    detailTitle: document.getElementById("detailTitle"),
    detailBody: document.getElementById("detailBody"),
    transferErrors: document.getElementById("transferErrors"),
    assetErrors: document.getElementById("assetErrors"),
    toast: document.getElementById("toast"),
  };

  const state = {
    checks: [],
    selectedName: "",
    toastTimer: 0,
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

  function titleize(value) {
    return String(value || "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, function (char) { return char.toUpperCase(); });
  }

  function prettyTime(value) {
    if (!value) return "-";
    const parsed = new Date(String(value).replace("+00:00", "Z"));
    if (Number.isNaN(parsed.getTime())) return String(value).replace("T", " ").slice(0, 19);
    return parsed.toLocaleString();
  }

  function prettyDuration(seconds) {
    const total = Number(seconds || 0);
    if (total < 60) return Math.round(total) + "s";
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (hours > 0) return hours + "h " + minutes + "m";
    return minutes + "m";
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
    const headers = { Accept: "application/json" };
    if (adminToken()) {
      headers["X-Admin-Token"] = adminToken();
    }
    const response = await fetch(path, { headers, credentials: "same-origin" });
    if (response.status === 401) {
      throw new Error("Admin token required");
    }
    if (!response.ok) {
      throw new Error(path + " returned " + response.status);
    }
    return response.json();
  }

  function checkSummary(check) {
    const details = check.details || {};
    if (check.error) return check.error;
    if (check.name === "database") {
      return details.players + " players, " + details.sessionsActive + " active sessions";
    }
    if (check.name === "asset_index") {
      return details.keyCount + " lookup keys, age " + (details.ageSeconds ?? "-") + "s";
    }
    if (check.name === "known_small_asset_read" || check.name === "manifest_or_index_load") {
      return details.path || details.error || "file check";
    }
    if (check.name === "asset_response_headers") {
      return "status " + details.status + ", range " + (details.contentRange || "not used");
    }
    if (check.name === "critical_asset_routes") {
      const probes = Array.isArray(details.probes) ? details.probes : [];
      const okCount = probes.filter(function (item) { return item.ok; }).length;
      return okCount + " / " + probes.length + " critical assets serving byte ranges";
    }
    if (check.name === "transfer_dependencies" && details.setBNIDBrowserProbe) {
      return details.setBNIDBrowserProbe.hasLoginForm ? "browser transfer login page renders" : "browser transfer login page missing";
    }
    if (check.name === "recent_transfer_flow") {
      const ignored = Number(details.ignoredInternalProbeCount || 0);
      const suffix = ignored ? " (" + ignored + " internal probes ignored)" : "";
      return (details.diagnosis || (details.setBNIDAfterLastProgress + " setBNID calls after progress")) + suffix;
    }
    if (details.path) return details.path;
    if (details.error) return details.error;
    return check.ok ? "ready" : "needs attention";
  }

  function renderSummary(payload) {
    const debug = payload.debug || {};
    els.overallStatus.textContent = payload.ok ? "Ready" : "Review";
    els.checkedAt.textContent = "checked " + prettyTime(payload.checkedAt);
    els.uptimeValue.textContent = prettyDuration(payload.uptimeSeconds);
    els.sessionsValue.textContent = String(debug.activeSessionsCount || 0) + " / " + String(debug.activeCustomizerSessionsCount || 0);
    els.assetIndexValue.textContent = debug.assetIndexLoaded ? "Loaded" : "Missing";
    els.assetIndexMeta.textContent = String(debug.assetIndexKeyCount || 0) + " keys";
    document.querySelector(".pulse-card.primary").classList.toggle("bad", !payload.ok);
  }

  function renderChecks(payload) {
    const checks = Array.isArray(payload.checks) ? payload.checks : [];
    state.checks = checks;
    if (!state.selectedName && checks.length) state.selectedName = checks[0].name;

    els.checkList.innerHTML = checks.map(function (check) {
      const active = check.name === state.selectedName ? " active" : "";
      const failed = check.ok ? "" : " fail";
      return [
        '<button class="check-card' + active + failed + '" type="button" data-check-name="' + escapeHtml(check.name) + '">',
        '  <span class="status-mark">' + (check.ok ? "OK" : "!") + "</span>",
        "  <span>",
        "    <h3>" + escapeHtml(titleize(check.name)) + "</h3>",
        "    <p>" + escapeHtml(checkSummary(check)) + "</p>",
        "  </span>",
        '  <span class="duration-pill">' + escapeHtml(check.durationMs ?? "-") + " ms</span>",
        "</button>",
      ].join("");
    }).join("");

    const selected = checks.find(function (check) { return check.name === state.selectedName; }) || checks[0];
    renderDetail(selected);
  }

  function renderDetail(check) {
    if (!check) {
      els.detailTitle.textContent = "Select A Check";
      els.detailBody.textContent = "";
      return;
    }
    state.selectedName = check.name;
    els.detailTitle.textContent = titleize(check.name);
    els.detailBody.textContent = JSON.stringify(check, null, 2);
    Array.from(document.querySelectorAll(".check-card")).forEach(function (item) {
      item.classList.toggle("active", item.dataset.checkName === check.name);
    });
  }

  function renderErrors(container, errors) {
    const rows = Array.isArray(errors) ? errors : [];
    if (!rows.length) {
      container.innerHTML = '<div class="empty-state">No recent errors recorded.</div>';
      return;
    }

    container.innerHTML = rows.slice(0, 8).map(function (item) {
      const path = item.path || item.requestedPath || "";
      return [
        '<div class="error-item">',
        "  <strong>" + escapeHtml(item.message || "Route error") + "</strong>",
        '  <div class="muted">' + escapeHtml(prettyTime(item.at)) + "</div>",
        path ? "  <code>" + escapeHtml(path) + "</code>" : "",
        item.exception ? "  <div><code>" + escapeHtml(item.exception) + "</code></div>" : "",
        "</div>",
      ].join("");
    }).join("");
  }

  async function refresh() {
    els.refreshBtn.disabled = true;
    try {
      const payload = await apiGet("/admin/api/full-health");
      renderSummary(payload);
      renderChecks(payload);
      renderErrors(els.transferErrors, payload.debug && payload.debug.recentTransferErrors);
      renderErrors(els.assetErrors, payload.debug && payload.debug.recentAssetErrors);
    } catch (error) {
      els.overallStatus.textContent = "Locked";
      els.checkedAt.textContent = error.message;
      els.checkList.innerHTML = '<div class="error-state">' + escapeHtml(error.message) + "</div>";
      els.detailBody.textContent = "";
      renderErrors(els.transferErrors, []);
      renderErrors(els.assetErrors, []);
      showToast(error.message);
    } finally {
      els.refreshBtn.disabled = false;
    }
  }

  els.saveTokenBtn.addEventListener("click", function () {
    localStorage.setItem("saovsAdminToken", adminToken());
    showToast("Token saved.");
    refresh();
  });

  els.refreshBtn.addEventListener("click", function () {
    refresh();
  });

  els.checkList.addEventListener("click", function (event) {
    const card = event.target.closest("[data-check-name]");
    if (!card) return;
    const check = state.checks.find(function (item) { return item.name === card.dataset.checkName; });
    renderDetail(check);
  });

  refresh();
})();
