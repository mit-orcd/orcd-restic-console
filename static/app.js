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
  updateTargetFromRepo(repo);
  restoreSnapshot.innerHTML = "<option value=\"\">— Select snapshot —</option>";
  restoreFileListContainer.style.display = "none";
  restoreFileList.innerHTML = "";
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
  restoreSnapshot.innerHTML = "<option value=\"\">— Select snapshot —</option>";
  restoreVerifySummary.style.display = "none";
});

async function loadSnapshotsForRepo(repo) {
  try {
    const data = await request(`/api/recovery/snapshots?repo=${encodeURIComponent(repo)}`);
    const list = Array.isArray(data) ? data : (data.snapshots || []);
    const latest = document.createElement("option");
    latest.value = "latest";
    latest.textContent = "latest";
    restoreSnapshot.appendChild(latest);
    list.forEach((s) => {
      const id = s.id || s.short_id;
      if (!id || id === "latest") return;
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = `${id} (${s.time || ""})`;
      restoreSnapshot.appendChild(opt);
    });
  } catch (err) {
    alert(err.message);
  }
}

document.getElementById("restore-snapshot")?.addEventListener("focus", () => {
  const repo = getEffectiveRepo();
  if (repo && restoreSnapshot.options.length <= 1) loadSnapshotsForRepo(repo);
});

restoreFilterMode?.addEventListener("change", () => {
  const mode = restoreFilterMode.value;
  restoreIncludeExclude.style.display = mode === "none" ? "none" : "grid";
  if (mode === "none") {
    restoreFileListContainer.style.display = "none";
  }
});

restoreLoadFiles?.addEventListener("click", async () => {
  const repo = getEffectiveRepo();
  const snapshot = restoreSnapshot.value;
  if (!repo || !snapshot) {
    alert("Select or type repository and select snapshot first.");
    return;
  }
  restoreFileList.innerHTML = "<span class=\"loading\">Loading…</span>";
  restoreFileListContainer.style.display = "block";
  try {
    const data = await request(
      `/api/recovery/ls?repo=${encodeURIComponent(repo)}&snapshot=${encodeURIComponent(snapshot)}`
    );
    const paths = data.paths || [];
    restoreFileList.innerHTML = "";
    paths.forEach((p) => {
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
  } catch (err) {
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
  restoreFrom.value = "user_home";
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
  restoreVerifySummary.style.display = "none";
  restoreVerifySummary.innerHTML = "";
  restoreFrom.dispatchEvent(new Event("change"));
});

// Load repos on page load for initial "Restore from" value
if (restoreFrom?.value) {
  restoreFrom.dispatchEvent(new Event("change"));
}
