const $ = id => document.getElementById(id);
let loaded = false;
let localLog = '';
let currentModalSku = null;
let currentModalFolder = null;
let lastCreatedList = [];
let lastPendingList = [];
let driveUrlsQueue = [];
let driveFoldersStats = [];
let hiddenDriveFolders = [];
let activePipelineFolders = [];
let pipelineIsRunning = false;
let currentSelectedFolder = 'all';
let driveFolderSearch = '';
let driveFolderPage = 1;
const DRIVE_FOLDERS_PER_PAGE = 4;

function driveFolderId(url) {
  const match = String(url || '').match(/\/folders\/([^/?#]+)/i);
  return match ? match[1] : '';
}

function renderDriveFolders(statsList, queueList) {
  const container = $('drive-folders-list');
  container.replaceChildren();
  container.classList.toggle('pipeline-empty', !pipelineIsRunning);

  // Combine queue with stats
  const hiddenFolderKeys = new Set(hiddenDriveFolders.map(folder => String(folder || '').trim().toLocaleLowerCase('vi')));
  const activePipelineKeys = new Set(activePipelineFolders.map(folder => String(folder || '').trim().toLocaleLowerCase('vi')));
  const items = ((statsList && statsList.length > 0) ? statsList : queueList.map(q => ({
    folder: q.folder || '',
    folder_display: q.folder || 'Mặc định',
    url: q.url || '',
    modified_at: q.modified_at || '',
    modified_ts: Date.parse(q.modified_at || '') || 0,
    total: 0,
    created_count: 0,
    pending_count: 0,
    percent: 0,
    is_active: false
  }))).filter(item => {
    const folderKey = String(item.folder_display || item.folder || 'Mặc định').trim().toLocaleLowerCase('vi');
    return pipelineIsRunning && activePipelineKeys.has(folderKey) && !hiddenFolderKeys.has(folderKey);
  });

  const sortedItems = [...items].sort((a, b) => {
    const aModified = Number(a.modified_ts || Date.parse(a.modified_at || '') || 0);
    const bModified = Number(b.modified_ts || Date.parse(b.modified_at || '') || 0);
    return bModified - aModified || String(a.folder_display || a.folder || '').localeCompare(String(b.folder_display || b.folder || ''), 'vi');
  });
  const query = driveFolderSearch.trim().toLocaleLowerCase('vi');
  const filteredItems = query
    ? sortedItems.filter(item => String(item.folder_display || item.folder || '').toLocaleLowerCase('vi').includes(query))
    : sortedItems;
  const totalPages = Math.max(1, Math.ceil(filteredItems.length / DRIVE_FOLDERS_PER_PAGE));
  driveFolderPage = Math.min(Math.max(1, driveFolderPage), totalPages);
  const pageStart = (driveFolderPage - 1) * DRIVE_FOLDERS_PER_PAGE;
  const pageItems = filteredItems.slice(pageStart, pageStart + DRIVE_FOLDERS_PER_PAGE);

  $('drive-folder-count').textContent = query
    ? `${filteredItems.length}/${items.length} thư mục`
    : `${items.length} thư mục`;
  $('drive-pagination').classList.toggle('hidden', filteredItems.length <= DRIVE_FOLDERS_PER_PAGE);
  $('drive-page-info').textContent = `Trang ${driveFolderPage}/${totalPages}`;
  $('drive-page-prev').disabled = driveFolderPage <= 1;
  $('drive-page-next').disabled = driveFolderPage >= totalPages;

  if (!items.length || !pageItems.length) {
    const empty = document.createElement('div');
    empty.className = 'hint';
    empty.style.padding = '12px';
    empty.style.textAlign = 'center';
    empty.textContent = items.length
      ? `Không tìm thấy thư mục khớp với “${driveFolderSearch.trim()}”.`
      : (pipelineIsRunning
        ? 'Không còn thư mục nào đang chờ xử lý trong pipeline.'
        : 'Chưa có pipeline nào đang chạy.');
    container.appendChild(empty);
    return;
  }

  pageItems.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'drive-folder-item' + (item.is_active ? ' active' : '');

    const header = document.createElement('div');
    header.className = 'df-header';

    const titleBox = document.createElement('div');
    titleBox.className = 'df-title';
    titleBox.innerHTML = `📁 <span class="df-name">${escapeHtml(item.folder_display || item.folder || 'Mặc định')}</span>`;

    const badge = document.createElement('span');
    if (item.is_active) {
      badge.className = 'df-badge running';
      badge.textContent = '⚡ Đang xử lý...';
    } else if (item.total > 0 && item.created_count >= item.total) {
      badge.className = 'df-badge ok';
      badge.textContent = `✔ Đã xong ${item.created_count}/${item.total}`;
    } else if (item.created_count > 0) {
      badge.className = 'df-badge pending';
      badge.textContent = `● ${item.created_count}/${item.total} (${item.percent}%)`;
    } else {
      badge.className = 'df-badge';
      badge.textContent = item.total > 0 ? `Chưa chạy (${item.total} SKU)` : 'Chưa tải ảnh';
    }

    header.appendChild(titleBox);
    header.appendChild(badge);
    card.appendChild(header);

    if (item.url) {
      const urlLink = document.createElement('a');
      urlLink.className = 'df-url';
      urlLink.href = item.url;
      urlLink.target = '_blank';
      urlLink.title = item.url;
      urlLink.textContent = '🔗 ' + item.url;
      card.appendChild(urlLink);
    }

    const meta = document.createElement('div');
    meta.className = 'df-meta';
    let syncInfoHtml = '';
    if (item.drive_total !== undefined && item.drive_total !== null) {
      syncInfoHtml += `<span>📥 Drive: <b>${item.drive_total}</b></span>`;
    }
    if (item.last_sync_at) {
      const timeOnly = item.last_sync_at.split(' ')[1] || item.last_sync_at;
      syncInfoHtml += `<span title="Lần fetch gần nhất: ${escapeHtml(item.last_sync_at)}">🕒 Sync: <b>${escapeHtml(timeOnly)}</b></span>`;
    }
    meta.innerHTML = `
      <span>📦 Tổng SKU: <b>${item.total || 0}</b></span>
      ${syncInfoHtml}
      <span>✂ Đã crop: <b>${item.cropped_count || 0}</b></span>
      <span>🎨 Seamless: <b>${item.seamless_count || 0}</b></span>
      <span>👗 Swatch: <b>${item.fabric_count || 0}</b></span>
    `;
    card.appendChild(meta);

    const progWrap = document.createElement('div');
    progWrap.className = 'df-progress-bar';
    const progBar = document.createElement('span');
    progBar.style.width = (item.percent || 0) + '%';
    progWrap.appendChild(progBar);
    card.appendChild(progWrap);

    const actions = document.createElement('div');
    actions.className = 'df-actions';

    const runChatGptBtn = document.createElement('button');
    runChatGptBtn.type = 'button';
    runChatGptBtn.className = 'primary btn-sm';
    runChatGptBtn.innerHTML = '▶ Chạy ChatGPT';
    runChatGptBtn.onclick = () => runSingleFolder(item.folder, item.url, 'chatgpt');

    const runAlgoBtn = document.createElement('button');
    runAlgoBtn.type = 'button';
    runAlgoBtn.className = 'ghost btn-sm';
    runAlgoBtn.style.color = 'var(--green)';
    runAlgoBtn.style.borderColor = 'var(--green)';
    runAlgoBtn.innerHTML = '⚡ Thuật toán';
    runAlgoBtn.onclick = () => runSingleFolder(item.folder, item.url, 'algo');

    const runFlowBtn = document.createElement('button');
    runFlowBtn.type = 'button';
    runFlowBtn.className = 'ghost btn-sm';
    runFlowBtn.style.color = 'var(--cyan)';
    runFlowBtn.style.borderColor = 'var(--cyan)';
    runFlowBtn.innerHTML = '🌊 Chạy Flow';
    runFlowBtn.onclick = () => runSingleFolder(item.folder, item.url, 'flow');

    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'ghost btn-sm';
    openBtn.innerHTML = '📁 Mở output';
    openBtn.onclick = () => openFolderDirectory(item.folder);

    const viewBtn = document.createElement('button');
    viewBtn.type = 'button';
    viewBtn.className = 'ghost btn-sm';
    viewBtn.innerHTML = '🔍 Xem SKU';
    viewBtn.onclick = () => selectFolderFilter(item.folder);

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'danger btn-sm';
    delBtn.innerHTML = '✕';
    delBtn.title = 'Gỡ thư mục khỏi danh sách quản lý';
    delBtn.onclick = () => removeDriveFolder(item.folder, item.url);

    actions.appendChild(runChatGptBtn);
    actions.appendChild(runAlgoBtn);
    actions.appendChild(runFlowBtn);
    actions.appendChild(openBtn);
    actions.appendChild(viewBtn);
    actions.appendChild(delBtn);
    card.appendChild(actions);

    container.appendChild(card);
  });
}

$('drive-folder-search').addEventListener('input', event => {
  driveFolderSearch = event.target.value || '';
  driveFolderPage = 1;
  renderDriveFolders(driveFoldersStats, driveUrlsQueue);
});
$('drive-page-prev').onclick = () => {
  if (driveFolderPage > 1) driveFolderPage -= 1;
  renderDriveFolders(driveFoldersStats, driveUrlsQueue);
};
$('drive-page-next').onclick = () => {
  driveFolderPage += 1;
  renderDriveFolders(driveFoldersStats, driveUrlsQueue);
};

function escapeHtml(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function updateFolderFilterSelect(statsList) {
  const select = $('folder-filter');
  const previousVal = select.value || currentSelectedFolder || 'all';
  select.replaceChildren();

  const allOpt = document.createElement('option');
  allOpt.value = 'all';
  allOpt.textContent = '📁 Tất cả thư mục';
  select.appendChild(allOpt);

  if (statsList && statsList.length) {
    statsList.forEach(item => {
      const opt = document.createElement('option');
      opt.value = item.folder || '';
      const display = item.folder_display || item.folder || 'Mặc định';
      opt.textContent = `📁 ${display} (${item.created_count || 0}/${item.total || 0} - ${item.percent || 0}%)`;
      select.appendChild(opt);
    });
  }

  // Restore previous selection if exists
  select.value = previousVal;
}

function selectFolderFilter(folderName) {
  const select = $('folder-filter');
  select.value = folderName || '';
  currentSelectedFolder = folderName || '';
  onFolderFilterChange();
  switchTab('progress');
  $('fabric-percent').scrollIntoView({ behavior: 'smooth' });
}

function onFolderFilterChange() {
  currentSelectedFolder = $('folder-filter').value;
  // Trigger immediate state fetch with folder filter
  fetchState();
}

async function runSingleFolder(folder, url, engine = 'chatgpt') {
  const label = engine === 'algo' ? 'Thuật toán CV' : engine === 'flow' ? 'Google Flow' : 'ChatGPT';
  if (!confirm(`Chạy pipeline ${label} cho riêng thư mục "${folder || 'Mặc định'}"?`)) return;
  try {
    const pl = payload();
    pl.folder = folder || '';
    pl.url = url || '';
    pl.engine = engine;
    if (engine === 'chatgpt' && !(await prepareSwatchPrerequisites(pl))) return;
    await api('/api/run-folder', pl);
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) {
    toastError(e);
  }
}

async function openFolderDirectory(folder) {
  try {
    await api('/api/open-folder', { folder: folder || '' });
  } catch (e) {
    toastError(e);
  }
}

async function removeDriveFolder(folder, url) {
  const name = folder || 'Mặc định';
  if (!confirm(`Gỡ thư mục "${name}" khỏi danh sách quản lý Google Drive?\n(Thư mục gốc trên Google Drive và toàn bộ dữ liệu trên máy vẫn được giữ nguyên).`)) return;
  try {
    await api('/api/delete-drive-folder', { folder: folder || '', url: url || '' });
    await fetchState();
  } catch (e) {
    toastError(e);
  }
}

$('add-drive-btn').onclick = async () => {
  const urlInput = $('new-drive-url');
  const folderInput = $('new-drive-folder');
  const url = urlInput.value.trim();
  const folder = folderInput.value.trim();

  if (!url) {
    alert('Vui lòng nhập link Google Drive.');
    return;
  }
  if (!url.includes('drive.google.com') || !url.includes('/folders/')) {
    alert('Link Drive phải có định dạng https://drive.google.com/drive/folders/...');
    return;
  }

  // Check if duplicate
  const folderId = driveFolderId(url);
  const existingItem = driveUrlsQueue.find(i => driveFolderId(i.url) === folderId);
  const requestedFolder = folder || (existingItem && existingItem.folder) || '';
  const requestedFolderKey = requestedFolder.trim().toLocaleLowerCase('vi');
  const isHidden = hiddenDriveFolders.some(item => String(item || '').trim().toLocaleLowerCase('vi') === requestedFolderKey);
  const existingFolderKey = String((existingItem && existingItem.folder) || '').trim().toLocaleLowerCase('vi');
  if (existingItem && existingFolderKey === requestedFolderKey && !isHidden) {
    alert('Folder ID này đã có trong danh sách. Không thể thêm bản ghi trùng.');
    return;
  }
  if (requestedFolder) {
    try {
      await api('/api/save-drive-link', { folder: requestedFolder, url });
      driveFolderSearch = '';
      driveFolderPage = 1;
      $('drive-folder-search').value = '';
      urlInput.value = '';
      folderInput.value = '';
      await fetchState();
    } catch (e) {
      toastError(e);
    }
    return;
  }

  driveUrlsQueue.push({ url, folder, modified_at: new Date().toISOString() });
  driveFolderSearch = '';
  driveFolderPage = 1;
  $('drive-folder-search').value = '';
  urlInput.value = '';
  folderInput.value = '';

  try {
    await api('/api/save', payload());
    await fetchState();
  } catch (e) {
    toastError(e);
  }
};

function toastError(err) {
  const msg = err && err.message ? err.message : String(err || 'Đã xảy ra lỗi không xác định.');
  alert('⚠️ Lỗi: ' + msg);
}

async function api(path, body) {
  try {
    const r = await fetch(path, {
      method: body ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined
    });
    let j = {};
    try {
      j = await r.json();
    } catch(err) {
      j = { error: `Máy chủ phản hồi mã ${r.status}` };
    }
    if (!r.ok) throw new Error(j.error || `Lỗi máy chủ (${r.status})`);
    return j;
  } catch (err) {
    if (err && err.name === 'TypeError' && String(err.message).toLowerCase().includes('fetch')) {
      throw new Error('Mất kết nối tới VEO3_AUTO_APP. Hãy đảm bảo ứng dụng đang mở.');
    }
    throw err;
  }
}

function sourceMode() { return $('source-local').checked ? 'local' : 'drive'; }
function texPromptMode() { return $('tex-prompt-mode-manual').checked ? 'manual' : 'attachment'; }
function fabPromptMode() { return $('fab-prompt-mode-manual').checked ? 'manual' : 'attachment'; }
function flowPromptMode() { return $('flow-prompt-mode-manual').checked ? 'manual' : 'attachment'; }

const qualityImages = new Map();
let qualityRerunFolder = '';
function openQualityRerun() {
  const folder = qualityFolderGroups[activeQualityFolderIndex];
  if (!folder) { addQualityError('Hãy chọn folder vải trước.'); return; }
  qualityRerunFolder = folder.path;
  $('quality-rerun-folder').textContent = folder.path;
  $('quality-rerun-list').replaceChildren();
  const failures = folder.images.filter(image => image.failed);
  for (const file of failures) {
    const row = document.createElement('label');
    row.className = 'quality-rerun-row';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = file.path;
    checkbox.onchange = updateQualityRerunSelection;
    const img = document.createElement('img');
    img.src = '/api/quality-image?path=' + encodeURIComponent(file.path);
    img.alt = ''; img.loading = 'lazy';
    const name = document.createElement('span');
    name.textContent = file.relative_path || file.name;
    name.title = file.path;
    row.append(checkbox, img, name);
    $('quality-rerun-list').append(row);
  }
  $('quality-rerun-status').textContent = failures.length ? 'Chọn ảnh cần tạo lại.' : 'Folder này không có ảnh được đánh dấu fail.';
  updateQualityRerunSelection();
  $('quality-rerun-dialog').showModal();
}
function updateQualityRerunSelection() {
  const all = [...$('quality-rerun-list').querySelectorAll('input')];
  const count = all.filter(input => input.checked).length;
  $('quality-rerun-submit').disabled = count === 0;
  $('quality-rerun-all').disabled = all.length === 0;
  $('quality-rerun-all').textContent = count === all.length && count ? 'Bỏ chọn tất cả' : 'Chọn tất cả';
}
function toggleAllQualityRerun() {
  const all = [...$('quality-rerun-list').querySelectorAll('input')];
  const checked = !all.every(input => input.checked);
  all.forEach(input => input.checked = checked);
  updateQualityRerunSelection();
}
async function submitQualityRerun() {
  const inputs = [...$('quality-rerun-list').querySelectorAll('input')];
  const paths = inputs.filter(input => input.checked).map(input => input.value);
  if (!paths.length || $('quality-rerun-submit').disabled) return;
  $('quality-rerun-submit').disabled = true;
  $('quality-rerun-all').disabled = true;
  inputs.forEach(input => input.disabled = true);
  try {
    const result = await api('/api/quality-rerun', {folder: qualityRerunFolder, image_paths: paths});
    $('quality-rerun-dialog').close();
    $('quality-active-folder').textContent = `Đã bắt đầu tạo lại ${result.sku_count} SKU qua ChatGPT. Xem tiến trình trong nhật ký.`;
  } catch (error) {
    $('quality-rerun-status').textContent = error.message;
  } finally {
    inputs.forEach(input => input.disabled = false);
    updateQualityRerunSelection();
  }
}
const qualityErrors = [];
let qualityUploading = false;
let selectedQualityItem = null;
let qualityFolderGroups = [];
let activeQualityFolderIndex = -1;
function addQualityError(message) {
  qualityErrors.push(message);
  if (qualityErrors.length > 100) qualityErrors.shift();
  $('quality-errors').textContent = qualityErrors.join('\n');
  $('quality-errors').classList.add('has-errors');
}
function clearQualityErrors() {
  qualityErrors.length = 0;
  $('quality-errors').textContent = 'Chưa có lỗi.';
  $('quality-errors').classList.remove('has-errors');
}
function validateQualityWebsite() {
  const input = $('quality-website');
  const value = input.value.trim();
  try {
    if (value && !['http:', 'https:'].includes(new URL(value).protocol)) throw new Error();
    input.removeAttribute('aria-invalid');
    return true;
  } catch (_) {
    input.setAttribute('aria-invalid', 'true');
    addQualityError('Link trang web không hợp lệ. Vui lòng nhập địa chỉ bắt đầu bằng http:// hoặc https://.');
    return false;
  }
}
function updateQualitySummary() {
  $('quality-count').textContent = `${qualityImages.size} ảnh`;
  $('quality-empty').classList.toggle('hidden', qualityImages.size > 0);
  const folder = qualityFolderGroups[activeQualityFolderIndex];
  $('quality-empty').querySelector('strong').textContent = folder ? 'Folder này chưa có ảnh' : 'Chưa có ảnh mẫu vải';
  $('quality-empty').querySelector('span').textContent = folder ? `Không có file ảnh được hỗ trợ trong ${folder.name}. Chọn folder khác bên trên để xem ảnh.` : 'Chọn folder vải để xem các ảnh output tại đây.';
}
async function selectQualityFolder() {
  const button = $('quality-folder-button');
  button.disabled = true;
  try {
    const result = await api('/api/select-quality-folder', {});
    if (!result.path) return;
    qualityFolderGroups = result.folders || [];
    activeQualityFolderIndex = -1;
    renderQualityFolderButtons();
    if (qualityFolderGroups.length) showQualityFolder(0);
    else {
      clearQualityImages();
      addQualityError('Folder đã chọn không chứa file ảnh được hỗ trợ.');
    }
  } catch (error) {
    addQualityError(error.message);
  } finally {
    button.disabled = false;
  }
}
function clearQualityImages() {
  $('quality-active-folder').textContent = '';
  qualityImages.clear();
  selectedQualityItem = null;
  $('quality-grid').replaceChildren();
  updateQualitySummary();
}
function renderQualityFolderButtons() {
  const container = $('quality-folders');
  container.replaceChildren();
  qualityFolderGroups.forEach((folder, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'ghost btn-sm quality-folder';
    button.textContent = `📁 ${folder.name} (${folder.images.length})`;
    button.setAttribute('aria-pressed', 'false');
    button.title = folder.path;
    button.onclick = () => showQualityFolder(index);
    folder.button = button;
    container.append(button);
  });
}
function showQualityFolder(index) {
  if (index < 0 || index >= qualityFolderGroups.length) return;
  activeQualityFolderIndex = index;
  qualityFolderGroups.forEach((folder, position) => {
    folder.button?.classList.toggle('active', position === index);
    folder.button?.setAttribute('aria-pressed', String(position === index));
  });
  clearQualityImages();
  const folder = qualityFolderGroups[index];
  $('quality-active-folder').textContent = `Nhóm vải: ${folder.name}`;
  $('quality-grid').closest('.quality-gallery').scrollTop = 0;
  const images = folder.images || [];
  const fragment = document.createDocumentFragment();
  const renderedNodes = new Map();
  for (const file of images) {
    const path = file.relative_path || file.name;
    const key = file.path;
    if (qualityImages.has(key)) continue;
    try {
      const node = document.createElement('figure');
      node.className = 'quality-image';
      node.tabIndex = 0;
      node.role = 'button';
      node.title = 'Bấm để gửi ảnh này lên trang web mục tiêu';
      const img = document.createElement('img');
      img.alt = file.name;
      img.loading = 'lazy';
      img.decoding = 'async';
      img.onerror = () => {
        if (!qualityImages.has(key)) return;
        addQualityError(`Không thể hiển thị ảnh: ${path}. File có thể bị hỏng hoặc định dạng không được trình duyệt hỗ trợ.`);
      };
      img.src = '/api/quality-image?path=' + encodeURIComponent(file.path);
      const caption = document.createElement('figcaption');
      caption.textContent = path;
      node.append(img, caption);
      const item = {path: file.path, name: file.name, folder: folder.path, node};
      const failToggle = document.createElement('button');
      failToggle.type = 'button';
      failToggle.className = 'quality-fail-toggle';
      failToggle.setAttribute('role', 'checkbox');
      const renderFailure = () => {
        node.classList.toggle('failed', !!file.failed);
        failToggle.setAttribute('aria-checked', String(!!file.failed));
        failToggle.textContent = file.failed ? '×' : '';
        failToggle.title = file.failed ? 'Bỏ đánh dấu ảnh fail' : 'Đánh dấu ảnh fail';
        failToggle.setAttribute('aria-label', `${failToggle.title}: ${path}`);
      };
      renderFailure();
      failToggle.onkeydown = event => event.stopPropagation();
      failToggle.onclick = async event => {
        event.stopPropagation();
        failToggle.disabled = true;
        try {
          const result = await api('/api/quality-image-failure', {image_path: file.path, failed: !file.failed});
          file.failed = result.failed;
          // Also update a newly rendered copy if the user switched folders while saving.
          qualityFolderGroups.forEach(group => group.images.forEach(image => {
            if (image.path === file.path) image.failed = result.failed;
          }));
          renderFailure();
          qualityImages.get(file.path)?.renderFailure?.();
        } catch (error) {
          addQualityError(`Không lưu được đánh dấu fail: ${error.message}`);
        } finally {
          failToggle.disabled = false;
        }
      };
      item.renderFailure = renderFailure;
      node.append(failToggle);
      node.onclick = () => uploadQualityImage(item);
      node.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); uploadQualityImage(item); }
      };
      qualityImages.set(key, item);
      renderedNodes.set(key, node);
    } catch (error) {
      addQualityError(`Không thể đọc ảnh ${path}: ${error.message}`);
    }
  }
  const pairName = name => {
    const lower = (name || '').toLowerCase();
    return lower === 'seamless_texture.png' ? 'final' :
      lower === 'seamless_texture_chatgpt_raw.png' ? 'raw' : '';
  };
  const parentKey = file => (file.path || '').replace(/\\/g, '/').replace(/\/[^/]*$/, '').toLowerCase();
  const pairs = new Map();
  for (const file of images) {
    const kind = pairName(file.name);
    if (!kind) continue;
    const key = parentKey(file);
    const pair = pairs.get(key) || {};
    pair[kind] = file;
    pairs.set(key, pair);
  }
  const appended = new Set();
  for (const file of images) {
    if (appended.has(file.path) || !renderedNodes.has(file.path)) continue;
    const pair = pairs.get(parentKey(file));
    if (pair?.final && pair?.raw && pairName(file.name)) {
      const wrapper = document.createElement('div');
      wrapper.className = 'quality-pair';
      wrapper.append(renderedNodes.get(pair.final.path), renderedNodes.get(pair.raw.path));
      fragment.append(wrapper);
      appended.add(pair.final.path);
      appended.add(pair.raw.path);
    } else {
      fragment.append(renderedNodes.get(file.path));
      appended.add(file.path);
    }
  }
  $('quality-grid').append(fragment);
  updateQualitySummary();
}
async function uploadQualityImage(item) {
  if (qualityUploading) return;
  if (selectedQualityItem === item) return;
  clearQualityErrors();
  if (!validateQualityWebsite()) return;
  if (selectedQualityItem) selectedQualityItem.node.classList.remove('selected');
  selectedQualityItem = item;
  item.node.classList.add('selected');
  qualityUploading = true;
  item.node.classList.add('uploading');
  $('quality-count').textContent = 'Đang gửi ảnh...';
  try {
    const frame = $('quality-website-frame');
    const response = await fetch('/api/quality-image?path=' + encodeURIComponent(item.path), {cache: 'no-store'});
    if (!response.ok) throw new Error('Không đọc được file ảnh đã chọn.');
    const blob = await response.blob();
    const bytes = await blob.arrayBuffer();
    const requestId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    const targetOrigin = 'https://dunniotailor.com';
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        window.removeEventListener('message', receiveResult);
        reject(new Error('Dunnio Tailor không phản hồi yêu cầu nhận ảnh.'));
      }, 8000);
      function receiveResult(event) {
        if (event.origin !== targetOrigin || event.source !== frame.contentWindow) return;
        const message = event.data;
        if (message?.type !== 'DUNNIO_ATTACH_IMAGE_RESULT' || message.requestId !== requestId) return;
        clearTimeout(timeout);
        window.removeEventListener('message', receiveResult);
        message.success ? resolve(message) : reject(new Error(message.error || 'Dunnio Tailor không nhận được ảnh.'));
      }
      window.addEventListener('message', receiveResult);
      frame.contentWindow.postMessage({
        type: 'DUNNIO_ATTACH_IMAGE',
        requestId,
        payload: {
          name: item.name,
          mime: blob.type || 'application/octet-stream',
          bytes
        }
      }, targetOrigin, [bytes]);
    });
    $('quality-count').textContent = `Đã gửi ${item.name}`;
  } catch (error) {
    addQualityError(error.message);
    updateQualitySummary();
  } finally {
    qualityUploading = false;
    item.node.classList.remove('uploading');
  }
}
function reloadQualityFrame() {
  const frame = $('quality-website-frame');
  const url = new URL(frame.src); url.searchParams.set('reload', Date.now()); frame.src = url.href;
}

