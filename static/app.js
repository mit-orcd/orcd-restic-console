async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || "Request failed");
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function reloadSoon() {
  setTimeout(() => window.location.reload(), 800);
}

document.addEventListener("click", async (event) => {
  const action = event.target.dataset.action;
  if (!action) return;

  const row = event.target.closest("tr");
  const fsId = row?.dataset.fsId;
  const destId = row?.dataset.destId;

  try {
    if (action === "backup") {
      await request(`/api/backup/${fsId}`, { method: "POST", body: "{}" });
      reloadSoon();
    }
    if (action === "restore") {
      const snapshotId = row.querySelector("input[name='snapshot_id']").value;
      const targetPath = row.querySelector("input[name='target_path']").value;
      await request(`/api/restore/${fsId}`, {
        method: "POST",
        body: JSON.stringify({ snapshot_id: snapshotId, target_path: targetPath }),
      });
      reloadSoon();
    }
    if (action === "snapshots") {
      const data = await request(`/api/snapshots/${fsId}`);
      document.getElementById("snapshots-output").textContent = JSON.stringify(data, null, 2);
    }
    if (action === "delete-fs") {
      await request(`/api/filesystems/${fsId}`, { method: "DELETE" });
      reloadSoon();
    }
    if (action === "delete-dest") {
      await request(`/api/destinations/${destId}`, { method: "DELETE" });
      reloadSoon();
    }
  } catch (err) {
    alert(err.message);
  }
});

document.getElementById("add-fs")?.addEventListener("click", async () => {
  const payload = {
    id: document.getElementById("fs-id").value.trim(),
    name: document.getElementById("fs-name").value.trim(),
    source_path: document.getElementById("fs-source").value.trim(),
    destination_id: document.getElementById("fs-destination").value.trim(),
    repo_suffix: document.getElementById("fs-repo-suffix").value.trim() || undefined,
  };
  try {
    await request("/api/filesystems", { method: "POST", body: JSON.stringify(payload) });
    reloadSoon();
  } catch (err) {
    alert(err.message);
  }
});

document.getElementById("add-dest")?.addEventListener("click", async () => {
  const type = document.getElementById("dest-type").value;
  const payload = {
    id: document.getElementById("dest-id").value.trim(),
    name: document.getElementById("dest-name").value.trim(),
    type,
    path: document.getElementById("dest-path").value.trim(),
    bucket: document.getElementById("dest-bucket").value.trim(),
    prefix: document.getElementById("dest-prefix").value.trim(),
    endpoint: document.getElementById("dest-endpoint").value.trim(),
    region: document.getElementById("dest-region").value.trim(),
  };
  try {
    await request("/api/destinations", { method: "POST", body: JSON.stringify(payload) });
    reloadSoon();
  } catch (err) {
    alert(err.message);
  }
});

document.getElementById("create-bucket")?.addEventListener("click", async () => {
  const name = document.getElementById("dest-bucket").value.trim();
  const region = document.getElementById("dest-region").value.trim();
  const endpoint = document.getElementById("dest-endpoint").value.trim();
  if (!name) {
    alert("Bucket name is required");
    return;
  }
  try {
    await request("/api/s3/buckets", {
      method: "POST",
      body: JSON.stringify({ name, region, endpoint }),
    });
    alert("Bucket created");
  } catch (err) {
    alert(err.message);
  }
});

// ——— Restore panel (recovery) ———
const restoreFrom = document.getElementById("restore-from");
const restoreRepo = document.getElementById("restore-repo");
const restoreSnapshot = document.getElementById("restore-snapshot");
const restoreTarget = document.getElementById("restore-target");
const restoreFilterMode = document.getElementById("restore-filter-mode");
const restoreLoadFiles = document.getElementById("restore-load-files");
const restoreFileListContainer = document.getElementById("restore-file-list-container");
const restoreFileList = document.getElementById("restore-file-list");
const restoreLsCommandWrap = document.getElementById("restore-ls-command-wrap");
const restoreLsCommand = document.getElementById("restore-ls-command");

