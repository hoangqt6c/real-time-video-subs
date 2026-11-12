(() => {
  const STORAGE_KEY = "rtdub_allowed_domains";
  const SERVER = "http://127.0.0.1:8765";

  const serverBadge = document.getElementById("serverBadge");
  const currentHostEl = document.getElementById("currentHost");
  const toggleSiteBtn = document.getElementById("toggleSiteBtn");
  const domainInput = document.getElementById("domainInput");
  const addDomainBtn = document.getElementById("addDomainBtn");
  const domainListEl = document.getElementById("domainList");
  const domainCountEl = document.getElementById("domainCount");

  let currentHost = "";
  let allowedDomains = [];

  function normalizeDomain(input) {
    if (!input) return "";
    let s = String(input).trim().toLowerCase();
    try {
      if (s.includes("://")) {
        s = new URL(s).hostname;
      } else {
        s = s.split("/")[0].split(":")[0];
      }
    } catch (_) {}
    s = s.replace(/^\*\./, "").replace(/^www\./, "").trim();
    return s;
  }

  function isHostAllowed(host, list) {
    const clean = normalizeDomain(host);
    if (!clean) return false;
    return list.some((d) => {
      const rule = normalizeDomain(d);
      return rule && (clean === rule || clean.endsWith("." + rule));
    });
  }

  function findMatchingRule(host, list) {
    const clean = normalizeDomain(host);
    return list.find((d) => {
      const rule = normalizeDomain(d);
      return rule && (clean === rule || clean.endsWith("." + rule));
    });
  }

  function saveDomains(nextList) {
    const unique = [...new Set(nextList.map(normalizeDomain).filter(Boolean))];
    allowedDomains = unique;
    chrome.storage.local.set({ [STORAGE_KEY]: unique }, () => {
      render();
    });
  }

  function render() {
    domainCountEl.textContent = String(allowedDomains.length);

    if (currentHost) {
      currentHostEl.textContent = currentHost;
      toggleSiteBtn.disabled = false;
      const enabled = isHostAllowed(currentHost, allowedDomains);
      if (enabled) {
        toggleSiteBtn.className = "toggle-btn toggle-btn-on";
        toggleSiteBtn.textContent = "✓ Đang bật trên trang này (Bấm để tắt)";
      } else {
        toggleSiteBtn.className = "toggle-btn toggle-btn-off";
        toggleSiteBtn.textContent = "+ Bật phụ đề trên " + currentHost;
      }
    } else {
      currentHostEl.textContent = "Tab hệ thống / Không phải trang web";
      toggleSiteBtn.disabled = true;
      toggleSiteBtn.className = "toggle-btn toggle-btn-off";
      toggleSiteBtn.textContent = "Không khả dụng trên tab này";
    }

    domainListEl.innerHTML = "";
    if (allowedDomains.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-msg";
      empty.textContent = "Chưa có trang web nào. Hãy bấm nút Bật ở trên hoặc nhập tên miền.";
      domainListEl.appendChild(empty);
      return;
    }

    for (const domain of allowedDomains) {
      const row = document.createElement("div");
      row.className = "domain-item";

      const nameSpan = document.createElement("span");
      nameSpan.textContent = domain;

      const delBtn = document.createElement("button");
      delBtn.className = "domain-remove";
      delBtn.title = "Xóa trang này";
      delBtn.textContent = "✕";
      delBtn.addEventListener("click", () => {
        saveDomains(allowedDomains.filter((d) => d !== domain));
      });

      row.append(nameSpan, delBtn);
      domainListEl.appendChild(row);
    }
  }

  toggleSiteBtn.addEventListener("click", () => {
    if (!currentHost) return;
    const matched = findMatchingRule(currentHost, allowedDomains);
    if (matched) {
      saveDomains(allowedDomains.filter((d) => d !== matched));
    } else {
      saveDomains([currentHost, ...allowedDomains]);
    }
  });

  function handleAddInput() {
    const val = normalizeDomain(domainInput.value);
    if (!val || !val.includes(".")) return;
    domainInput.value = "";
    saveDomains([val, ...allowedDomains]);
  }

  addDomainBtn.addEventListener("click", handleAddInput);
  domainInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") handleAddInput();
  });

  // Load active tab hostname
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0];
    if (tab && tab.url && /^https?:\/\//i.test(tab.url)) {
      currentHost = normalizeDomain(tab.url);
    }
    chrome.storage.local.get({ [STORAGE_KEY]: [] }, (res) => {
      allowedDomains = Array.isArray(res[STORAGE_KEY]) ? res[STORAGE_KEY].map(normalizeDomain).filter(Boolean) : [];
      render();
    });
  });

  // Check local server health
  fetch(`${SERVER}/health`)
    .then((r) => {
      if (!r.ok) throw new Error();
      serverBadge.className = "server-badge server-online";
      serverBadge.textContent = "● Server đang chạy";
    })
    .catch(() => {
      serverBadge.className = "server-badge server-offline";
      serverBadge.textContent = "○ Server chưa bật";
    });
})();