let activeTab = 'dashboard';
let activePromptSubTab = 'texture';

function switchTab(tabId) {
  if (tabId === 'logs') {
    openLogPopup();
    return;
  }
  activeTab = tabId;
  try { sessionStorage.setItem('veo3_active_tab', tabId); } catch(e) {}

  $('tab-dashboard').classList.toggle('hidden', tabId !== 'dashboard');
  $('tab-progress').classList.toggle('hidden', tabId !== 'progress');
  $('tab-quality').classList.toggle('hidden', tabId !== 'quality');

  $('nav-tab-dashboard').classList.toggle('active', tabId === 'dashboard');
  $('nav-tab-progress').classList.toggle('active', tabId === 'progress');
  $('nav-tab-quality').classList.toggle('active', tabId === 'quality');
}

function openDriveManagementModal() {
  $('drive-management-modal').classList.remove('hidden');
  $('nav-drive-management').classList.add('active');
  $('nav-drive-management').setAttribute('aria-expanded', 'true');
}

function closeDriveManagementModal() {
  $('drive-management-modal').classList.add('hidden');
  $('nav-drive-management').classList.remove('active');
  $('nav-drive-management').setAttribute('aria-expanded', 'false');
}

function openSettingsModal() {
  const modal = $('settings-modal');
  // Keep the settings available from every main tab. It starts beside the
  // dashboard markup for readability, then moves outside the tab container.
  if (modal.parentElement !== document.body) document.body.appendChild(modal);
  modal.classList.remove('hidden');
  $('nav-settings').classList.add('active');
  $('nav-settings').setAttribute('aria-expanded', 'true');
}