function setRestoreLsCommand(command) {
  if (!restoreLsCommandWrap || !restoreLsCommand) return;
  if (command) {
    restoreLsCommand.textContent = command;
    restoreLsCommandWrap.style.display = "block";
  } else {
    restoreLsCommand.textContent = "";
    restoreLsCommandWrap.style.display = "none";
  }
}
const restoreIncludeExclude = document.getElementById("restore-include-exclude");
const restoreIncludePaths = document.getElementById("restore-include-paths");
const restoreExcludePaths = document.getElementById("restore-exclude-paths");
const restoreVerifySummary = document.getElementById("restore-verify-summary");
const restoreVerifyBtn = document.getElementById("restore-verify");
const restoreRunBtn = document.getElementById("restore-run");
const restoreCancelBtn = document.getElementById("restore-cancel");
const restoreRepoType = document.getElementById("restore-repo-type");

let recoveryRootPath = "";

function getDefaultRestoreTargetBase() {
  return restoreTarget.value.trim() || restoreTarget.placeholder || "/tmp";
}

function getEffectiveRepo() {
  const typed = restoreRepoType?.value.trim();
  if (typed) {
    const base = recoveryRootPath.replace(/\/+$/, "");
    return base ? `${base}/${typed}` : "";
  }
  return restoreRepo?.value || "";
}

restoreFrom?.addEventListener("change", async () => {
  const root = restoreFrom.value;
  restoreRepo.innerHTML = "<option value=\"\">— Select repository —</option>";
  restoreRepoType.value = "";
  restoreSnapshot.innerHTML = "<option value=\"\">— Select snapshot —</option>";
  restoreTarget.value = restoreTarget.placeholder || "/tmp";
  restoreVerifySummary.style.display = "none";
  recoveryRootPath = "";
  if (!root) return;
  try {
    const data = await request(`/api/recovery/repos?root=${encodeURIComponent(root)}`);
    recoveryRootPath = data.root || "";
    (data.repos || data).forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.path;
      opt.textContent = r.name;
      restoreRepo.appendChild(opt);
    });
  } catch (err) {
    alert(err.message);
  }
});

function updateTargetFromRepo(repoPath) {
  const base = getDefaultRestoreTargetBase().replace(/\/+$/, "");
  const repoName = repoPath ? repoPath.split("/").filter(Boolean).pop() : "";
  restoreTarget.value = repoName ? `${base}/${repoName}` : base;
}

restoreRepo?.addEventListener("change", () => {
  if (restoreRepoType?.value.trim()) return;
  const repo = restoreRepo.value;
  restoreTarget.value = restoreTarget.placeholder || "/tmp";
  updateTargetFromRepo(repo);
  restoreSnapshot.innerHTML = "<option value=\"\">— Select snapshot —</option>";
  restoreFileListContainer.style.display = "none";
  restoreFileList.innerHTML = "";
  setRestoreLsCommand("");
  restoreVerifySummary.style.display = "none";
  if (!repo) return;
  loadSnapshotsForRepo(repo);
});

restoreRepoType?.addEventListener("blur", () => {
  const repo = getEffectiveRepo();
  if (!repo) return;
  updateTargetFromRepo(repo);
});

restoreRepoType?.addEventListener("input", () => {
  restoreTarget.value = restoreTarget.placeholder || "/tmp";
  restoreSnapshot.innerHTML = "<option value=\"\">— Select snapshot —</option>";
  restoreVerifySummary.style.display = "none";
});

let snapshotLoadInProgress = false;

