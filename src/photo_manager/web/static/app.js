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
    <div class="stat"><strong>${backedUp}</strong><span>safely backed up</span></div>
    <div class="stat"><strong>${stats.flagged_photos || 0}</strong><span>editorially flagged</span></div>
    <div class="stat one-of-stat"><strong>${stats.one_of_photos || 0}</strong><span>in the “one of” shortlist</span></div>`;
}

const flagLabels = {
  flagship: "Flagship",
  include: "Include",
  candidate: "Candidate",
  one_of: "One of",
};

function selectedValues(name) {
  return Array.from(document.querySelectorAll(`input[name="${name}"]:checked`))
    .map(input => input.value);
}

function sourceLabel(sources) {
  const value = String(sources || "").toLowerCase();
  const camera = value.includes("camera") || value.includes("gopro") || value.includes("drone");
  const phone = value.includes("phone") || value.includes("iphone");
  if (camera && phone) return "Camera + phone";
  if (phone) return "Phone";
  if (camera) return "Camera";
  return "Archive";
}

function photoCard(photo) {
  const megapixels = photo.width && photo.height ? `${(photo.width * photo.height / 1e6).toFixed(1)} MP` : "Unknown size";
  const activeFlag = photo.editorial_flag || "";
  const flagButtons = Object.entries(flagLabels).map(([flag, label]) => `
    <button class="flag-button flag-${flag.replace("_", "-")} ${activeFlag === flag ? "active" : ""}"
      type="button" aria-pressed="${activeFlag === flag}"
      onclick="setEditorialFlag(${photo.id}, '${flag}')">${label}</button>`).join("");
  return `<article class="photo-card ${activeFlag ? `has-flag flag-card-${activeFlag.replace("_", "-")}` : ""}" data-photo-id="${photo.id}">
    <a class="photo-image" href="/api/photos/${photo.id}/original" target="_blank" rel="noopener">
      <img loading="lazy" src="/api/photos/${photo.id}/thumbnail" alt="${escapeHtml(photo.filename)}">
      <span class="source-label">${sourceLabel(photo.sources)}</span>
      ${photo.favorite ? '<span class="favorite-mark" title="Favourited">♥</span>' : ""}
      ${activeFlag ? `<span class="editorial-badge flag-${activeFlag.replace("_", "-")}">${flagLabels[activeFlag]}</span>` : ""}
    </a>
    <div class="photo-info">
      <div class="photo-title"><strong title="${escapeHtml(photo.filename)}">${escapeHtml(photo.filename)}</strong><span>${megapixels}</span></div>
      <p class="photo-meta">${photo.captured_at ? photo.captured_at.slice(0, 10) : "No date"} · ${formatBytes(photo.size_bytes)}</p>
      <div class="flag-controls" aria-label="Editorial flag">${flagButtons}</div>
    </div>
  </article>`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[char]);
}
function renderPhotos() {
  $("#photo-grid").innerHTML = state.photos.map(photoCard).join("");
  $("#empty-library").classList.toggle("hidden", state.photos.length > 0);
}

function updateFilterSummary() {
  const flagCount = selectedValues("flag-filter").length;
  const sourceCount = selectedValues("source-filter").length;
  const hasDates = Boolean($("#date-from").value || $("#date-to").value);
  const favorite = $("#favorites-filter").checked;
  const activeCount = flagCount + sourceCount + Number(hasDates) + Number(favorite);
  $("#filter-count").textContent = activeCount ? `${activeCount} active filter${activeCount === 1 ? "" : "s"} ·` : "All";
}

async function loadPhotos(reset = true) {
  if (reset) state.offset = 0;
  const params = new URLSearchParams({ limit: String(state.pageSize), offset: String(state.offset) });
  selectedValues("flag-filter").forEach(flag => params.append("flag", flag));
  selectedValues("source-filter").forEach(source => params.append("source", source));
  if ($("#favorites-filter").checked) params.set("favorite", "true");
  const dateFrom = $("#date-from").value;
  const dateTo = $("#date-to").value;
  if (dateFrom && dateTo && dateFrom > dateTo) {
    showToast("The start date must be before the end date.");
    return;
  }
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const page = await api(`/api/photos?${params}`);
  state.photos = reset ? page : state.photos.concat(page);
  state.offset = state.photos.length;
  renderPhotos();
  $("#load-more").classList.toggle("hidden", page.length < state.pageSize);
  updateFilterSummary();
}

async function setEditorialFlag(photoId, flag) {
  const photo = state.photos.find(item => item.id === photoId);
  if (!photo) return;
  const nextFlag = photo.editorial_flag === flag ? null : flag;
  try {
    await api(`/api/photos/${photoId}/flag`, {
      method: "PUT",
      body: JSON.stringify({ flag: nextFlag }),
    });
    photo.editorial_flag = nextFlag;
    const activeFilters = selectedValues("flag-filter");
    const effectiveFlag = nextFlag || "unflagged";
    if (activeFilters.length && !activeFilters.includes(effectiveFlag)) {
      state.photos = state.photos.filter(item => item.id !== photoId);
      state.offset = state.photos.length;
    }
    renderPhotos();
    await loadStats();
    showToast(nextFlag ? `Flagged as ${flagLabels[nextFlag]}.` : "Flag removed.");
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
  document.querySelectorAll('input[name="flag-filter"], input[name="source-filter"], #favorites-filter')
    .forEach(input => input.addEventListener("change", () => loadPhotos()));
  $("#date-from").addEventListener("change", () => loadPhotos());
  $("#date-to").addEventListener("change", () => loadPhotos());
  $("#clear-filters").addEventListener("click", () => {
    document.querySelectorAll('input[name="flag-filter"], input[name="source-filter"], #favorites-filter')
      .forEach(input => { input.checked = false; });
    $("#date-from").value = "";
    $("#date-to").value = "";
    loadPhotos();
  });
  $("#backup-button").addEventListener("click", runBackup);
  $("#upload-input").addEventListener("change", event => uploadFiles(event.target.files));
  $("#load-more").addEventListener("click", () => loadPhotos(false));
  try {
    const config = await api("/api/config");
    state.hosted = Boolean(config.hosted_gallery);
    if (state.hosted) state.pageSize = 24;
    document.querySelectorAll(".local-only").forEach(item => item.classList.toggle("hidden", state.hosted));
    await Promise.all([loadStats(), loadPhotos(), loadDuplicates()]);
  }
  catch (error) { showToast(error.message); }
});

window.setEditorialFlag = setEditorialFlag;
window.decideGroup = decideGroup;