function closeSettingsModal() {
  $('settings-modal').classList.add('hidden');
  $('nav-settings').classList.remove('active');
  $('nav-settings').setAttribute('aria-expanded', 'false');
}

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !$('drive-management-modal').classList.contains('hidden')) {
    closeDriveManagementModal();
  }
  if (event.key === 'Escape' && !$('settings-modal').classList.contains('hidden')) {
    closeSettingsModal();
  }
});

function openLogPopup() {
  $('tab-logs').classList.remove('hidden');
  $('nav-tab-logs').classList.add('active');
  $('nav-tab-logs').setAttribute('aria-expanded', 'true');
  $('log').scrollTop = $('log').scrollHeight;
}

function closeLogPopup() {
  $('tab-logs').classList.add('hidden');
  $('nav-tab-logs').classList.remove('active');
  $('nav-tab-logs').setAttribute('aria-expanded', 'false');
}

function switchPromptSubTab(subTabId) {
  activePromptSubTab = subTabId;
  $('prompt-subtab-tex').classList.toggle('active', subTabId === 'texture');
  $('prompt-subtab-fab').classList.toggle('active', subTabId === 'fabric');
  $('prompt-subtab-flow').classList.toggle('active', subTabId === 'flow');

  $('prompt-panel-tex').classList.toggle('hidden', subTabId !== 'texture');
  $('prompt-panel-fab').classList.toggle('hidden', subTabId !== 'fabric');
  $('prompt-panel-flow').classList.toggle('hidden', subTabId !== 'flow');
}

function requestShutdownApp() {
  if (confirm('Đóng VEO3 Auto Pipeline?')) {
    api('/api/shutdown', {}).then(() => window.close()).catch(toastError);
  }
}

function payload() {
  const local = sourceMode() === 'local';
  return {
    source_mode: sourceMode(),
    local_source_dir: $('local-folder').value.trim(),
    drive_urls: driveUrlsQueue.filter(i => i.url),
    sku: $('sku').value.trim(),
    limit: $('limit').value.trim(),
    images_per_chat: Number($('perchat').value || 10),
    auto_retry_enabled: $('auto-retry').checked,
    auto_retry_delay_seconds: Number($('retry-delay').value || 120),
    auto_retry_max_attempts: Number($('retry-max').value || 10),
    dry_run: $('dry').checked,
    force: $('force').checked,
    seamless_engine: (!$('seamless-engine-chatgpt') || $('seamless-engine-chatgpt').checked) ? 'chatgpt' : 'algo',
    texture_prompt_mode: texPromptMode(),
    texture_prompt_file: $('tex-prompt-file').value.trim(),
    texture_prompt_text: $('tex-prompt-text').value,
    fabric_prompt_mode: fabPromptMode(),
    fabric_prompt_file: $('fab-prompt-file').value.trim(),
    fabric_prompt_text: $('fab-prompt-text').value,
    flow_prompt_mode: flowPromptMode(),
    flow_prompt_file: $('flow-prompt-file').value.trim(),
    flow_prompt_text: $('flow-prompt-text').value,
    telegram: {
      enabled: $('tele-enabled').checked,
      bot_token: $('tele-token').value.trim(),
      chat_id: $('tele-chat-id').value.trim(),
      notify_on_start: $('tele-start').checked,
      notify_periodic_progress: $('tele-periodic').checked,
      notify_on_failure: $('tele-failure').checked,
      notify_on_complete: $('tele-complete').checked
    },
    flows: {
      import: !local && $('flow-import').checked,
      crop: $('flow-crop').checked,
      seamless: $('flow-seamless').checked,
      fabric: $('flow-fabric').checked,
      package: $('flow-package').checked
    }
  };
}

async function prepareSwatchPrerequisites(pl) {
  if (!pl?.flows?.fabric || pl.flows.seamless) return true;
  const check = await api('/api/check-swatch-prerequisites', pl);
  if (check.local_fallback) pl.flows.import = false;
  if (!check.missing_count) return true;
  const preview = (check.missing_skus || []).slice(0, 8).join(', ');
  const more = check.missing_count > 8 ? ` và ${check.missing_count - 8} SKU khác` : '';
  const reason = `Có ${check.missing_count} SKU chưa có seamless_texture.png${preview ? `: ${preview}${more}` : ''}.`;
  const accepted = confirm(
    reason + '\n\n' +
    'Tự động tạo seamless cho các SKU còn thiếu, sau đó tạo swatch tương ứng?'
  );
  if (!accepted) return false;
  pl.flows.crop = true;
  pl.flows.seamless = true;
  pl.flows.package = true;
  pl.seamless_missing_only = true;
  pl.seamless_engine = 'chatgpt';
  return true;
}

function toastError(e) { alert(e.message || e); }

function updateSourceUI() {
  const local = sourceMode() === 'local';
  $('drive-source').classList.toggle('hidden', local);
  $('local-source').classList.toggle('hidden', !local);
  $('flow-import').disabled = local;
  if (local) $('flow-import').checked = false;
}

function updatePromptCounts() {
  const texLen = ($('tex-prompt-text').value || '').length;
  const fabLen = ($('fab-prompt-text').value || '').length;
  const flowLen = ($('flow-prompt-text').value || '').length;
  $('tex-prompt-count').textContent = texLen.toLocaleString() + ' ký tự';
  $('fab-prompt-count').textContent = fabLen.toLocaleString() + ' ký tự';
  $('flow-prompt-count').textContent = flowLen.toLocaleString() + ' ký tự';
}

function toggleTelegramInputs() {
  const enabled = $('tele-enabled').checked;
  const box = $('tele-config-box');
  if (box) {
    box.style.opacity = enabled ? '1' : '0.6';
  }
}

async function testTelegramConnection() {
  const btn = $('tele-test-btn');
  const msg = $('tele-status-msg');
  const token = $('tele-token').value.trim();
  const chatId = $('tele-chat-id').value.trim();
  if (!token || !chatId) {
    toastError('Vui lòng nhập Bot Token và Chat ID trước khi test.');
    return;
  }
  btn.disabled = true;
  btn.textContent = '⏳ Đang gửi...';
  msg.textContent = '';
  try {
    const res = await api('/api/test-telegram', { bot_token: token, chat_id: chatId });
    if (res.ok) {
      msg.innerHTML = '<span style="color:var(--green)">✅ ' + escapeHtml(res.message || 'Kết nối thành công!') + '</span>';
    } else {
      msg.innerHTML = '<span style="color:var(--red)">❌ ' + escapeHtml(res.error || 'Lỗi gửi tin') + '</span>';
    }
  } catch(e) {
    msg.innerHTML = '<span style="color:var(--red)">❌ ' + escapeHtml(e.message) + '</span>';
  } finally {
    btn.disabled = false;
    btn.textContent = '🧪 Gửi tin nhắn thử (Test)';
  }
}

