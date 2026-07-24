const state = {
  photos: [],
  groups: [],
  offset: 0,
  pageSize: 72,
  hosted: false,
  viewerPhoto: null,
  viewerImageRequest: 0,
  flagHistory: [],
  undoInFlight: false,
  oneOfGroup: { active: false, group_id: null, member_count: 0 },
  finishOneOfInFlight: false,
};
const $ = (selector) => document.querySelector(selector);
const viewerZoom = {
  scale: 1,
  x: 0,
  y: 0,
  pointers: new Map(),
  gesture: null,
  lastTapTime: 0,
  lastTapX: 0,
  lastTapY: 0,
  animationTimer: null,
  qualityTimer: null,
};
const VIEWER_MIN_SCALE = 1;
const VIEWER_MAX_SCALE = 5;
const VIEWER_DOUBLE_TAP_SCALE = 3;

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
  not_included: "Not included",
};

const compactFlagLabels = {
  flagship: "Flag",
  include: "In",
  candidate: "Maybe",
  one_of: "1 of",
  not_included: "Not in",
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

function flagButtons(photo, context = "card") {
  const activeFlag = photo.editorial_flag || "";
  return Object.entries(flagLabels).map(([flag, label]) => {
    const visibleLabel = context === "viewer" ? label : compactFlagLabels[flag];
    return `
    <button class="flag-button flag-${flag.replace("_", "-")} ${activeFlag === flag ? "active" : ""}"
      type="button" aria-label="${label}" title="${label}" aria-pressed="${activeFlag === flag}"
      data-flag-context="${context}"
      onclick="setEditorialFlag(${photo.id}, '${flag}')">${visibleLabel}</button>`;
  }).join("");
}

function editorialBadge(photo) {
  const activeFlag = photo.editorial_flag || "";
  return activeFlag
    ? `<span class="editorial-badge flag-${activeFlag.replace("_", "-")}">${flagLabels[activeFlag]}</span>`
    : "";
}

function photoCard(photo) {
  const activeFlag = photo.editorial_flag || "";
  const capturedDate = photo.captured_at ? photo.captured_at.slice(0, 10) : "Date unavailable";
  const sourceAndDate = `${sourceLabel(photo.sources)} · ${capturedDate}`;
  return `<article class="photo-card ${activeFlag ? `has-flag flag-card-${activeFlag.replace("_", "-")}` : ""}" data-photo-id="${photo.id}">
    <button class="photo-image" type="button" onclick="openPhotoViewer(${photo.id})"
      aria-label="View ${escapeHtml(photo.filename)} full screen" title="${escapeHtml(photo.filename)}">
      <img loading="lazy" src="/api/photos/${photo.id}/thumbnail" alt="${escapeHtml(photo.filename)}">
      <span class="source-label">${escapeHtml(sourceAndDate)}</span>
      ${photo.favorite ? '<span class="favorite-mark" title="Favourited">♥</span>' : ""}
      ${editorialBadge(photo)}
    </button>
    <div class="photo-info">
      <div class="flag-controls" aria-label="Editorial flag">${flagButtons(photo)}</div>
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

function updateUndoButton() {
  const button = $("#undo-flag-button");
  if (!button) return;
  const count = state.flagHistory.length;
  button.textContent = count ? `Undo (${count})` : "Undo";
  button.disabled = !count || state.undoInFlight;
}

function rememberFlagChange(photoId, previousFlag, nextFlag) {
  state.flagHistory.push({ photoId, previousFlag, nextFlag });
  if (state.flagHistory.length > 5) state.flagHistory.shift();
  updateUndoButton();
}

function updateOneOfGroupButton() {
  const button = $("#finish-one-of-button");
  if (!button) return;
  const count = Number(state.oneOfGroup.member_count || 0);
  button.textContent = state.oneOfGroup.active
    ? `Finish one of (${count})`
    : "Finish one of";
  button.disabled = !state.oneOfGroup.active || state.finishOneOfInFlight;
}

function setOneOfGroupState(group) {
  state.oneOfGroup = group || { active: false, group_id: null, member_count: 0 };
  updateOneOfGroupButton();
}

async function loadOneOfGroupState() {
  setOneOfGroupState(await api("/api/one-of-groups/current"));
}

async function finishOneOfGroup() {
  if (!state.oneOfGroup.active || state.finishOneOfInFlight) return;
  state.finishOneOfInFlight = true;
  updateOneOfGroupButton();
  try {
    const finished = await api("/api/one-of-groups/current/finish", { method: "POST" });
    setOneOfGroupState({ active: false, group_id: null, member_count: 0 });
    const count = Number(finished.member_count || 0);
    showToast(`Finished one-of group with ${count} photo${count === 1 ? "" : "s"}.`);
  } catch (error) {
    showToast(error.message);
  } finally {
    state.finishOneOfInFlight = false;
    updateOneOfGroupButton();
  }
}

function photoMatchesFlagFilters(flag) {
  const activeFilters = selectedValues("flag-filter");
  return !activeFilters.length || activeFilters.includes(flag || "unflagged");
}

function updatePhotoFlagPresentation(photo) {
  const activeFlag = photo.editorial_flag || "";
  const card = document.querySelector(`.photo-card[data-photo-id="${photo.id}"]`);
  if (card) {
    card.className = `photo-card ${activeFlag ? `has-flag flag-card-${activeFlag.replace("_", "-")}` : ""}`.trim();
    const controls = card.querySelector(".flag-controls");
    if (controls) controls.innerHTML = flagButtons(photo);
    const imageButton = card.querySelector(".photo-image");
    const existingBadge = imageButton?.querySelector(".editorial-badge");
    if (activeFlag && existingBadge) {
      existingBadge.className = `editorial-badge flag-${activeFlag.replace("_", "-")}`;
      existingBadge.textContent = flagLabels[activeFlag];
    } else if (activeFlag && imageButton) {
      imageButton.insertAdjacentHTML("beforeend", editorialBadge(photo));
    } else {
      existingBadge?.remove();
    }
  }
  if (state.viewerPhoto?.id === photo.id) {
    state.viewerPhoto.editorial_flag = photo.editorial_flag;
    $("#viewer-flag-controls").innerHTML = flagButtons(photo, "viewer");
  }
}

function removePhotoFromResults(photoId) {
  document.querySelector(`.photo-card[data-photo-id="${photoId}"]`)?.remove();
  state.photos = state.photos.filter(item => item.id !== photoId);
  state.offset = state.photos.length;
  $("#empty-library").classList.toggle("hidden", state.photos.length > 0);
}

function constrainViewerPan() {
  const stage = $("#viewer-stage");
  const image = $("#viewer-image");
  if (!stage || !image) return;
  const maxX = Math.max(0, (image.offsetWidth * viewerZoom.scale - stage.clientWidth) / 2);
  const maxY = Math.max(0, (image.offsetHeight * viewerZoom.scale - stage.clientHeight) / 2);
  viewerZoom.x = Math.max(-maxX, Math.min(maxX, viewerZoom.x));
  viewerZoom.y = Math.max(-maxY, Math.min(maxY, viewerZoom.y));
}

function applyViewerTransform(animate = false) {
  const stage = $("#viewer-stage");
  const image = $("#viewer-image");
  if (!stage || !image) return;
  constrainViewerPan();
  clearTimeout(viewerZoom.animationTimer);
  stage.classList.toggle("is-animating", animate);
  stage.classList.toggle("is-zoomed", viewerZoom.scale > VIEWER_MIN_SCALE);
  image.style.transform = `translate3d(${viewerZoom.x}px, ${viewerZoom.y}px, 0) scale(${viewerZoom.scale})`;
  if (animate) {
    viewerZoom.animationTimer = setTimeout(
      () => stage.classList.remove("is-animating"),
      240,
    );
  }
}

function resetViewerZoom(animate = false) {
  viewerZoom.scale = VIEWER_MIN_SCALE;
  viewerZoom.x = 0;
  viewerZoom.y = 0;
  viewerZoom.pointers.clear();
  viewerZoom.gesture = null;
  viewerZoom.lastTapTime = 0;
  $("#viewer-stage")?.classList.remove("is-dragging");
  applyViewerTransform(animate);
}

function zoomViewerTo(targetScale, clientX, clientY, animate = false) {
  const stage = $("#viewer-stage");
  if (!stage) return;
  const nextScale = Math.max(VIEWER_MIN_SCALE, Math.min(VIEWER_MAX_SCALE, targetScale));
  if (nextScale === VIEWER_MIN_SCALE) {
    viewerZoom.scale = VIEWER_MIN_SCALE;
    viewerZoom.x = 0;
    viewerZoom.y = 0;
    applyViewerTransform(animate);
    return;
  }
  const bounds = stage.getBoundingClientRect();
  const localX = clientX - (bounds.left + bounds.width / 2);
  const localY = clientY - (bounds.top + bounds.height / 2);
  const ratio = nextScale / viewerZoom.scale;
  viewerZoom.x = localX - (localX - viewerZoom.x) * ratio;
  viewerZoom.y = localY - (localY - viewerZoom.y) * ratio;
  viewerZoom.scale = nextScale;
  applyViewerTransform(animate);
}

function toggleViewerZoom(clientX, clientY) {
  const target = viewerZoom.scale > VIEWER_MIN_SCALE
    ? VIEWER_MIN_SCALE
    : VIEWER_DOUBLE_TAP_SCALE;
  zoomViewerTo(target, clientX, clientY, true);
}

function showViewerQuality(message, hideAfter = 0) {
  const quality = $("#viewer-quality");
  clearTimeout(viewerZoom.qualityTimer);
  quality.textContent = message;
  quality.classList.remove("hidden");
  if (hideAfter) {
    viewerZoom.qualityTimer = setTimeout(() => quality.classList.add("hidden"), hideAfter);
  }
}

function startViewerPan(point, moved = false) {
  viewerZoom.gesture = {
    kind: "pan",
    startX: point.x,
    startY: point.y,
    originX: viewerZoom.x,
    originY: viewerZoom.y,
    startedAt: Date.now(),
    moved,
  };
}

function startViewerPinch() {
  const points = Array.from(viewerZoom.pointers.values()).slice(0, 2);
  if (points.length < 2) return;
  const [first, second] = points;
  viewerZoom.gesture = {
    kind: "pinch",
    startDistance: Math.hypot(second.x - first.x, second.y - first.y),
    startCenterX: (first.x + second.x) / 2,
    startCenterY: (first.y + second.y) / 2,
    startScale: viewerZoom.scale,
    originX: viewerZoom.x,
    originY: viewerZoom.y,
    moved: true,
  };
}

function viewerPointerDown(event) {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  event.preventDefault();
  const stage = $("#viewer-stage");
  try { stage.setPointerCapture(event.pointerId); } catch (_) {}
  const point = { x: event.clientX, y: event.clientY };
  viewerZoom.pointers.set(event.pointerId, point);
  if (viewerZoom.pointers.size === 1) startViewerPan(point);
  else if (viewerZoom.pointers.size === 2) startViewerPinch();
}

function viewerPointerMove(event) {
  if (!viewerZoom.pointers.has(event.pointerId)) return;
  event.preventDefault();
  viewerZoom.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  const stage = $("#viewer-stage");
  if (viewerZoom.pointers.size >= 2) {
    if (viewerZoom.gesture?.kind !== "pinch") startViewerPinch();
    const points = Array.from(viewerZoom.pointers.values()).slice(0, 2);
    const [first, second] = points;
    const distance = Math.hypot(second.x - first.x, second.y - first.y);
    const centerX = (first.x + second.x) / 2;
    const centerY = (first.y + second.y) / 2;
    const gesture = viewerZoom.gesture;
    const nextScale = Math.max(
      VIEWER_MIN_SCALE,
      Math.min(
        VIEWER_MAX_SCALE,
        gesture.startScale * distance / Math.max(gesture.startDistance, 1),
      ),
    );
    const bounds = stage.getBoundingClientRect();
    const stageCenterX = bounds.left + bounds.width / 2;
    const stageCenterY = bounds.top + bounds.height / 2;
    const ratio = nextScale / gesture.startScale;
    const startLocalX = gesture.startCenterX - stageCenterX;
    const startLocalY = gesture.startCenterY - stageCenterY;
    viewerZoom.x = centerX - stageCenterX - (startLocalX - gesture.originX) * ratio;
    viewerZoom.y = centerY - stageCenterY - (startLocalY - gesture.originY) * ratio;
    viewerZoom.scale = nextScale;
    applyViewerTransform();
    return;
  }
  const point = viewerZoom.pointers.get(event.pointerId);
  const gesture = viewerZoom.gesture;
  if (!point || gesture?.kind !== "pan") return;
  const deltaX = point.x - gesture.startX;
  const deltaY = point.y - gesture.startY;
  if (Math.hypot(deltaX, deltaY) > 5) gesture.moved = true;
  if (viewerZoom.scale > VIEWER_MIN_SCALE) {
    stage.classList.add("is-dragging");
    viewerZoom.x = gesture.originX + deltaX;
    viewerZoom.y = gesture.originY + deltaY;
    applyViewerTransform();
  }
}

function recordViewerTap(clientX, clientY) {
  const now = Date.now();
  const nearby = Math.hypot(
    clientX - viewerZoom.lastTapX,
    clientY - viewerZoom.lastTapY,
  ) < 48;
  if (nearby && now - viewerZoom.lastTapTime < 360) {
    viewerZoom.lastTapTime = 0;
    toggleViewerZoom(clientX, clientY);
    return;
  }
  viewerZoom.lastTapTime = now;
  viewerZoom.lastTapX = clientX;
  viewerZoom.lastTapY = clientY;
}

function finishViewerPointer(event, canceled = false) {
  if (!viewerZoom.pointers.has(event.pointerId)) return;
  const gesture = viewerZoom.gesture;
  const isTap = !canceled
    && viewerZoom.pointers.size === 1
    && gesture?.kind === "pan"
    && !gesture.moved
    && Date.now() - gesture.startedAt < 360;
  viewerZoom.pointers.delete(event.pointerId);
  $("#viewer-stage").classList.remove("is-dragging");
  if (isTap) recordViewerTap(event.clientX, event.clientY);
  const remaining = Array.from(viewerZoom.pointers.values());
  if (remaining.length === 1) startViewerPan(remaining[0], true);
  else if (remaining.length >= 2) startViewerPinch();
  else viewerZoom.gesture = null;
}

function viewerWheel(event) {
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.22 : 1 / 1.22;
  zoomViewerTo(viewerZoom.scale * factor, event.clientX, event.clientY);
}

function updatePhotoViewer() {
  const photo = state.viewerPhoto;
  if (!photo) return;
  const requestId = ++state.viewerImageRequest;
  const image = $("#viewer-image");
  resetViewerZoom();
  image.alt = photo.filename;
  image.dataset.quality = "thumbnail";
  image.src = `/api/photos/${photo.id}/thumbnail`;
  const isPhoto = String(photo.media_type || "").startsWith("image/");
  if (isPhoto) {
    showViewerQuality("Loading high quality…");
    const highQualityImage = new Image();
    highQualityImage.onload = () => {
      if (requestId !== state.viewerImageRequest || state.viewerPhoto?.id !== photo.id) return;
      image.src = highQualityImage.src;
      image.dataset.quality = "preview";
      showViewerQuality("High quality", 1400);
    };
    highQualityImage.onerror = () => {
      if (requestId !== state.viewerImageRequest || state.viewerPhoto?.id !== photo.id) return;
      showViewerQuality("High quality unavailable", 2400);
    };
    highQualityImage.src = `/api/photos/${photo.id}/preview`;
  } else {
    $("#viewer-quality").classList.add("hidden");
  }
  const hasMultiplePhotos = state.photos.length > 1;
  $("#viewer-prev").disabled = !hasMultiplePhotos;
  $("#viewer-next").disabled = !hasMultiplePhotos;
  $("#viewer-flag-controls").innerHTML = flagButtons(photo, "viewer");
}

function openPhotoViewer(photoId) {
  const photo = state.photos.find(item => item.id === photoId);
  if (!photo) return;
  state.viewerPhoto = photo;
  updatePhotoViewer();
  const viewer = $("#photo-viewer");
  if (!viewer.open) viewer.showModal();
}

function closePhotoViewer() {
  const viewer = $("#photo-viewer");
  if (viewer.open) viewer.close();
}

function movePhotoViewer(direction) {
  if (!state.viewerPhoto || state.photos.length < 2) return;
  const currentIndex = state.photos.findIndex(item => item.id === state.viewerPhoto.id);
  if (currentIndex < 0) return;
  const nextIndex = (currentIndex + direction + state.photos.length) % state.photos.length;
  state.viewerPhoto = state.photos[nextIndex];
  updatePhotoViewer();
}

function updateFilterSummary() {
  const flagCount = selectedValues("flag-filter").length;
  const sourceCount = selectedValues("source-filter").length;
  const mediaCount = selectedValues("media-filter").length;
  const hasDates = Boolean($("#date-from").value || $("#date-to").value);
  const favorite = $("#favorites-filter").checked;
  const activeCount = flagCount + sourceCount + mediaCount + Number(hasDates) + Number(favorite);
  $("#filter-count").textContent = activeCount ? `${activeCount} active filter${activeCount === 1 ? "" : "s"} ·` : "All";
}

function photoQueryParams(limit, offset) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  params.set("date_order", $("#date-sort").value);
  selectedValues("flag-filter").forEach(flag => params.append("flag", flag));
  selectedValues("source-filter").forEach(source => params.append("source", source));
  selectedValues("media-filter").forEach(media => params.append("media", media));
  if ($("#favorites-filter").checked) params.set("favorite", "true");
  const dateFrom = $("#date-from").value;
  const dateTo = $("#date-to").value;
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  return params;
}

async function fetchPhotoPage(limit, offset) {
  return api(`/api/photos?${photoQueryParams(limit, offset)}`);
}

async function loadPhotos(reset = true) {
  if (reset) state.offset = 0;
  const dateFrom = $("#date-from").value;
  const dateTo = $("#date-to").value;
  if (dateFrom && dateTo && dateFrom > dateTo) {
    showToast("The start date must be before the end date.");
    return;
  }
  const page = await fetchPhotoPage(state.pageSize, state.offset);
  state.photos = reset ? page : state.photos.concat(page);
  state.offset = state.photos.length;
  renderPhotos();
  $("#load-more").classList.toggle("hidden", page.length < state.pageSize);
  updateFilterSummary();
}

async function fillFilteredResultGap() {
  const page = await fetchPhotoPage(2, state.photos.length);
  const replacement = page.find(
    candidate => !state.photos.some(photo => photo.id === candidate.id),
  );
  if (replacement) {
    state.photos.push(replacement);
    state.offset = state.photos.length;
    $("#photo-grid").insertAdjacentHTML("beforeend", photoCard(replacement));
    $("#empty-library").classList.add("hidden");
  }
  $("#load-more").classList.toggle("hidden", page.length < 2);
}

async function setEditorialFlag(photoId, flag) {
  if (state.undoInFlight) return;
  const photo = state.photos.find(item => item.id === photoId)
    || (state.viewerPhoto && state.viewerPhoto.id === photoId ? state.viewerPhoto : null);
  if (!photo) return;
  const previousFlag = photo.editorial_flag || null;
  const nextFlag = photo.editorial_flag === flag ? null : flag;
  try {
    const update = await api(`/api/photos/${photoId}/flag`, {
      method: "PUT",
      body: JSON.stringify({ flag: nextFlag }),
    });
    setOneOfGroupState(update.one_of_group);
    rememberFlagChange(photoId, previousFlag, nextFlag);
    photo.editorial_flag = nextFlag;
    if (photoMatchesFlagFilters(nextFlag)) {
      updatePhotoFlagPresentation(photo);
    } else {
      removePhotoFromResults(photoId);
      if (state.viewerPhoto?.id === photoId) closePhotoViewer();
      await fillFilteredResultGap();
    }
    await loadStats();
    showToast(nextFlag ? `Flagged as ${flagLabels[nextFlag]}.` : "Flag removed.");
  } catch (error) { showToast(error.message); }
}

async function undoLastFlag() {
  if (!state.flagHistory.length || state.undoInFlight) return;
  const change = state.flagHistory[state.flagHistory.length - 1];
  state.undoInFlight = true;
  updateUndoButton();
  try {
    const update = await api(`/api/photos/${change.photoId}/flag`, {
      method: "PUT",
      body: JSON.stringify({ flag: change.previousFlag }),
    });
    setOneOfGroupState(update.one_of_group);
    state.flagHistory.pop();
    const photo = state.photos.find(item => item.id === change.photoId)
      || (state.viewerPhoto?.id === change.photoId ? state.viewerPhoto : null);
    if (!photo) {
      await loadPhotos();
    } else {
      photo.editorial_flag = change.previousFlag;
      if (photoMatchesFlagFilters(change.previousFlag)) {
        updatePhotoFlagPresentation(photo);
      } else {
        removePhotoFromResults(change.photoId);
        if (state.viewerPhoto?.id === change.photoId) closePhotoViewer();
        await fillFilteredResultGap();
      }
    }
    await loadStats();
    showToast("Undid last flag change.");
  } catch (error) {
    showToast(error.message);
  } finally {
    state.undoInFlight = false;
    updateUndoButton();
  }
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
  $("#undo-flag-button").addEventListener("click", undoLastFlag);
  $("#finish-one-of-button").addEventListener("click", finishOneOfGroup);
  $("#viewer-close").addEventListener("click", closePhotoViewer);
  $("#viewer-prev").addEventListener("click", () => movePhotoViewer(-1));
  $("#viewer-next").addEventListener("click", () => movePhotoViewer(1));
  $("#viewer-image").addEventListener("load", () => applyViewerTransform());
  $("#viewer-stage").addEventListener("pointerdown", viewerPointerDown);
  $("#viewer-stage").addEventListener("pointermove", viewerPointerMove);
  $("#viewer-stage").addEventListener("pointerup", event => finishViewerPointer(event));
  $("#viewer-stage").addEventListener(
    "pointercancel",
    event => finishViewerPointer(event, true),
  );
  $("#viewer-stage").addEventListener("wheel", viewerWheel, { passive: false });
  $("#photo-viewer").addEventListener("close", () => {
    state.viewerPhoto = null;
    state.viewerImageRequest += 1;
    resetViewerZoom();
  });
  $("#photo-viewer").addEventListener("keydown", event => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      movePhotoViewer(-1);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      movePhotoViewer(1);
    }
  });
  document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(item => item.classList.toggle("active", item === tab));
    $("#library-view").classList.toggle("hidden", tab.dataset.view !== "library");
    $("#duplicates-view").classList.toggle("hidden", tab.dataset.view !== "duplicates");
  }));
  document.querySelectorAll('input[name="flag-filter"], input[name="source-filter"], input[name="media-filter"], #favorites-filter')
    .forEach(input => input.addEventListener("change", () => loadPhotos()));
  $("#date-from").addEventListener("change", () => loadPhotos());
  $("#date-to").addEventListener("change", () => loadPhotos());
  $("#date-sort").addEventListener("change", () => loadPhotos());
  $("#clear-filters").addEventListener("click", () => {
    document.querySelectorAll('input[name="flag-filter"], input[name="source-filter"], input[name="media-filter"], #favorites-filter')
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
    if (state.hosted) state.pageSize = 60;
    document.querySelectorAll(".local-only").forEach(item => item.classList.toggle("hidden", state.hosted));
    await Promise.all([loadStats(), loadPhotos(), loadDuplicates(), loadOneOfGroupState()]);
  }
  catch (error) { showToast(error.message); }
});

window.setEditorialFlag = setEditorialFlag;
window.openPhotoViewer = openPhotoViewer;
window.decideGroup = decideGroup;