async function loadSnapshotsForRepo(repo) {
  if (!restoreSnapshot) return;
  if (snapshotLoadInProgress) return;
  snapshotLoadInProgress = true;
  restoreSnapshot.innerHTML = "<option value=\"\">— Select snapshot —</option>";
  try {
    const data = await request(`/api/recovery/snapshots?repo=${encodeURIComponent(repo)}`);
    const raw = Array.isArray(data) ? data : (data.snapshots || []);
    const seen = new Set();
    const list = raw.filter((s) => {
      const id = s.id || s.short_id;
      if (!id || id === "latest" || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
    list.sort((a, b) => (b.time || "").localeCompare(a.time || ""));
    const latest = document.createElement("option");
    latest.value = "latest";
    latest.textContent = "latest";
    restoreSnapshot.appendChild(latest);
    list.forEach((s) => {
      const id = s.id || s.short_id;
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = `${id} (${s.time || ""})`;
      restoreSnapshot.appendChild(opt);
    });
  } catch (err) {
    alert(err.message);
  } finally {
    snapshotLoadInProgress = false;
  }
}

document.getElementById("restore-snapshot")?.addEventListener("focus", () => {
  const repo = getEffectiveRepo();
  if (repo && restoreSnapshot.options.length <= 1 && !snapshotLoadInProgress) loadSnapshotsForRepo(repo);
});

restoreFilterMode?.addEventListener("change", () => {
  const mode = restoreFilterMode.value;
  restoreIncludeExclude.style.display = mode === "none" ? "none" : "grid";
  if (mode === "none") {
    restoreFileListContainer.style.display = "none";
    setRestoreLsCommand("");
  }
});

function renderFileList(paths, searchTerm) {
  const filtered = searchTerm
    ? paths.filter((p) => p.toLowerCase().includes(searchTerm.toLowerCase()))
    : paths;
  restoreFileList.innerHTML = "";
  if (searchTerm && filtered.length === 0) {
    const msg = document.createElement("div");
    msg.className = "file-list-message";
    msg.textContent = "No paths matching \"" + searchTerm.replace(/</g, "&lt;") + "\"";
    restoreFileList.appendChild(msg);
    return;
  }
  if (searchTerm && paths.length > 0) {
    const summary = document.createElement("div");
    summary.className = "file-list-summary";
    summary.textContent = "Showing " + filtered.length + " of " + paths.length + " paths";
    restoreFileList.appendChild(summary);
  }
  filtered.forEach((p) => {
    const line = document.createElement("div");
    line.className = "file-line";
    line.textContent = p;
    line.title = "Click to copy path";
    line.addEventListener("click", () => {
      navigator.clipboard.writeText(p).then(() => {
        line.classList.add("copied");
        setTimeout(() => line.classList.remove("copied"), 500);
      });
    });
    restoreFileList.appendChild(line);
  });
}

restoreLoadFiles?.addEventListener("click", async () => {
  const repo = getEffectiveRepo();
  const snapshot = restoreSnapshot.value;
  if (!repo || !snapshot) {
    alert("Select or type repository and select snapshot first.");
    return;
  }
  const searchTerm = document.getElementById("restore-file-search")?.value?.trim() || "";
  restoreFileList.innerHTML = "<span class=\"loading\">Loading…</span>";
  setRestoreLsCommand("");
  restoreFileListContainer.style.display = "block";
  try {
    const data = await request(
      `/api/recovery/ls?repo=${encodeURIComponent(repo)}&snapshot=${encodeURIComponent(snapshot)}`
    );
    const paths = data.paths || [];
    setRestoreLsCommand(data.command || "");
    renderFileList(paths, searchTerm);
  } catch (err) {
    setRestoreLsCommand("");
    restoreFileList.innerHTML = "";
    const errEl = document.createElement("div");
    errEl.className = "error";
    errEl.textContent = err.message;
    restoreFileList.appendChild(errEl);
  }
});

restoreVerifyBtn?.addEventListener("click", async () => {
  const repo = getEffectiveRepo();
  const snapshot = restoreSnapshot.value;
  const targetPath = restoreTarget.value.trim();
  if (!repo || !snapshot || !targetPath) {
    alert("Select or type repository, snapshot, and target directory.");
    return;
  }
  const includePaths = restoreIncludePaths.value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  const excludePaths = restoreExcludePaths.value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  try {
    const result = await request("/api/recovery/verify", {
      method: "POST",
      body: JSON.stringify({
        repo,
        snapshot_id: snapshot,
        target_path: targetPath,
        include_paths: includePaths,
        exclude_paths: excludePaths,
      }),
    });
    restoreVerifySummary.style.display = "block";
    restoreVerifySummary.className = "verify-summary";
    restoreVerifySummary.innerHTML = [
      "<strong>Verify</strong>",
      "<p>" + (result.summary || "").replace(/</g, "&lt;") + "</p>",
      result.include_paths?.length
        ? "<p>Include (" + result.include_paths.length + "): " + result.include_paths.join(", ").replace(/</g, "&lt;") + "</p>"
        : "",
      result.exclude_paths?.length
        ? "<p>Exclude (" + result.exclude_paths.length + "): " + result.exclude_paths.join(", ").replace(/</g, "&lt;") + "</p>"
        : "",
    ].join("");
  } catch (err) {
    restoreVerifySummary.style.display = "block";
    restoreVerifySummary.className = "verify-summary error";
    restoreVerifySummary.textContent = err.message;
  }
});

restoreRunBtn?.addEventListener("click", async () => {
  const repo = getEffectiveRepo();
  const snapshot = restoreSnapshot.value;
  const targetPath = restoreTarget.value.trim();
  if (!repo || !snapshot || !targetPath) {
    alert("Select or type repository, snapshot, and target directory.");
    return;
  }
  const includePaths = restoreIncludePaths.value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  const excludePaths = restoreExcludePaths.value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  try {
    const result = await request("/api/recovery/restore", {
      method: "POST",
      body: JSON.stringify({
        repo,
        snapshot_id: snapshot,
        target_path: targetPath,
        include_paths: includePaths,
        exclude_paths: excludePaths,
      }),
    });
    alert("Restore job started. Job ID: " + (result.job_id || "—"));
    if (restoreVerifySummary.style.display === "block") {
      restoreVerifySummary.innerHTML += "<p><em>Restore job submitted.</em></p>";
    }
  } catch (err) {
    alert(err.message);
  }
});

restoreCancelBtn?.addEventListener("click", () => {
  if (restoreFrom?.options?.length > 1) restoreFrom.value = restoreFrom.options[1].value;
  else restoreFrom.value = "";
  restoreRepo.innerHTML = "<option value=\"\">— Select repository —</option>";
  restoreRepoType.value = "";
  restoreSnapshot.innerHTML = "<option value=\"\">— Select snapshot —</option>";
  restoreTarget.value = restoreTarget.placeholder || "/tmp";
  restoreFilterMode.value = "none";
  restoreIncludeExclude.style.display = "none";
  restoreIncludePaths.value = "";
  restoreExcludePaths.value = "";
  restoreFileListContainer.style.display = "none";
  restoreFileList.innerHTML = "";
  setRestoreLsCommand("");
  restoreVerifySummary.style.display = "none";
  restoreVerifySummary.innerHTML = "";
  restoreFrom.dispatchEvent(new Event("change"));
  window.location.reload();
});

// Load recovery roots for "Restore from" dropdown, then load repos for first option
(async function loadRestoreFromOptions() {
  if (!restoreFrom) return;
  try {
    const roots = await request("/api/recovery/roots");
    restoreFrom.innerHTML = "<option value=\"\">— Select source —</option>";
    (roots || []).forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.key;
      opt.textContent = r.name;
      restoreFrom.appendChild(opt);
    });
    if (roots && roots.length > 0) {
      restoreFrom.value = roots[0].key;
      restoreFrom.dispatchEvent(new Event("change"));
    }
  } catch (err) {
    restoreFrom.innerHTML = "<option value=\"\">— Failed to load —</option>";
    console.error(err);
  }
})();

// Theme toggle (light / dark panel)
(function () {
  const STORAGE_KEY = "backup-panel-theme";
  const DARK = "dark";
  const LIGHT = "light";

  function getStored() {
    try {
      return localStorage.getItem(STORAGE_KEY) || LIGHT;
    } catch (e) {
      return LIGHT;
    }
  }

  function applyTheme(theme) {
    document.body.classList.toggle("theme-dark", theme === DARK);
  }

  function setTheme(theme) {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch (e) {}
    applyTheme(theme);
  }

  const btn = document.getElementById("theme-toggle");
  if (btn) {
    applyTheme(getStored());
    btn.addEventListener("click", function () {
      const next = document.body.classList.contains("theme-dark") ? LIGHT : DARK;
      setTheme(next);
    });
  }
})();