async function saveTelegramSettings() {
  const p = payload();
  const msg = $('tele-status-msg');
  try {
    await api('/api/save', p);
    msg.innerHTML = '<span style="color:var(--green)">💾 Đã lưu cấu hình Telegram!</span>';
    setTimeout(() => { if (msg) msg.textContent = ''; }, 3500);
  } catch(e) {
    toastError('Lỗi lưu cấu hình Telegram: ' + e.message);
  }
}

function updatePromptUI() {
  const texManual = texPromptMode() === 'manual';
  $('tex-attachment-box').classList.toggle('hidden', texManual);
  $('tex-manual-box').classList.toggle('hidden', !texManual);
  $('tex-prompt-badge').textContent = texManual ? 'Nhập thủ công' : 'Đính kèm file';
  $('tex-prompt-badge').className = 'badge ' + (texManual ? 'pending' : 'ok');

  const fabManual = fabPromptMode() === 'manual';
  $('fab-attachment-box').classList.toggle('hidden', fabManual);
  $('fab-manual-box').classList.toggle('hidden', !fabManual);
  $('fab-prompt-badge').textContent = fabManual ? 'Nhập thủ công' : 'Đính kèm file';
  $('fab-prompt-badge').className = 'badge ' + (fabManual ? 'pending' : 'ok');

  const flowManual = flowPromptMode() === 'manual';
  $('flow-attachment-box').classList.toggle('hidden', flowManual);
  $('flow-manual-box').classList.toggle('hidden', !flowManual);
  $('flow-prompt-badge').textContent = flowManual ? 'Nhập thủ công' : 'Đính kèm file';
  $('flow-prompt-badge').className = 'badge ' + (flowManual ? 'pending' : 'ok');

  updatePromptCounts();
}

function formatSecToMin(sec) {
  if (!sec || isNaN(sec) || sec <= 0) return '0s';
  sec = Math.round(sec);
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m + 'm ' + (s > 0 ? (s < 10 ? '0' + s : s) + 's' : '');
}

function formatStopwatch(sec) {
  if (!sec || isNaN(sec) || sec < 0) return '00:00:00';
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return (h < 10 ? '0' + h : h) + ':' + (m < 10 ? '0' + m : m) + ':' + (s < 10 ? '0' + s : s);
}

function renderSkuGrid(containerId, items, kind) {
  const box = $(containerId);
  box.replaceChildren();
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'hint';
    empty.style.padding = '20px';
    empty.style.gridColumn = '1 / -1';
    empty.style.textAlign = 'center';
    empty.textContent = 'Không có SKU nào';
    box.appendChild(empty);
    return;
  }
  for (const rawItem of items) {
    const item = typeof rawItem === 'string'
      ? { sku: rawItem, folder: '', has_seamless: kind === 'created', has_fabric: kind === 'created' }
      : rawItem;
    const sku = item.sku;
    const itemFolder = item.folder || (currentSelectedFolder !== 'all' ? currentSelectedFolder : '');
    const card = document.createElement('div');
    card.className = 'sku-card ' + kind;
    card.setAttribute('data-sku', sku);
    card.onclick = () => openSkuModal(sku, itemFolder || null);

    const wrap = document.createElement('div');
    wrap.className = 'sku-thumb-wrap';

    const img = document.createElement('img');
    img.className = 'sku-thumb';
    img.loading = 'lazy';
    img.alt = sku;
    const folderParam = itemFolder ? ('&folder=' + encodeURIComponent(itemFolder)) : '';
    if (item.has_seamless) {
      img.src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=seamless' + folderParam;
      img.onerror = () => { img.src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=cropped' + folderParam; };
    } else {
      img.src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=cropped' + folderParam;
      img.onerror = () => { img.src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=raw' + folderParam; };
    }

    const dot = document.createElement('span');
    dot.className = 'sku-dot ' + (kind === 'created' ? 'ok' : 'pending');

    wrap.appendChild(img);
    wrap.appendChild(dot);

    const name = document.createElement('div');
    name.className = 'sku-card-name';
    name.textContent = sku;
    name.title = sku;

    const statuses = document.createElement('div');
    statuses.className = 'sku-statuses';
    const seamlessStatus = document.createElement('span');
    seamlessStatus.className = 'sku-status-pill ' + (item.has_seamless ? 'ok' : 'missing');
    seamlessStatus.textContent = (item.has_seamless ? '✓ ' : '− ') + 'Seamless';
    const fabricStatus = document.createElement('span');
    fabricStatus.className = 'sku-status-pill ' + (item.has_fabric ? 'ok' : 'missing');
    fabricStatus.textContent = (item.has_fabric ? '✓ ' : '− ') + 'Swatch';
    statuses.appendChild(seamlessStatus);
    statuses.appendChild(fabricStatus);

    card.appendChild(wrap);
    card.appendChild(statuses);
    card.appendChild(name);
    box.appendChild(card);
  }
}

function filterSkuCards(kind) {
  const searchInput = kind === 'created' ? $('search-created') : $('search-pending');
  const term = (searchInput.value || '').trim().toLowerCase();
  const box = kind === 'created' ? $('created-list') : $('pending-list');
  const cards = box.querySelectorAll('.sku-card');
  for (const card of cards) {
    const sku = (card.getAttribute('data-sku') || '').toLowerCase();
    card.style.display = (!term || sku.includes(term)) ? 'flex' : 'none';
  }
}

function arraysEqual(a, b) {
  if (a === b) return true;
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (JSON.stringify(a[i]) !== JSON.stringify(b[i])) return false;
  }
  return true;
}

function renderFabricProgress(p, timing) {
  p = p || {};
  timing = timing || {};
  const percentText = (p.percent || 0) + '%';
  $('fabric-percent').textContent = percentText;
  const navBadge = $('nav-progress-badge');
  if (navBadge) navBadge.textContent = percentText;
  $('fabric-total').textContent = p.total || 0;
  $('fabric-created').textContent = p.created_count || 0;
  $('fabric-pending').textContent = p.pending_count || 0;
  $('created-count').textContent = p.created_count || 0;
  $('pending-count').textContent = p.pending_count || 0;
  $('fabric-progress-bar').style.width = (p.percent || 0) + '%';

  const newCreated = p.created || [];
  const newPending = p.pending || [];
  if (!arraysEqual(lastCreatedList, newCreated)) {
    lastCreatedList = newCreated;
    renderSkuGrid('created-list', newCreated, 'created');
    filterSkuCards('created');
  }
  if (!arraysEqual(lastPendingList, newPending)) {
    lastPendingList = newPending;
    renderSkuGrid('pending-list', newPending, 'pending');
    filterSkuCards('pending');
  }

  $('fabric-paths').textContent = 'Nguồn: ' + (p.source_dir || '-') + '  •  Kết quả: ' + (p.output_dir || '-') + (p.source_exists === false ? '  •  Thư mục nguồn không tồn tại' : '');

  // Timing metrics
  const running = Boolean(timing.session_running);
  const sessionSec = timing.session_duration_seconds || 0;
  $('session-time').textContent = formatStopwatch(sessionSec);
  const cardSession = $('card-session-time');
  if (running) {
    cardSession.classList.add('running-timer');
    $('session-status-sub').textContent = 'Đang chạy pipeline...';
  } else {
    cardSession.classList.remove('running-timer');
    $('session-status-sub').textContent = sessionSec > 0 ? 'Phiên vừa hoàn tất' : 'Sẵn sàng';
  }

  const createdInSession = timing.session_created_count || 0;
  $('session-count').textContent = createdInSession + ' ảnh';
  if (running && sessionSec > 0 && createdInSession > 0) {
    $('session-speed-sub').textContent = formatSecToMin(sessionSec / createdInSession) + ' / ảnh phiên này';
  } else {
    $('session-speed-sub').textContent = createdInSession > 0 ? (formatSecToMin(timing.session_avg_seconds) + ' / ảnh') : '-';
  }

  const avgSec = timing.effective_avg_seconds || timing.historical_avg_seconds || 0;
  $('avg-time').textContent = avgSec > 0 ? formatSecToMin(avgSec) : '--';
  $('avg-calc-sub').textContent = timing.session_avg_seconds > 0 && createdInSession >= 2 ? 'Trung bình theo phiên hiện tại' : 'Dựa trên lịch sử ChatGPT';

  const etaSec = timing.eta_seconds || 0;
  if (running && (p.pending_count || 0) > 0 && avgSec > 0) {
    $('eta-time').textContent = '~' + formatSecToMin(etaSec);
    $('eta-sub').textContent = 'Còn ' + (p.pending_count || 0) + ' SKU thiếu Seamless hoặc Swatch';
  } else if ((p.pending_count || 0) === 0) {
    $('eta-time').textContent = 'Đã hoàn thành';
    $('eta-sub').textContent = 'Tất cả SKU có đủ Seamless và Swatch';
  } else {
    $('eta-time').textContent = 'Chưa hoàn thành';
    $('eta-sub').textContent = (p.pending_count || 0) + ' SKU thiếu Seamless hoặc Swatch';
  }
}

// Modal logic
async function openSkuModal(sku, folder) {
  currentModalSku = sku;
  currentModalFolder = folder || (currentSelectedFolder !== 'all' ? currentSelectedFolder : null);
  $('modal-sku-title').textContent = 'Mã SKU: ' + sku;
  $('sku-modal').classList.remove('hidden');

  const folderParam = currentModalFolder ? ('&folder=' + encodeURIComponent(currentModalFolder)) : '';

  $('modal-cropped-img').style.display = 'block';
  $('modal-cropped-img').src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=cropped' + folderParam + '&t=' + Date.now();
  $('modal-cropped-empty').classList.add('hidden');

  $('modal-output-img').style.display = 'block';
  $('modal-output-img').src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=final_seamless' + folderParam + '&t=' + Date.now();
  $('modal-output-empty').classList.add('hidden');

  $('modal-fabric-img').style.display = 'block';
  $('modal-fabric-img').src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=fabric' + folderParam + '&t=' + Date.now();
  $('modal-fabric-empty').classList.add('hidden');

  $('modal-cropped-dim').textContent = '--';
  $('modal-cropped-size').textContent = '--';
  $('modal-output-dim').textContent = 'Đang kiểm tra Seamless 2K...';
  $('modal-output-dim').className = 'preview-result-status badge';
  $('modal-output-size').textContent = '--';
  $('modal-output-time').textContent = '--';
  $('modal-fabric-dim').textContent = 'Đang kiểm tra Swatch 4:3...';
  $('modal-fabric-dim').className = 'preview-result-status badge';
  $('modal-fabric-size').textContent = '--';
  $('modal-fabric-time').textContent = '--';
  $('modal-notice').textContent = '';
  $('modal-notice').classList.add('hidden');
  $('modal-folder').textContent = currentModalFolder || 'Mặc định / Tự phát hiện';
  $('modal-completed-at').textContent = '--';
  $('modal-duration').textContent = '--';
  $('modal-output-path').textContent = '--';

  try {
    const info = await api('/api/sku-info?sku=' + encodeURIComponent(sku) + folderParam);
    if (info.folder) {
      $('modal-folder').textContent = info.folder;
      currentModalFolder = info.folder;
    }
    if (info.cropped) {
      $('modal-cropped-dim').textContent = info.cropped.width ? (info.cropped.width + ' × ' + info.cropped.height + ' px') : 'Ảnh cắt';
      $('modal-cropped-size').textContent = info.cropped.size_formatted || '--';
    } else {
      $('modal-cropped-img').style.display = 'none';
      $('modal-cropped-empty').classList.remove('hidden');
    }

    if (info.output) {
      const outputSize = info.output.width ? ` (${info.output.width} × ${info.output.height} px)` : '';
      $('modal-output-dim').textContent = 'Đã tạo Seamless 2K' + outputSize;
      $('modal-output-dim').className = 'preview-result-status badge ok';
      $('modal-output-size').textContent = info.output.size_formatted || '--';
      $('modal-output-time').textContent = info.duration_text || '--';
      $('modal-output-path').textContent = info.output.path || '--';
    } else {
      $('modal-output-img').style.display = 'none';
      $('modal-output-empty').classList.remove('hidden');
      $('modal-output-dim').textContent = 'Chưa tạo Seamless 2K';
      $('modal-output-dim').className = 'preview-result-status badge pending';
    }

    if (info.fabric) {
      const fabricSize = info.fabric.width ? ` (${info.fabric.width} × ${info.fabric.height} px)` : '';
      $('modal-fabric-dim').textContent = 'Đã tạo swatch 4:3' + fabricSize;
      $('modal-fabric-dim').className = 'preview-result-status badge ok';
      $('modal-fabric-size').textContent = info.fabric.size_formatted || '--';
      $('modal-fabric-time').textContent = info.fabric_duration_text || '--';
    } else {
      $('modal-fabric-img').style.display = 'none';
      $('modal-fabric-empty').classList.remove('hidden');
      $('modal-fabric-dim').textContent = 'Chưa tạo swatch 4:3';
      $('modal-fabric-dim').className = 'preview-result-status badge pending';
    }

    if (info.quota_info) {
      $('modal-notice').textContent = '⏳ Chờ quota: ' + info.quota_info;
      $('modal-notice').classList.remove('hidden');
    }
    if (!info.output) {
      $('modal-output-path').textContent = (currentModalFolder ? ('output/chatgpt/' + currentModalFolder + '/') : 'output/chatgpt/') + sku + '/';
    }

    if (info.status_record) {
      $('modal-completed-at').textContent = info.status_record.completed_at || info.status_record.started_at || '--';
      $('modal-duration').textContent = info.duration_text || '--';
    }
  } catch (e) {
    $('modal-notice').textContent = 'Lỗi đọc chi tiết: ' + e.message;
    $('modal-notice').classList.remove('hidden');
  }
}

