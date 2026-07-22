const state = { photos: [], groups: [], offset: 0, pageSize: 72, hosted: false };
const $ = (selector) => document.querySelector(selector);

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, index)).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.add("hidden"), 3500);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

async function loadStats() {
  const stats = await api("/api/stats");
  const backedUp = stats.backed_up || 0;
  $("#stats").innerHTML = `
    <div class="stat"><strong>${stats.photos || 0}</strong><span>cataloged originals</span></div>
    <div class="stat"><strong>${formatBytes(stats.bytes || 0)}</strong><span>source material</span></div>
    <div class="stat"><strong>${backedUp}</strong><span>safely backed up</span></div>
    <div class="stat"><strong>${stats.magazine_photos || 0}</strong><span>magazine picks</span></div>`;
}

function photoCard(photo) {
  const megapixels = photo.width && photo.height ? `${(photo.width * photo.height / 1e6).toFixed(1)} MP` : "Unknown size";
  const candidate = photo.magazine_status === "candidate";
  const selected = photo.magazine_status === "selected";
  return `<article class="photo-card">
    <a class="photo-image" href="/api/photos/${photo.id}/original" target="_blank" rel="noopener">
      <img loading="lazy" src="/api/photos/${photo.id}/thumbnail" alt="${escapeHtml(photo.filename)}">
      <span class="pill">${photo.backup_status === "uploaded" ? "Backed up" : "Local"}</span>
      ${photo.favorite ? '<span class="pill favorite">Favorite</span>' : ""}
    </a>
    <div class="photo-info">
      <div class="photo-title"><strong title="${escapeHtml(photo.filename)}">${escapeHtml(photo.filename)}</strong><span>${megapixels}</span></div>
      <p class="photo-meta">${photo.captured_at ? photo.captured_at.slice(0, 10) : "No date"} · ${formatBytes(photo.size_bytes)}</p>
      <div class="card-actions">
        <button class="${candidate ? "active" : ""}" onclick="setMagazine(${photo.id}, 'candidate')">Candidate</button>
        <button class="${selected ? "active" : ""}" onclick="setMagazine(${photo.id}, 'selected')">Selected</button>
        <button onclick="setMagazine(${photo.id}, 'placed')">Placed</button>
        <button onclick="editTags(${photo.id}, '${escapeAttribute(photo.user_tags || "")}')">Tags</button>
      </div>
      ${photo.tags ? `<div class="tag-row">${escapeHtml(photo.tags)}</div>` : ""}
    </div>
  </article>`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[char]);
}
function escapeAttribute(value) { return escapeHtml(value).replace(/'/g, "&#39;"); }

async function loadPhotos(reset = true) {
  if (reset) state.offset = 0;
  const status = $("#status-filter").value;
  const params = new URLSearchParams({ limit: String(state.pageSize), offset: String(state.offset) });
  if (status === "not_uploaded") params.set("backup_status", status);
  else if (status) {
    params.set("magazine_status", status);
    params.set("issue", $("#issue-input").value.trim());
  }
  if ($("#favorites-filter").checked) params.set("favorite", "true");
  const year = $("#year-filter").value.trim();
  if (year) params.set("year", year);
  const page = await api(`/api/photos?${params}`);
  state.photos = reset ? page : state.photos.concat(page);
  state.offset = state.photos.length;
  $("#photo-grid").innerHTML = state.photos.map(photoCard).join("");
  $("#empty-library").classList.toggle("hidden", state.photos.length > 0);
  $("#load-more").classList.toggle("hidden", page.length < state.pageSize);
}

async function setMagazine(photoId, status) {
  const issue = $("#issue-input").value.trim();
  if (!issue) return showToast("Add a magazine issue name first.");
  try {
    await api(`/api/photos/${photoId}/magazine`, { method: "PUT", body: JSON.stringify({ issue, status }) });
    showToast(`Marked ${status} for ${issue}.`);
    await Promise.all([loadPhotos(), loadStats()]);
  } catch (error) { showToast(error.message); }
}

async function editTags(photoId, existing) {
  const value = window.prompt("Comma-separated tags", existing);
  if (value === null) return;
  try {
    const tags = value.split(",").map(tag => tag.trim()).filter(Boolean);
    await api(`/api/photos/${photoId}/tags`, { method: "PUT", body: JSON.stringify({ tags }) });
    await loadPhotos();
    showToast("Tags updated.");
  } catch (error) { showToast(error.message); }
}

function duplicateGroup(group) {
  const members = group.members.map(member => {
    const megapixels = member.width && member.height ? (member.width * member.height / 1e6).toFixed(1) : "?";
    return `<div class="duplicate-member ${member.is_preferred ? "selected" : ""}">
      <img loading="lazy" src="/api/photos/${member.id}/thumbnail" alt="${escapeHtml(member.filename)}">
      <label><input type="radio" name="preferred-${group.id}" value="${member.id}" ${member.is_preferred ? "checked" : ""}>
        <span><strong>${escapeHtml(member.filename)}</strong><br>${megapixels} MP · ${formatBytes(member.size_bytes)} ${member.is_preferred ? "· recommended" : ""}</span>
      </label>
    </div>`;
  }).join("");
  return `<article class="duplicate-group">
    <p class="duplicate-evidence">${Math.round(group.confidence * 100)}% confidence · ${escapeHtml(group.match_method)}</p>
    <div class="duplicate-members">${members}</div>
    <div class="duplicate-actions">
      <button class="button secondary small" onclick="decideGroup(${group.id}, 'rejected')">Keep both</button>
      <button class="button small" onclick="decideGroup(${group.id}, 'confirmed')">Use selected master</button>
    </div>
  </article>`;
}

async function loadDuplicates() {
  state.groups = await api("/api/variant-groups?status=pending");
  $("#duplicate-list").innerHTML = state.groups.map(duplicateGroup).join("");
  $("#empty-duplicates").classList.toggle("hidden", state.groups.length > 0);
  $("#duplicate-count").textContent = state.groups.length ? `(${state.groups.length})` : "";
}

async function decideGroup(groupId, decision) {
  const selected = document.querySelector(`input[name="preferred-${groupId}"]:checked`);
  try {
    await api(`/api/variant-groups/${groupId}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, preferred_photo_id: selected ? Number(selected.value) : null }),
    });
    showToast(decision === "confirmed" ? "Master confirmed." : "Both versions will be kept.");
    await Promise.all([loadDuplicates(), loadStats()]);
  } catch (error) { showToast(error.message); }
}

async function runBackup() {
  const button = $("#backup-button");
  button.disabled = true;
  button.textContent = "Backing up…";
  try {
    const result = await api("/api/backups/run", { method: "POST" });
    showToast(`Backup complete: ${result.uploaded} uploaded, ${result.failed} failed.`);
    await Promise.all([loadPhotos(), loadStats()]);
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; button.textContent = "Back up now"; }
}

async function uploadFiles(files) {
  if (!files.length) return;
  const form = new FormData();
  for (const file of files) form.append("files", file);
  showToast(`Importing ${files.length} photo${files.length === 1 ? "" : "s"}…`);
  try {
    const response = await fetch("/api/imports/upload", { method: "POST", body: form });
    if (!response.ok) throw new Error(response.statusText);
    await Promise.all([loadPhotos(), loadDuplicates(), loadStats()]);
    showToast("Import complete.");
  } catch (error) { showToast(error.message); }
}

document.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(item => item.classList.toggle("active", item === tab));
    $("#library-view").classList.toggle("hidden", tab.dataset.view !== "library");
    $("#duplicates-view").classList.toggle("hidden", tab.dataset.view !== "duplicates");
  }));
  $("#status-filter").addEventListener("change", () => loadPhotos());
  $("#favorites-filter").addEventListener("change", () => loadPhotos());
  $("#year-filter").addEventListener("change", () => loadPhotos());
  $("#issue-input").addEventListener("change", () => loadPhotos());
  $("#backup-button").addEventListener("click", runBackup);
  $("#upload-input").addEventListener("change", event => uploadFiles(event.target.files));
  $("#load-more").addEventListener("click", () => loadPhotos(false));
  try {
    const config = await api("/api/config");
    state.hosted = Boolean(config.hosted_gallery);
    document.querySelectorAll(".local-only").forEach(item => item.classList.toggle("hidden", state.hosted));
    await Promise.all([loadStats(), loadPhotos(), loadDuplicates()]);
  }
  catch (error) { showToast(error.message); }
});

window.setMagazine = setMagazine;
window.editTags = editTags;
window.decideGroup = decideGroup;
