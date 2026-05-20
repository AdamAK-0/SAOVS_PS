(function () {
  const els = {
    loginView: document.getElementById("loginView"),
    appView: document.getElementById("appView"),
    loginForm: document.getElementById("loginForm"),
    emailInput: document.getElementById("emailInput"),
    passwordInput: document.getElementById("passwordInput"),
    loginError: document.getElementById("loginError"),
    accountName: document.getElementById("accountName"),
    accountId: document.getElementById("accountId"),
    logoutBtn: document.getElementById("logoutBtn"),
    ownedCount: document.getElementById("ownedCount"),
    copyCount: document.getElementById("copyCount"),
    catalogCount: document.getElementById("catalogCount"),
    searchInput: document.getElementById("searchInput"),
    rarityFilter: document.getElementById("rarityFilter"),
    ownedOnlyInput: document.getElementById("ownedOnlyInput"),
    ownedMeta: document.getElementById("ownedMeta"),
    ownedList: document.getElementById("ownedList"),
    catalogMeta: document.getElementById("catalogMeta"),
    catalogGrid: document.getElementById("catalogGrid"),
    editorImage: document.getElementById("editorImage"),
    editorCode: document.getElementById("editorCode"),
    editorTitle: document.getElementById("editorTitle"),
    editorMeta: document.getElementById("editorMeta"),
    editorForm: document.getElementById("editorForm"),
    levelInput: document.getElementById("levelInput"),
    potentialInput: document.getElementById("potentialInput"),
    copiesInput: document.getElementById("copiesInput"),
    copiesLabel: document.getElementById("copiesLabel"),
    lockedInput: document.getElementById("lockedInput"),
    saveBtn: document.getElementById("saveBtn"),
    removeBtn: document.getElementById("removeBtn"),
    editorMessage: document.getElementById("editorMessage"),
    toast: document.getElementById("toast"),
  };

  const state = {
    account: null,
    catalog: [],
    owned: [],
    selectedMode: "",
    selectedCode: 0,
    selectedGroupId: 0,
    limits: { maxCopies: 99, maxPotential: 5 },
    toastTimer: 0,
  };

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
    }, 2400);
  }

  function titleFor(item) {
    const name = item.name || `Ability ${item.code}`;
    return item.character ? `${item.character} - ${name}` : name;
  }

  function ownedGroupsForCode(code) {
    return state.owned.filter(function (item) {
      return Number(item.code) === Number(code);
    });
  }

  function ownedCopiesByCode() {
    const map = new Map();
    state.owned.forEach(function (item) {
      const code = Number(item.code);
      map.set(code, (map.get(code) || 0) + Number(item.copies || 0));
    });
    return map;
  }

  function selectedOwnedGroup() {
    return state.owned.find(function (item) {
      return Number(item.groupId) === state.selectedGroupId;
    }) || null;
  }

  function selectedCatalogItem() {
    return state.catalog.find(function (item) {
      return Number(item.code) === state.selectedCode;
    }) || null;
  }

  function selectedItem() {
    if (state.selectedMode === "owned") {
      return selectedOwnedGroup();
    }
    if (state.selectedMode === "catalog") {
      return selectedCatalogItem();
    }
    return null;
  }

  async function fetchJson(path, options) {
    const response = await fetch(path, Object.assign({ credentials: "same-origin" }, options || {}));
    const data = await response.json().catch(function () {
      return {};
    });
    if (!response.ok) {
      const error = new Error(data.error || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function showLogin() {
    els.loginView.classList.remove("hidden");
    els.appView.classList.add("hidden");
  }

  function showApp() {
    els.loginView.classList.add("hidden");
    els.appView.classList.remove("hidden");
  }

  function applyAccount(account) {
    state.account = account;
    els.accountName.textContent = account.userName || account.displayName || account.email;
    els.accountId.textContent = `ID ${account.userId}`;
  }

  async function loadMe() {
    const data = await fetchJson("/customize/api/me");
    if (!data.authenticated) {
      showLogin();
      return false;
    }
    applyAccount(data.account);
    showApp();
    return true;
  }

  async function loadAbilities() {
    try {
      const data = await fetchJson("/customize/api/abilities");
      applyAccount(data.account);
      state.catalog = data.catalog || [];
      state.owned = data.owned || [];
      state.limits = data.limits || state.limits;

      if (state.selectedMode === "owned" && !selectedOwnedGroup()) {
        state.selectedMode = "";
        state.selectedGroupId = 0;
      }
      if (!state.selectedMode && state.owned.length) {
        state.selectedMode = "owned";
        state.selectedGroupId = Number(state.owned[0].groupId);
        state.selectedCode = Number(state.owned[0].code);
      }
      renderAll();
    } catch (error) {
      if (error.status === 401) {
        showLogin();
        return;
      }
      showToast(error.message);
    }
  }

  function selectOwned(groupId) {
    const item = state.owned.find(function (ownedItem) {
      return Number(ownedItem.groupId) === Number(groupId);
    });
    if (!item) {
      return;
    }
    state.selectedMode = "owned";
    state.selectedGroupId = Number(groupId);
    state.selectedCode = Number(item.code);
    renderAll();
  }

  function selectCatalog(code) {
    state.selectedMode = "catalog";
    state.selectedGroupId = 0;
    state.selectedCode = Number(code);
    renderAll();
  }

  function resetEditor() {
    els.editorImage.removeAttribute("src");
    els.editorImage.classList.add("hidden");
    els.editorCode.textContent = "Select Card";
    els.editorTitle.textContent = "No card selected";
    els.editorMeta.textContent = "-";
    els.copiesLabel.textContent = "Copies";
    els.saveBtn.textContent = "Save";
    els.removeBtn.textContent = "Delete Group";
    [els.levelInput, els.potentialInput, els.copiesInput, els.lockedInput, els.saveBtn].forEach(function (input) {
      input.disabled = true;
    });
    els.removeBtn.disabled = true;
    els.editorMessage.textContent = "";
  }

  function renderEditor() {
    const item = selectedItem();
    if (!item) {
      resetEditor();
      return;
    }

    const isOwned = state.selectedMode === "owned";
    const isEditable = !isOwned || item.isEditable !== false;
    const maxLevel = Number(item.maxLevel || 55);
    els.editorImage.classList.remove("hidden");
    els.editorImage.src = item.image || `/customize/ability-image/${item.code}`;
    els.editorCode.textContent = `Code ${item.code} - Game ID ${item.gameId || "-"}`;
    els.editorTitle.textContent = titleFor(item);
    els.editorMeta.textContent = isOwned
      ? `${item.rarity || "-"} Star - group ${item.groupId} - ${item.copies} owned copies${isEditable ? "" : " - game default"}`
      : `${item.rarity || "-"} Star - Max level ${maxLevel} - ${ownedGroupsForCode(item.code).length} owned groups`;
    els.levelInput.max = String(maxLevel);
    els.levelInput.value = String(isOwned ? item.level : maxLevel);
    els.potentialInput.max = String(state.limits.maxPotential || 5);
    els.potentialInput.value = String(isOwned ? item.potential : state.limits.maxPotential || 5);
    els.copiesInput.max = String(isOwned ? item.copies : state.limits.maxCopies || 99);
    els.copiesInput.value = String(isOwned ? item.copies : 1);
    els.copiesLabel.textContent = isOwned ? "Copies To Edit" : "Copies To Add";
    els.lockedInput.checked = isOwned ? Boolean(item.isLocked) : true;
    els.saveBtn.textContent = isOwned ? (isEditable ? "Apply To Owned" : "Game Default") : "Add Copies";
    els.removeBtn.textContent = isEditable ? "Delete Group" : "Locked";
    [els.levelInput, els.potentialInput, els.copiesInput, els.lockedInput, els.saveBtn].forEach(function (input) {
      input.disabled = !isEditable;
    });
    els.removeBtn.disabled = !isOwned || !isEditable;
    els.editorMessage.textContent = isEditable ? "" : "This group is supplied by the game.";
  }

  function renderSummary() {
    const copiesByCode = ownedCopiesByCode();
    const totalCopies = state.owned.reduce(function (sum, item) {
      return sum + Number(item.copies || 0);
    }, 0);
    const editableCopies = state.owned.reduce(function (sum, item) {
      return item.isEditable === false ? sum : sum + Number(item.copies || 0);
    }, 0);
    els.ownedCount.textContent = String(totalCopies);
    els.copyCount.textContent = String(state.owned.length);
    els.catalogCount.textContent = String(state.catalog.length);
    els.ownedMeta.textContent = `${state.owned.length} groups / ${copiesByCode.size} codes / ${totalCopies} copies / ${editableCopies} editable`;
  }

  function renderOwned() {
    if (!state.owned.length) {
      els.ownedList.innerHTML = '<div class="owned-row empty-row"><div></div><div class="owned-title">No cards owned</div></div>';
      return;
    }

    els.ownedList.innerHTML = state.owned.map(function (item) {
      const selected = Number(item.groupId) === state.selectedGroupId && state.selectedMode === "owned" ? " selected" : "";
      const systemClass = item.isEditable === false ? " system" : "";
      const badge = item.isEditable === false ? '<span class="system-badge">Default</span>' : "";
      return `
        <article class="owned-row${selected}${systemClass}" data-mode="owned" data-group-id="${item.groupId}">
          <img loading="lazy" src="${escapeHtml(item.image)}" alt="">
          <div class="owned-main">
            <div class="owned-title" title="${escapeHtml(titleFor(item))}">${escapeHtml(titleFor(item))}</div>
            <div class="owned-sub">Code ${item.code} - Game ID ${escapeHtml(item.gameId || "-")} - Group ${item.groupId} ${badge}</div>
          </div>
          <div class="mini-stats">
            <span>L${item.level}</span>
            <span>P${item.potential}</span>
            <span>x${item.copies}</span>
            <button type="button" data-mode="owned" data-group-id="${item.groupId}">${item.isEditable === false ? "View" : "Edit"}</button>
          </div>
        </article>`;
    }).join("");
  }

  function filteredCatalog() {
    const query = els.searchInput.value.trim().toLowerCase();
    const rarity = els.rarityFilter.value;
    const onlyOwned = els.ownedOnlyInput.checked;
    const copiesByCode = ownedCopiesByCode();
    return state.catalog.filter(function (item) {
      if (rarity && String(item.rarity || "") !== rarity) {
        return false;
      }
      if (onlyOwned && !copiesByCode.has(Number(item.code))) {
        return false;
      }
      if (!query) {
        return true;
      }
      const haystack = `${item.code} ${item.gameId || ""} ${item.name || ""} ${item.character || ""}`.toLowerCase();
      return haystack.includes(query);
    });
  }

  function renderCatalog() {
    const copiesByCode = ownedCopiesByCode();
    const items = filteredCatalog();
    els.catalogMeta.textContent = `${items.length} shown`;
    els.catalogGrid.innerHTML = items.map(function (item) {
      const selected = Number(item.code) === state.selectedCode && state.selectedMode === "catalog" ? " selected" : "";
      const ownedCopies = copiesByCode.get(Number(item.code)) || 0;
      const copies = ownedCopies ? `<span>x${ownedCopies}</span>` : "";
      return `
        <article class="card${selected}" data-mode="catalog" data-code="${item.code}">
          <img loading="lazy" src="${escapeHtml(item.image)}" alt="">
          <div class="card-head">
            <span class="rarity">${escapeHtml(item.rarity || "-")} Star</span>
            ${copies}
          </div>
          <div class="card-text">
            <div class="card-title" title="${escapeHtml(titleFor(item))}">${escapeHtml(titleFor(item))}</div>
            <div class="card-sub">Code ${item.code} - Game ID ${escapeHtml(item.gameId || "-")}</div>
          </div>
          <button type="button" data-mode="catalog" data-code="${item.code}">Add</button>
        </article>`;
    }).join("");
  }

  function renderAll() {
    renderSummary();
    renderOwned();
    renderCatalog();
    renderEditor();
  }

  async function saveSelected(event) {
    event.preventDefault();
    const item = selectedItem();
    if (!item) {
      return;
    }

    const isOwned = state.selectedMode === "owned";
    if (isOwned && item.isEditable === false) {
      els.editorMessage.textContent = "This group is supplied by the game.";
      return;
    }
    const path = isOwned
      ? `/customize/api/ability-groups/${item.groupId}`
      : `/customize/api/abilities/${item.code}`;
    els.saveBtn.disabled = true;
    try {
      const data = await fetchJson(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          level: Number(els.levelInput.value),
          potential: Number(els.potentialInput.value),
          copies: Number(els.copiesInput.value),
          isLocked: els.lockedInput.checked,
        }),
      });
      if (data.item && data.item.groupId) {
        state.selectedMode = "owned";
        state.selectedGroupId = Number(data.item.groupId);
        state.selectedCode = Number(data.item.code);
      }
      els.editorMessage.textContent = isOwned ? "Updated" : "Added";
      showToast(isOwned ? "Owned ability updated" : "Ability copies added");
      await loadAbilities();
    } catch (error) {
      els.editorMessage.textContent = error.message;
    } finally {
      els.saveBtn.disabled = false;
    }
  }

  async function removeSelected() {
    const item = selectedOwnedGroup();
    if (!item) {
      return;
    }
    if (item.isEditable === false) {
      els.editorMessage.textContent = "This group is supplied by the game.";
      return;
    }

    els.removeBtn.disabled = true;
    try {
      await fetchJson(`/customize/api/ability-groups/${item.groupId}`, { method: "DELETE" });
      state.selectedMode = "";
      state.selectedGroupId = 0;
      state.selectedCode = 0;
      showToast("Owned ability group deleted");
      await loadAbilities();
    } catch (error) {
      els.editorMessage.textContent = error.message;
    } finally {
      els.removeBtn.disabled = state.selectedMode !== "owned";
    }
  }

  els.loginForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    els.loginError.textContent = "";
    const body = new URLSearchParams(new FormData(els.loginForm));
    try {
      const data = await fetchJson("/customize/api/login", {
        method: "POST",
        body,
      });
      applyAccount(data.account);
      showApp();
      await loadAbilities();
    } catch (error) {
      els.loginError.textContent = error.message;
    }
  });

  els.logoutBtn.addEventListener("click", async function () {
    await fetchJson("/customize/api/logout", { method: "POST" }).catch(function () {});
    state.account = null;
    state.catalog = [];
    state.owned = [];
    state.selectedMode = "";
    state.selectedCode = 0;
    state.selectedGroupId = 0;
    showLogin();
  });

  els.editorForm.addEventListener("submit", saveSelected);
  els.removeBtn.addEventListener("click", removeSelected);

  [els.searchInput, els.rarityFilter, els.ownedOnlyInput].forEach(function (input) {
    input.addEventListener("input", renderCatalog);
    input.addEventListener("change", renderCatalog);
  });

  document.addEventListener("click", function (event) {
    const target = event.target.closest("[data-mode]");
    if (!target) {
      return;
    }
    if (target.getAttribute("data-mode") === "owned") {
      selectOwned(target.getAttribute("data-group-id"));
      return;
    }
    selectCatalog(target.getAttribute("data-code"));
  });

  resetEditor();
  loadMe().then(function (authenticated) {
    if (authenticated) {
      loadAbilities();
    }
  }).catch(function () {
    showLogin();
  });
})();