function closeSkuModal() {
  $('sku-modal').classList.add('hidden');
  currentModalSku = null;
}

$('modal-rerun-sku').onclick = async () => {
  if (!currentModalSku) return;
  const sku = currentModalSku;
  const folder = currentModalFolder || '';
  if (!confirm(`Chạy tạo lại ảnh qua ChatGPT cho riêng SKU: ${sku}${folder ? ' trong thư mục ' + folder : ''}?`)) return;
  try {
    const result = await api('/api/run-sku', { sku: sku, folder: folder, images_per_chat: 1, engine: 'chatgpt' });
    if (result.started === false) {
      alert(result.message || 'SKU đã có đủ Seamless và Swatch.');
      return;
    }
    closeSkuModal();
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) {
    toastError(e);
  }
};

$('modal-rerun-algo-sku').onclick = async () => {
  if (!currentModalSku) return;
  const sku = currentModalSku;
  const folder = currentModalFolder || '';
  if (!confirm(`Tạo texture bằng Thuật toán Offline cho riêng SKU: ${sku}${folder ? ' trong thư mục ' + folder : ''}?`)) return;
  try {
    closeSkuModal();
    await api('/api/run-sku', { sku: sku, folder: folder, force: true, engine: 'algo' });
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) {
    toastError(e);
  }
};

$('modal-rerun-flow-sku').onclick = async () => {
  if (!currentModalSku) return;
  const sku = currentModalSku;
  const folder = currentModalFolder || '';
  if (!confirm(`Chạy tạo lại ảnh qua Google Flow cho riêng SKU: ${sku}${folder ? ' trong thư mục ' + folder : ''}?`)) return;
  try {
    closeSkuModal();
    await api('/api/run-sku', { sku: sku, folder: folder, force: true, engine: 'flow' });
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) {
    toastError(e);
  }
};

$('modal-open-folder').onclick = async () => {
  if (!currentModalSku) return;
  try {
    await api('/api/open-folder', { sku: currentModalSku, folder: currentModalFolder || '' });
  } catch (e) {
    toastError(e);
  }
};

$('modal-view-full').onclick = () => {
  if (!currentModalSku) return;
  const folderParam = currentModalFolder ? ('&folder=' + encodeURIComponent(currentModalFolder)) : '';
  window.open('/api/image?sku=' + encodeURIComponent(currentModalSku) + '&kind=final_seamless' + folderParam, '_blank');
};

$('modal-view-fabric').onclick = () => {
  if (!currentModalSku) return;
  const folderParam = currentModalFolder ? ('&folder=' + encodeURIComponent(currentModalFolder)) : '';
  window.open('/api/image?sku=' + encodeURIComponent(currentModalSku) + '&kind=fabric' + folderParam, '_blank');
};

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !$('sku-modal').classList.contains('hidden')) {
    closeSkuModal();
  }
});

$('source-drive').onchange = updateSourceUI;
$('source-local').onchange = updateSourceUI;
$('browse').onclick = async () => {
  try {
    const result = await api('/api/select-folder', { initial_dir: $('local-folder').value.trim() });
    if (result.path) {
      $('local-folder').value = result.path;
      $('source-local').checked = true;
      updateSourceUI();
    }
  } catch (e) { toastError(e); }
};

$('browse-tex-prompt').onclick = async () => {
  try {
    const result = await api('/api/select-prompt-file', { initial_path: $('tex-prompt-file').value.trim() });
    if (result.path) {
      $('tex-prompt-file').value = result.path;
      $('tex-prompt-mode-attach').checked = true;
      updatePromptUI();
    }
  } catch (e) { toastError(e); }
};

$('browse-fab-prompt').onclick = async () => {
  try {
    const result = await api('/api/select-prompt-file', { initial_path: $('fab-prompt-file').value.trim() });
    if (result.path) {
      $('fab-prompt-file').value = result.path;
      $('fab-prompt-mode-attach').checked = true;
      updatePromptUI();
    }
  } catch (e) { toastError(e); }
};

$('browse-flow-prompt').onclick = async () => {
  try {
    const result = await api('/api/select-prompt-file', { initial_path: $('flow-prompt-file').value.trim() });
    if (result.path) {
      $('flow-prompt-file').value = result.path;
      $('flow-prompt-mode-attach').checked = true;
      updatePromptUI();
    }
  } catch (e) { toastError(e); }
};

$('load-default-tex-prompt').onclick = async () => {
  try {
    const result = await api('/api/load-default-prompt?flow=texture');
    if (result.prompt_text) {
      $('tex-prompt-text').value = result.prompt_text;
      updatePromptCounts();
      alert('Đã tải nội dung từ file mẫu 01_TEXTURE_SEAMLESS_MASTER.md!');
    }
  } catch (e) { toastError(e); }
};

$('load-default-fab-prompt').onclick = async () => {
  try {
    const result = await api('/api/load-default-prompt?flow=fabric');
    if (result.prompt_text) {
      $('fab-prompt-text').value = result.prompt_text;
      updatePromptCounts();
      alert('Đã tải nội dung từ file mẫu 02_FABRIC_SWATCH_MASTER.md!');
    }
  } catch (e) { toastError(e); }
};

$('load-default-flow-prompt').onclick = async () => {
  try {
    const result = await api('/api/load-default-prompt?flow=flow_texture');
    if (result.prompt_text) {
      $('flow-prompt-text').value = result.prompt_text;
      updatePromptCounts();
      alert('Đã tải nội dung từ file mẫu scanned_to_texture_prompt.md!');
    }
  } catch (e) { toastError(e); }
};

$('save').onclick = () => api('/api/save', payload()).then(() => alert('Đã lưu cấu hình thành công!')).catch(toastError);
$('run').onclick = async () => {
  try {
    const pl = payload();
    pl.engine = 'chatgpt';
    if (!(await prepareSwatchPrerequisites(pl))) return;
    await api('/api/run', pl);
  } catch (error) {
    toastError(error);
  }
};
$('run-algo').onclick = () => {
  const pl = payload();
  pl.engine = 'algo';
  api('/api/run', pl).catch(toastError);
};
$('run-flow').onclick = () => {
  const pl = payload();
  pl.engine = 'flow';
  api('/api/run', pl).catch(toastError);
};
$('stop').onclick = () => {
  if (confirm('Dừng tiến trình hiện tại? Checkpoint vẫn được giữ.')) api('/api/stop', {}).catch(toastError);
};
$('chrome').onclick = () => api('/api/chrome', {}).catch(toastError);
$('check').onclick = () => api('/api/check-browser', {}).catch(toastError);
$('check-flow').onclick = () => api('/api/check-flow-browser', {}).catch(toastError);
async function fetchState() {
  try {
    const filterParam = '?folder=' + encodeURIComponent(currentSelectedFolder || 'all');
    const s = await api('/api/state' + filterParam);
    if (s.drive_urls && Array.isArray(s.drive_urls)) {
      driveUrlsQueue = s.drive_urls;
    } else if (s.drive_url) {
      driveUrlsQueue = [{ url: s.drive_url, folder: '' }];
    } else {
      driveUrlsQueue = [];
    }

    if (!loaded) {
      $('local-folder').value = s.local_source_dir || '';
      $('source-local').checked = s.source_mode === 'local';
      $('source-drive').checked = s.source_mode !== 'local';
      $('perchat').value = s.images_per_chat || 10;
      $('auto-retry').checked = s.auto_retry_enabled !== false;
      $('retry-delay').value = s.auto_retry_delay_seconds || 120;
      $('retry-max').value = s.auto_retry_max_attempts !== undefined ? s.auto_retry_max_attempts : 10;

      // Initialize prompt settings
      $('tex-prompt-mode-manual').checked = (s.texture_prompt_mode === 'manual');
      $('tex-prompt-mode-attach').checked = (s.texture_prompt_mode !== 'manual');
      $('tex-prompt-file').value = s.texture_prompt_file || 'prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md';
      $('tex-prompt-text').value = s.texture_prompt_text || '';

      $('fab-prompt-mode-manual').checked = (s.fabric_prompt_mode === 'manual');
      $('fab-prompt-mode-attach').checked = (s.fabric_prompt_mode !== 'manual');
      $('fab-prompt-file').value = s.fabric_prompt_file || 'prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md';
      $('fab-prompt-text').value = s.fabric_prompt_text || '';

      $('flow-prompt-mode-manual').checked = (s.flow_prompt_mode === 'manual');
      $('flow-prompt-mode-attach').checked = (s.flow_prompt_mode !== 'manual');
      $('flow-prompt-file').value = s.flow_prompt_file || 'prompts/scanned_to_texture_prompt.md';
      $('flow-prompt-text').value = s.flow_prompt_text || '';

      if (s.telegram) {
        $('tele-enabled').checked = Boolean(s.telegram.enabled);
        $('tele-token').value = s.telegram.bot_token || '';
        $('tele-chat-id').value = s.telegram.chat_id || '';
        $('tele-start').checked = (s.telegram.notify_on_start !== false);
        $('tele-periodic').checked = (s.telegram.notify_periodic_progress !== false);
        $('tele-failure').checked = (s.telegram.notify_on_failure !== false);
        $('tele-complete').checked = (s.telegram.notify_on_complete !== false);
        toggleTelegramInputs();
      }

      updateSourceUI();
      updatePromptUI();

      // Restore active tab from previous session
      try {
        const savedTab = sessionStorage.getItem('veo3_active_tab');
        if (savedTab && ['dashboard', 'progress', 'quality'].includes(savedTab)) {
          switchTab(savedTab);
        }
      } catch(e) {}

      loaded = true;
    }

    driveFoldersStats = (s.drive_folders_stats && s.drive_folders_stats.folders) || [];
    hiddenDriveFolders = Array.isArray(s.hidden_drive_folders) ? s.hidden_drive_folders : [];
    activePipelineFolders = Array.isArray(s.active_pipeline_folders) ? s.active_pipeline_folders : [];
    pipelineIsRunning = Boolean(s.running);
    renderDriveFolders(driveFoldersStats, driveUrlsQueue);
    renderDriveManagementSection(s.drive_folders_stats);
    updateFolderFilterSelect(driveFoldersStats);
    if (s.output_subdirectories) {
      updateAuditTargetChildOptions(s.output_subdirectories);
    }

    $('project').textContent = s.project_dir;
    $('python').textContent = s.python_exe || 'Không tìm thấy Python';
    $('status').textContent = s.status;
    $('status').className = 'badge' + (s.running ? ' running' : '');
    $('run').disabled = s.running;
    if ($('run-algo')) $('run-algo').disabled = s.running;
    if ($('run-flow')) $('run-flow').disabled = s.running;
    $('stop').disabled = !s.running;
    $('chrome').disabled = s.running;
    $('check').disabled = s.running;
    $('save').disabled = s.running;
    $('browse').disabled = s.running;
    $('progress').className = 'progress' + (s.running ? ' running' : '');

    renderFabricProgress(s.fabric_progress, s.timing_stats);

    if (s.quota_alert) {
      $('quota-banner').classList.remove('hidden');
      $('quota-banner-text').textContent = s.quota_alert.message || s.quota_alert;
    } else {
      $('quota-banner').classList.add('hidden');
    }

    if (s.log !== localLog) {
      localLog = s.log;
      $('log').textContent = localLog;
      $('log').scrollTop = $('log').scrollHeight;
    }
  } catch (e) {
    $('status').textContent = 'Mất kết nối';
    $('status').className = 'badge error';
  }
}

