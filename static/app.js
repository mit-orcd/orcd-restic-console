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