async function dismissQuotaBanner() {
  $('quota-banner').classList.add('hidden');
  try {
    await api('/api/dismiss-quota-alert', {});
  } catch(e) {}
}

let auditResultsData = [];
let auditSubdirsLoaded = false;

function updateAuditTargetChildOptions(subdirs) {
  if (auditSubdirsLoaded || !Array.isArray(subdirs)) return;
  const sel = $('audit-target-child');
  if (!sel) return;
  const cur = sel.value;
  sel.replaceChildren();

  const allOpt = document.createElement('option');
  allOpt.value = 'all';
  allOpt.textContent = 'Tất cả thư mục (' + subdirs.join(', ') + ')';
  sel.appendChild(allOpt);

  subdirs.forEach(d => {
    const opt = document.createElement('option');
    opt.value = d;
    opt.textContent = 'output/' + d;
    if (d === 'chatgpt') opt.selected = true;
    sel.appendChild(opt);
  });
  if (cur && Array.from(sel.options).some(o => o.value === cur)) {
    sel.value = cur;
  }
  auditSubdirsLoaded = true;
}

async function runDriveAudit() {
  const urlsText = ($('audit-urls').value || '').trim();
  if (!urlsText) {
    alert('Vui lòng nhập ít nhất một link Google Drive vào ô văn bản.');
    $('audit-urls').focus();
    return;
  }
  const urls = urlsText.split(/[\r\n,]+/).map(u => u.trim()).filter(u => u.length > 0);
  if (!urls.length) {
    alert('Không tìm thấy link Google Drive hợp lệ.');
    return;
  }
  const targetChild = $('audit-target-child').value;

  $('btn-run-audit').disabled = true;
  $('audit-loading').classList.remove('hidden');
  $('audit-results-container').replaceChildren();

  try {
    const res = await api('/api/audit-drive-folders', {
      urls: urls,
      target_child: targetChild
    });
    if (res && res.results) {
      auditResultsData = res.results;
      renderAuditResults(res.results);
    } else {
      alert('Không nhận được dữ liệu kiểm tra từ máy chủ.');
    }
  } catch (err) {
    alert('Lỗi kiểm tra Drive: ' + (err.message || err));
  } finally {
    $('btn-run-audit').disabled = false;
    $('audit-loading').classList.add('hidden');
  }
}

function renderAuditResults(results) {
  const container = $('audit-results-container');
  container.replaceChildren();

  if (!results || !results.length) {
    const empty = document.createElement('div');
    empty.className = 'hint';
    empty.style.textAlign = 'center';
    empty.style.padding = '20px';
    empty.textContent = 'Không có kết quả kiểm tra nào.';
    container.appendChild(empty);
    return;
  }

  results.forEach((item, rIdx) => {
    const card = document.createElement('div');
    card.className = 'audit-card';

    // Header
    const header = document.createElement('div');
    header.className = 'audit-header';

    const titleBox = document.createElement('div');
    titleBox.className = 'audit-title-box';

    const title = document.createElement('div');
    title.className = 'audit-title';
    title.innerHTML = `📁 <span>${escapeHtml(item.title || 'Thư mục Drive')}</span>`;

    const subtitle = document.createElement('div');
    subtitle.className = 'audit-subtitle';
    subtitle.innerHTML = `
      <span>Mã chính: <strong style="color:var(--cyan); font-family:monospace;">${escapeHtml(item.base_code || '--')}</strong></span>
      <span>|</span>
      <span>📦 Drive: <strong>${item.drive_total || 0} ảnh</strong></span>
      <span>|</span>
      <a href="${item.url}" target="_blank" style="color:var(--cyan); text-decoration:none;">🔗 Mở link Drive</a>
    `;

    titleBox.appendChild(title);
    titleBox.appendChild(subtitle);
    header.appendChild(titleBox);
    card.appendChild(header);

    // Error case
    if (item.error) {
      const errBox = document.createElement('div');
      errBox.style.padding = '10px 14px';
      errBox.style.borderRadius = '8px';
      errBox.style.border = '1px solid #9f1239';
      errBox.style.background = 'rgba(159,18,57,.15)';
      errBox.style.color = '#fda4af';
      errBox.style.fontSize = '13px';
      errBox.innerHTML = `⚠️ <b>Lỗi:</b> ${escapeHtml(item.error)}`;
      card.appendChild(errBox);
      container.appendChild(card);
      return;
    }

    // Checks per child directory
    (item.checks || []).forEach((chk, cIdx) => {
      const chkBox = document.createElement('div');
      chkBox.className = 'audit-check-box';

      const chkHeader = document.createElement('div');
      chkHeader.className = 'audit-check-header';

      const pathInfo = document.createElement('div');
      pathInfo.style.display = 'flex';
      pathInfo.style.alignItems = 'center';
      pathInfo.style.gap = '8px';

      const statusBadge = document.createElement('span');
      if (chk.folder_exists) {
        statusBadge.className = 'badge ok';
        statusBadge.textContent = '✔ Đã có thư mục';
      } else {
        statusBadge.className = 'badge pending';
        statusBadge.textContent = '⚠️ Chưa có thư mục';
      }

      pathInfo.appendChild(statusBadge);
      const pathText = document.createElement('span');
      pathText.innerHTML = `Thư mục đối chiếu: <code style="color:var(--cyan); font-weight:bold;">${escapeHtml(chk.folder_path)}</code>`;
      pathInfo.appendChild(pathText);

      const countInfo = document.createElement('div');
      countInfo.innerHTML = `Đã tạo: <b style="color:var(--green);">${chk.created_count}/${chk.total}</b> (${chk.percent}%) &nbsp;|&nbsp; Còn thiếu: <b style="color:var(--red);">${chk.missing_count}</b>`;

      chkHeader.appendChild(pathInfo);
      chkHeader.appendChild(countInfo);
      chkBox.appendChild(chkHeader);

      // Progress Bar
      const progWrap = document.createElement('div');
      progWrap.className = 'audit-progress-bar';
      const progSpan = document.createElement('span');
      progSpan.style.width = chk.percent + '%';
      progWrap.appendChild(progSpan);
      chkBox.appendChild(progWrap);

      // Actions row
      const actionsRow = document.createElement('div');
      actionsRow.className = 'audit-actions';

      const missingSkus = (chk.skus || []).filter(s => s.status !== 'done').map(s => s.sku);

      if (missingSkus.length > 0) {
        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'ghost btn-sm';
        copyBtn.innerHTML = `📋 Copy ${missingSkus.length} SKU chưa tạo`;
        copyBtn.onclick = () => copyMissingSkus(missingSkus.join(', '));
        actionsRow.appendChild(copyBtn);
      }

      const addQueueBtn = document.createElement('button');
      addQueueBtn.type = 'button';
      addQueueBtn.className = 'primary btn-sm';
      addQueueBtn.innerHTML = '➕ Thêm vào hàng đợi Pipeline';
      addQueueBtn.onclick = () => addAuditedFolderToPipeline(item.url, chk.matched_folder_name || item.base_code);
      actionsRow.appendChild(addQueueBtn);

      // Filter buttons for SKU chips
      const filterWrap = document.createElement('div');
      filterWrap.style.marginLeft = 'auto';
      filterWrap.style.display = 'flex';
      filterWrap.style.gap = '4px';

      const chipsId = `audit-chips-${rIdx}-${cIdx}`;

      const fAll = document.createElement('button');
      fAll.className = 'chip-filter-btn active';
      fAll.textContent = `Tất cả (${chk.total})`;
      fAll.onclick = () => filterAuditChips(chipsId, 'all', fAll);

      const fDone = document.createElement('button');
      fDone.className = 'chip-filter-btn';
      fDone.textContent = `Đã tạo (${chk.created_count})`;
      fDone.onclick = () => filterAuditChips(chipsId, 'done', fDone);

      const fMissing = document.createElement('button');
      fMissing.className = 'chip-filter-btn';
      fMissing.textContent = `Chưa tạo (${chk.missing_count})`;
      fMissing.onclick = () => filterAuditChips(chipsId, 'missing', fMissing);

      filterWrap.appendChild(fAll);
      filterWrap.appendChild(fDone);
      filterWrap.appendChild(fMissing);
      actionsRow.appendChild(filterWrap);

      chkBox.appendChild(actionsRow);

      // Chips Container
      const chipsBox = document.createElement('div');
      chipsBox.id = chipsId;
      chipsBox.className = 'audit-chips-container';

      (chk.skus || []).forEach(s => {
        const chip = document.createElement('span');
        chip.className = `audit-chip ${s.status}`;
        chip.dataset.status = s.status;
        const icon = s.status === 'done' ? '✔' : '⏳';
        const tip = s.status === 'done'
          ? `Seamless: ${s.has_seamless ? 'Có' : 'Không'} | Swatch: ${s.has_fabric ? 'Có' : 'Không'}`
          : 'Chưa tạo thành phẩm';
        chip.title = tip;
        chip.textContent = `${s.sku} ${icon}`;
        chipsBox.appendChild(chip);
      });

      chkBox.appendChild(chipsBox);
      card.appendChild(chkBox);
    });

    container.appendChild(card);
  });
}

function filterAuditChips(containerId, filter, btn) {
  const container = $(containerId);
  if (!container) return;
  const parent = btn.parentElement;
  if (parent) {
    Array.from(parent.children).forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }
  const chips = container.querySelectorAll('.audit-chip');
  chips.forEach(c => {
    if (filter === 'all') {
      c.style.display = 'inline-flex';
    } else {
      c.style.display = (c.dataset.status === filter) ? 'inline-flex' : 'none';
    }
  });
}

function copyMissingSkus(skuListStr) {
  if (!skuListStr) {
    alert('Không có SKU nào chưa tạo.');
    return;
  }
  navigator.clipboard.writeText(skuListStr).then(() => {
    alert(`Đã sao chép ${skuListStr.split(',').length} SKU vào clipboard!`);
  }).catch(() => {
    prompt('Sao chép danh sách SKU:', skuListStr);
  });
}

async function addAuditedFolderToPipeline(url, folder) {
  folder = (folder || '').trim();
  url = (url || '').trim();
  if (!url) return;
  const folderId = driveFolderId(url);
  const exists = driveUrlsQueue.some(item => driveFolderId(item.url) === folderId);
  if (!exists) {
    driveUrlsQueue.push({ url: url, folder: folder, modified_at: new Date().toISOString() });
    driveFolderSearch = '';
    driveFolderPage = 1;
    $('drive-folder-search').value = '';
    renderDriveFolders(driveFoldersStats, driveUrlsQueue);
    try {
      await api('/api/save', payload());
      await fetchState();
    } catch(e) {
      toastError(e);
    }
  }
  switchTab('dashboard');
  const target = $('drive-folders-list');
  if (target) {
    target.scrollIntoView({ behavior: 'smooth' });
  }
}

let currentAuditFolderData = null;
let currentDriveTableFolders = [];
let editDriveOriginalFolder = '';

function renderDriveManagementSection(statsData) {
  if (!statsData) return;
  const folders = statsData.folders || [];
  currentDriveTableFolders = folders;

  // 1. Update badges & metrics
  if ($('dt-total-folders')) $('dt-total-folders').textContent = statsData.total_folders || 0;
  if ($('dt-linked-folders')) $('dt-linked-folders').textContent = statsData.linked_folders || 0;
  if ($('dt-unlinked-folders')) $('dt-unlinked-folders').textContent = statsData.unlinked_folders || 0;
  if ($('dt-total-skus')) $('dt-total-skus').textContent = statsData.total_skus || 0;
  if ($('dt-total-created')) $('dt-total-created').textContent = statsData.total_created || 0;
  if ($('dt-total-pending')) $('dt-total-pending').textContent = statsData.total_pending || 0;
  if ($('dt-overall-percent')) $('dt-overall-percent').textContent = (statsData.overall_percent || 0) + '%';
  if ($('dt-progress-bar-span')) {
    $('dt-progress-bar-span').style.width = (statsData.overall_percent || 0) + '%';
  }
  // 3. Render Table rows
  filterDriveTable();
}

function filterDriveTable() {
  const tbody = $('drive-table-body');
  if (!tbody) return;
  tbody.replaceChildren();

  const query = ($('dt-search') ? $('dt-search').value : '').trim().toLowerCase();
  const filter = ($('dt-status-filter') ? $('dt-status-filter').value : 'all');

  const filtered = currentDriveTableFolders.filter(f => {
    if (query && !f.folder.toLowerCase().includes(query) && !(f.url || '').toLowerCase().includes(query)) {
      return false;
    }
    if (filter === 'completed') return f.status === 'completed';
    if (filter === 'in_progress') return f.status === 'in_progress';
    if (filter === 'pending') return f.pending_count > 0;
    if (filter === 'linked') return !!f.has_drive_url;
    if (filter === 'unlinked') return !f.has_drive_url;
    return true;
  });

  if (!filtered.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="9" style="text-align:center; padding:32px;" class="hint">Không tìm thấy thư mục vải nào phù hợp với bộ lọc.</td>`;
    tbody.appendChild(tr);
    return;
  }

  filtered.forEach(item => {
    const tr = document.createElement('tr');

    // 1. Folder Name + Status Badge
    const tdFolder = document.createElement('td');
    let badgeHtml = '';
    if (item.status === 'completed') {
      badgeHtml = `<span class="badge ok dt-status-badge">✔ Xong 100%</span>`;
    } else if (item.status === 'in_progress') {
      badgeHtml = `<span class="badge dt-status-badge" style="border-color:var(--cyan); color:var(--cyan);">⚡ Đang làm (${item.percent}%)</span>`;
    } else if (!item.has_drive_url) {
      badgeHtml = `<span class="badge-unlinked dt-status-badge">⚠️ Chưa có Drive</span>`;
    } else {
      badgeHtml = `<span class="badge pending dt-status-badge">⏳ Chưa tạo</span>`;
    }
    tdFolder.innerHTML = `
      <div class="dt-folder-stack">
        <div class="dt-folder-head">
          <b class="dt-folder-name" title="Bấm để lọc SKU thư mục này" onclick="selectFolderFilter('${escapeHtml(item.folder)}')">📁 ${escapeHtml(item.folder)}</b>
        </div>
        <span class="hint" style="font-size:11px;">${item.total || 0} SKU trên máy</span>
      </div>
    `;
    tr.appendChild(tdFolder);

    // 2. Folder Status
    const tdStatus = document.createElement('td');
    tdStatus.innerHTML = badgeHtml;
    tr.appendChild(tdStatus);

    // 3. Google Drive Link
    const tdUrl = document.createElement('td');
    if (item.url) {
      tdUrl.innerHTML = `
        <div class="drive-link-box">
          <a href="${escapeHtml(item.url)}" target="_blank" title="${escapeHtml(item.url)}">🔗 ${escapeHtml(item.url)}</a>
          <button type="button" class="ghost btn-icon" title="Sửa link Drive" onclick="openEditDriveModal('${escapeHtml(item.folder)}', '${escapeHtml(item.url)}')">✏️</button>
        </div>
      `;
    } else {
      tdUrl.innerHTML = `
        <button type="button" class="ghost btn-sm" style="border-color:var(--cyan); color:var(--cyan); font-size:11px;" onclick="openEditDriveModal('${escapeHtml(item.folder)}', '')">+ Gán link Drive</button>
      `;
    }
    tr.appendChild(tdUrl);

    // 4. Drive Images Count
    const tdDrive = document.createElement('td');
    if (item.drive_total !== undefined && item.drive_total !== null) {
      const timeStr = item.last_sync_at ? `<div class="metric-sub">🕒 ${escapeHtml(item.last_sync_at.split(' ')[1] || item.last_sync_at)}</div>` : '';
      tdDrive.innerHTML = `<b>📥 ${item.drive_total}</b> ảnh${timeStr}`;
    } else if (item.url) {
      tdDrive.innerHTML = `
        <span class="hint" style="font-size:12px;">Chưa quét</span>
        <button type="button" class="ghost btn-icon" title="Quét Drive ngay" onclick="openFolderSkuAudit('${escapeHtml(item.folder)}', '${escapeHtml(item.url)}')">🔍</button>
      `;
    } else {
      tdDrive.innerHTML = `<span class="hint">-</span>`;
    }
    tr.appendChild(tdDrive);

    // 5. Raw & Cropped
    const tdRaw = document.createElement('td');
    tdRaw.innerHTML = `
      <div>✂ Crop: <b>${item.cropped_count || 0}</b></div>
      <div class="metric-sub">Raw: <b>${item.raw_count || 0}</b></div>
    `;
    tr.appendChild(tdRaw);

    // 6. Created Artifacts
    const tdCreated = document.createElement('td');
    tdCreated.innerHTML = `
      <div>🎨 Seamless: <b class="ok">${item.seamless_count || 0}</b></div>
      <div class="metric-sub">👗 Swatch: <b class="ok">${item.fabric_count || 0}</b></div>
    `;
    tr.appendChild(tdCreated);

    // 7. Progress
    const tdProg = document.createElement('td');
    tdProg.innerHTML = `
      <div style="display:flex; align-items:center;">
        <div class="dt-progress-bar"><span style="width:${item.percent || 0}%;"></span></div>
        <b>${item.percent || 0}%</b>
      </div>
      <div class="metric-sub">${item.created_count || 0} / ${item.total || 0} SKU</div>
    `;
    tr.appendChild(tdProg);

    // 8. Missing
    const tdMissing = document.createElement('td');
    if (item.pending_count > 0) {
      tdMissing.innerHTML = `<b class="amber" style="font-size:13px;">Thiếu ${item.pending_count} SKU</b>`;
    } else if (item.total > 0) {
      tdMissing.innerHTML = `<span class="ok" style="font-weight:600;">✔ Đủ</span>`;
    } else {
      tdMissing.innerHTML = `<span class="hint">0 SKU</span>`;
    }
    tr.appendChild(tdMissing);

    // 9. Actions
    const tdActions = document.createElement('td');
    tdActions.style.textAlign = 'right';
    tdActions.style.whiteSpace = 'nowrap';
    tdActions.innerHTML = `
      <div class="dt-actions">
        <button type="button" class="ghost btn-sm" title="Đối chiếu chi tiết từng SKU" onclick="openFolderSkuAudit('${escapeHtml(item.folder)}', '${escapeHtml(item.url || '')}')">🔍 SKU</button>
        <button type="button" class="ghost btn-sm" style="color:var(--green); border-color:var(--green);" title="Chạy Thuật toán Seamless cho thư mục này" onclick="runSingleFolder('${escapeHtml(item.folder)}', '${escapeHtml(item.url || '')}', 'algo')">⚡ Thuật toán</button>
        <button type="button" class="primary btn-sm" title="Chạy ChatGPT cho thư mục này" onclick="runSingleFolder('${escapeHtml(item.folder)}', '${escapeHtml(item.url || '')}', 'chatgpt')">▶ Chạy</button>
        <button type="button" class="ghost btn-sm" title="Mở thư mục output trên máy" onclick="openFolderDirectory('${escapeHtml(item.folder)}')">📁</button>
        ${item.url ? `<button type="button" class="danger btn-sm" title="Gỡ link Drive" onclick="unlinkDriveFolder('${escapeHtml(item.folder)}')">🗑️</button>` : `<button type="button" class="danger btn-sm dt-action-placeholder" tabindex="-1" aria-hidden="true">🗑️</button>`}
      </div>
    `;
    tr.appendChild(tdActions);

    tbody.appendChild(tr);
  });
}

function openAddDriveModal() {
  openEditDriveModal('', '');
}

function openEditDriveModal(folder, url) {
  editDriveOriginalFolder = String(folder || '').trim();
  $('edit-drive-folder').value = folder || '';
  $('edit-drive-url').value = url || '';
  $('edit-drive-sku').value = $('sku').value || '';
  $('edit-drive-limit').value = $('limit').value || '';
  $('edit-drive-perchat').value = $('perchat').value || '10';
  $('edit-drive-message').className = 'edit-drive-message hidden';
  $('edit-drive-message').textContent = '';
  validateEditDriveDuplicates();
  $('modal-edit-drive-title').textContent = folder ? `🔗 Sửa Link Drive Cho Thư Mục "${folder}"` : '➕ Thêm / Gán Link Google Drive Mới';
  $('modal-edit-drive').classList.remove('hidden');
}

function closeEditDriveModal() {
  $('modal-edit-drive').classList.add('hidden');
}

// A text-selection drag can begin inside the dialog and end on its backdrop.
// Only close when the pointer press itself also began on the backdrop.
{
  const modal = $('modal-edit-drive');
  let pointerStartedOnBackdrop = false;
  modal.addEventListener('pointerdown', (event) => {
    pointerStartedOnBackdrop = event.target === modal;
  });
  modal.addEventListener('click', (event) => {
    if (event.target === modal && pointerStartedOnBackdrop) closeEditDriveModal();
    pointerStartedOnBackdrop = false;
  });
  modal.addEventListener('pointercancel', () => {
    pointerStartedOnBackdrop = false;
  });
}

function setEditDriveFieldError(inputId, errorId, message) {
  const input = $(inputId);
  const error = $(errorId);
  input.classList.toggle('input-invalid', Boolean(message));
  input.setAttribute('aria-invalid', message ? 'true' : 'false');
  error.textContent = message || '';
  error.classList.toggle('hidden', !message);
}

function validateEditDriveDuplicates() {
  const folder = $('edit-drive-folder').value.trim();
  const url = $('edit-drive-url').value.trim();
  const folderKey = folder.toLocaleLowerCase('vi');
  const originalKey = editDriveOriginalFolder.toLocaleLowerCase('vi');
  const allFolders = [
    ...currentDriveTableFolders,
    ...driveUrlsQueue.map(item => ({ folder: item.folder || '', url: item.url || '' }))
  ];

  const folderConflict = folderKey && allFolders.find(item => {
    const itemKey = String(item.folder || '').trim().toLocaleLowerCase('vi');
    return itemKey === folderKey && itemKey !== originalKey;
  });
  const urlId = driveFolderId(url);
  const urlConflict = urlId && allFolders.find(item => {
    const itemFolderKey = String(item.folder || '').trim().toLocaleLowerCase('vi');
    return driveFolderId(item.url || '') === urlId && itemFolderKey !== originalKey;
  });

  setEditDriveFieldError(
    'edit-drive-folder',
    'edit-drive-folder-error',
    folderConflict ? `Tên thư mục đã tồn tại: ${folderConflict.folder}.` : ''
  );
  setEditDriveFieldError(
    'edit-drive-url',
    'edit-drive-url-error',
    urlConflict ? `Link Drive này đã được gán cho thư mục ${urlConflict.folder || 'khác'}.` : ''
  );
  return !folderConflict && !urlConflict;
}

async function saveDriveLinkModal() {
  const folder = $('edit-drive-folder').value.trim();
  const url = $('edit-drive-url').value.trim();
  const sku = $('edit-drive-sku').value.trim();
  const limit = $('edit-drive-limit').value.trim();
  const imagesPerChat = Number($('edit-drive-perchat').value || 10);
  const saveButton = $('save-drive-link-btn');
  const message = $('edit-drive-message');
  const showError = (text, input) => {
    message.textContent = text;
    message.className = 'edit-drive-message';
    if (input) input.focus();
  };
  if (!validateEditDriveDuplicates()) {
    showError('Vui lòng xử lý các trường đang bị trùng trước khi lưu.');
    return;
  }
  if (!folder) {
    showError('Vui lòng nhập tên thư mục vải mới.', $('edit-drive-folder'));
    return;
  }
  if (!url || !url.includes('drive.google.com') || !url.includes('/folders/')) {
    showError('Link đang trống hoặc không đúng định dạng thư mục Google Drive.', $('edit-drive-url'));
    return;
  }
  if (!Number.isInteger(imagesPerChat) || imagesPerChat < 1 || imagesPerChat > 20) {
    showError('Ảnh mỗi chat phải là số nguyên từ 1 đến 20.', $('edit-drive-perchat'));
    return;
  }
  message.className = 'edit-drive-message hidden';
  message.textContent = '';
  saveButton.disabled = true;
  saveButton.textContent = '⏳ Đang lưu...';
  try {
    await api('/api/save-drive-link', { folder, url, original_folder: editDriveOriginalFolder });
    $('sku').value = sku;
    $('limit').value = limit;
    $('perchat').value = String(imagesPerChat);
    await fetchState();
    saveButton.textContent = '✓ Đã lưu';
    closeEditDriveModal();
  } catch (err) {
    showError('Không thể lưu link Drive: ' + (err.message || err));
  } finally {
    saveButton.disabled = false;
    saveButton.textContent = '💾 Lưu liên kết';
  }
}

async function unlinkDriveFolder(folder) {
  if (!confirm(`Bạn có chắc muốn gỡ link Google Drive của thư mục "${folder}"?\n(Các file kết quả trên máy vẫn được giữ nguyên).`)) return;
  try {
    await api('/api/save-drive-link', { folder, url: '' });
    await fetchState();
  } catch (err) {
    alert('Lỗi gỡ link: ' + (err.message || err));
  }
}

function openBulkDriveModal() {
  $('bulk-drive-urls').value = '';
  $('bulk-match-results').replaceChildren();
  $('bulk-match-results').classList.add('hidden');
  $('modal-bulk-drive').classList.remove('hidden');
}

function closeBulkDriveModal() {
  $('modal-bulk-drive').classList.add('hidden');
}

async function submitBulkDriveMatch() {
  const urlsText = ($('bulk-drive-urls').value || '').trim();
  if (!urlsText) {
    alert('Vui lòng dán ít nhất 1 link Google Drive.');
    return;
  }
  const urls = urlsText.split(/[\r\n,]+/).map(u => u.trim()).filter(u => u.length > 0);
  if (!urls.length) {
    alert('Không tìm thấy link hợp lệ.');
    return;
  }

  $('btn-submit-bulk-match').disabled = true;
  $('bulk-match-loading').classList.remove('hidden');
  $('bulk-match-results').classList.add('hidden');
  $('bulk-match-results').replaceChildren();

  try {
    const res = await api('/api/auto-match-drive-urls', { urls });
    $('bulk-match-loading').classList.add('hidden');
    $('bulk-match-results').classList.remove('hidden');

    if (res.matched && res.matched.length) {
      const matchBox = document.createElement('div');
      matchBox.innerHTML = `<b class="ok">✔ Đã ghép nối thành công ${res.matched.length} thư mục:</b>`;
      res.matched.forEach(m => {
        const item = document.createElement('div');
        item.style.padding = '4px 8px';
        item.style.background = 'rgba(52,211,153,.1)';
        item.style.borderRadius = '6px';
        item.innerHTML = `📁 <b>${escapeHtml(m.folder)}</b> ← <span style="font-family:monospace; color:var(--cyan);">${escapeHtml(m.url)}</span>`;
        matchBox.appendChild(item);
      });
      $('bulk-match-results').appendChild(matchBox);
    }

    if (res.unmatched && res.unmatched.length) {
      const unmatchBox = document.createElement('div');
      unmatchBox.innerHTML = `<b class="amber" style="margin-top:6px; display:block;">⚠️ ${res.unmatched.length} link chưa tìm thấy thư mục khớp:</b>`;
      res.unmatched.forEach(u => {
        const item = document.createElement('div');
        item.style.padding = '4px 8px';
        item.style.background = 'rgba(251,191,36,.1)';
        item.style.borderRadius = '6px';
        item.innerHTML = `<span style="font-family:monospace;">${escapeHtml(u.url)}</span>: ${escapeHtml(u.reason || 'Chưa ghép')}`;
        unmatchBox.appendChild(item);
      });
      $('bulk-match-results').appendChild(unmatchBox);
    }

    await fetchState();
  } catch (err) {
    alert('Lỗi ghép nối link Drive: ' + (err.message || err));
  } finally {
    $('btn-submit-bulk-match').disabled = false;
    $('bulk-match-loading').classList.add('hidden');
  }
}

async function openFolderSkuAudit(folder, url) {
  if (!url) {
    openEditDriveModal(folder, '');
    return;
  }
  currentAuditFolderData = { folder, url, skus: [] };
  $('mfa-title').textContent = `Đối Chiếu SKU: ${folder}`;
  $('mfa-subtitle').textContent = `Link Drive: ${url}`;
  $('mfa-counts').textContent = 'Đang quét...';
  $('mfa-chips-container').replaceChildren();
  $('modal-folder-audit').classList.remove('hidden');
  $('mfa-loading').classList.remove('hidden');

  try {
    const res = await api('/api/audit-single-folder', { folder, url });
    currentAuditFolderData = res;
    currentAuditFolderData.folder = folder;
    currentAuditFolderData.url = url;
    renderFolderAuditModalContent(res);
  } catch (err) {
    $('mfa-loading').classList.add('hidden');
    $('mfa-chips-container').innerHTML = `<div style="color:var(--red); padding:16px;">⚠️ Lỗi quét Drive: ${escapeHtml(err.message || err)}</div>`;
  }
}

function renderFolderAuditModalContent(data) {
  $('mfa-loading').classList.add('hidden');
  const check = (data.checks && data.checks[0]) || {};
  const total = check.total || data.drive_total || 0;
  const created = check.created_count || 0;
  const missing = check.missing_count || 0;
  const percent = check.percent || 0;
  const skus = check.skus || [];

  currentAuditFolderData.skus = skus;
  currentAuditFolderData.missingSkus = skus.filter(s => s.status !== 'done').map(s => s.sku);

  $('mfa-counts').innerHTML = `Đã tạo: <b class="ok">${created} / ${total}</b> (${percent}%) • Còn thiếu: <b class="amber">${missing}</b>`;
  $('mfa-progress-bar').style.width = percent + '%';

  $('mfa-filter-all').textContent = `Tất cả (${total})`;
  $('mfa-filter-done').textContent = `✔ Đã tạo (${created})`;
  $('mfa-filter-missing').textContent = `⚠️ Chưa tạo (${missing})`;

  renderFolderAuditChips('all');
}

function filterFolderAuditChips(filter, btn) {
  if (btn) {
    const parent = btn.parentElement;
    Array.from(parent.children).forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }
  renderFolderAuditChips(filter);
}

function renderFolderAuditChips(filter) {
  const container = $('mfa-chips-container');
  container.replaceChildren();
  const skus = (currentAuditFolderData && currentAuditFolderData.skus) || [];

  const filtered = skus.filter(s => {
    if (filter === 'done') return s.status === 'done';
    if (filter === 'missing') return s.status !== 'done';
    return true;
  });

  if (!filtered.length) {
    const empty = document.createElement('div');
    empty.className = 'hint';
    empty.style.padding = '16px';
    empty.textContent = 'Không có SKU nào phù hợp với bộ lọc này.';
    container.appendChild(empty);
    return;
  }

  filtered.forEach(s => {
    const chip = document.createElement('span');
    chip.className = `audit-chip ${s.status}`;
    const icon = s.status === 'done' ? '✔' : '⏳';
    chip.textContent = `${s.sku} ${icon}`;
    chip.title = s.status === 'done' ? `Seamless: ${s.has_seamless ? 'Có' : 'Không'} | Swatch: ${s.has_fabric ? 'Có' : 'Không'}` : 'Chưa tạo ảnh thành phẩm';
    container.appendChild(chip);
  });
}

function closeFolderSkuAuditModal() {
  $('modal-folder-audit').classList.add('hidden');
}

function copyCurrentFolderMissingSkus() {
  const missing = (currentAuditFolderData && currentAuditFolderData.missingSkus) || [];
  if (!missing.length) {
    alert('Thư mục này đã hoàn thành 100%! Không có SKU nào còn thiếu.');
    return;
  }
  const text = missing.join(', ');
  navigator.clipboard.writeText(text).then(() => {
    alert(`Đã sao chép ${missing.length} SKU còn thiếu vào Clipboard!`);
  }).catch(() => {
    prompt('Sao chép danh sách SKU thiếu:', text);
  });
}

function sendMissingSkusToPipeline() {
  const missing = (currentAuditFolderData && currentAuditFolderData.missingSkus) || [];
  if (!missing.length) {
    alert('Thư mục này đã tạo đủ tất cả SKU!');
    return;
  }
  const folder = (currentAuditFolderData && currentAuditFolderData.folder) || '';
  $('sku').value = missing.join(', ');
  closeFolderSkuAuditModal();
  switchTab('dashboard');
  if (folder) {
    const select = $('folder-filter');
    if (select) select.value = folder;
  }
  $('sku').scrollIntoView({ behavior: 'smooth' });
}

async function reScanCurrentFolderAudit() {
  if (!currentAuditFolderData || !currentAuditFolderData.folder) return;
  openFolderSkuAudit(currentAuditFolderData.folder, currentAuditFolderData.url);
}

async function auditAllDriveLinks() {
  const foldersWithUrls = currentDriveTableFolders.filter(f => f.url);
  if (!foldersWithUrls.length) {
    alert('Chưa có thư mục nào được gán link Google Drive. Hãy thêm link Drive trước.');
    return;
  }
  const btn = $('btn-audit-all');
  btn.disabled = true;
  btn.textContent = '⏳ Đang quét tất cả...';
  $('audit-loading').classList.remove('hidden');

  try {
    for (const item of foldersWithUrls) {
      try {
        await api('/api/audit-single-folder', { folder: item.folder, url: item.url });
      } catch (err) {
        console.error('Audit failed for folder', item.folder, err);
      }
    }
    await fetchState();
    alert(`Đã hoàn tất quét và đối chiếu ${foldersWithUrls.length} thư mục Google Drive!`);
  } catch (err) {
    alert('Lỗi quét đối chiếu: ' + (err.message || err));
  } finally {
    btn.disabled = false;
    btn.textContent = '🔄 Quét & Đối chiếu tất cả link';
    $('audit-loading').classList.add('hidden');
  }
}

async function poll() {
  await fetchState();
  setTimeout(poll, 1000);
}
poll();
