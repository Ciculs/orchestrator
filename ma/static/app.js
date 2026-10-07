// AI Company Orchestrator - Client Application

let currentConfig = null;
let availableModels = [];
let installedSkills = [];
let activeProjectId = null;
let currentEventSource = null;
let currentEditingRole = null;

// Tab Switching
function switchTab(tabId) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.remove('bg-cyan-500/20', 'text-cyan-300', 'border-cyan-500/30');
    btn.classList.add('text-slate-400');
  });

  const activeContent = document.getElementById(`tab-${tabId}`);
  if (activeContent) activeContent.classList.remove('hidden');

  const activeBtn = document.getElementById(`tab-btn-${tabId}`);
  if (activeBtn) {
    activeBtn.classList.add('bg-cyan-500/20', 'text-cyan-300', 'border-cyan-500/30');
    activeBtn.classList.remove('text-slate-400');
  }

  if (tabId === 'usage') loadUsage();
  if (tabId === 'coding' && !activeProjectId) loadProjectsList();
}

function switchMissionSubTab(subTabId) {
  const tabs = ['live', 'terminal', 'diff', 'tasks', 'artifacts'];
  tabs.forEach(t => {
    const content = document.getElementById(`subtab-${t}`);
    const btn = document.getElementById(`subtab-btn-${t}`);
    if (content) content.classList.add('hidden');
    if (btn) {
      btn.classList.remove('border-emerald-400', 'text-emerald-300', 'font-semibold', 'border-cyan-400', 'text-cyan-300');
      btn.classList.add('border-transparent', 'text-slate-400');
    }
  });

  const targetContent = document.getElementById(`subtab-${subTabId}`);
  const targetBtn = document.getElementById(`subtab-btn-${subTabId}`);
  if (targetContent) targetContent.classList.remove('hidden');
  if (targetBtn) {
    targetBtn.classList.remove('border-transparent', 'text-slate-400');
    if (subTabId === 'live') {
      targetBtn.classList.add('border-emerald-400', 'text-emerald-300', 'font-semibold');
    } else {
      targetBtn.classList.add('border-cyan-400', 'text-cyan-300', 'font-semibold');
    }
  }
}

// Initial Data Load
async function init() {
  await checkStatus();
  await loadModels();
  await loadSkills();
  await loadRoles();
  await loadProjectsList();
  lucide.createIcons();
}

async function loadSkills() {
  try {
    const res = await fetch('/api/skills');
    const data = await res.json();
    if (data.ok && data.skills) {
      installedSkills = data.skills;
      const countEl = document.getElementById('skill-modal-count');
      if (countEl) countEl.textContent = `${installedSkills.length} skills đã cài đặt`;
      renderSkillManagerList();
    }
  } catch (err) {
    console.error('Load skills error:', err);
  }
}

async function checkStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    const badge = document.getElementById('router-status-badge');
    const text = document.getElementById('router-status-text');
    if (data.router_up) {
      badge.className = 'flex items-center space-x-2 px-3 py-1 rounded-lg bg-emerald-950/40 border border-emerald-800/60 text-emerald-400 text-xs font-mono';
      text.textContent = '9Router Online (127.0.0.1:20128)';
    } else {
      badge.className = 'flex items-center space-x-2 px-3 py-1 rounded-lg bg-rose-950/40 border border-rose-800/60 text-rose-400 text-xs font-mono';
      text.textContent = '9Router Offline';
    }
  } catch (err) {
    console.error('Check status error:', err);
  }
}

async function loadModels() {
  try {
    const res = await fetch('/api/models');
    const data = await res.json();
    availableModels = data.models || [];
    const text = document.getElementById('router-status-text');
    if (text && availableModels.length > 0) {
      text.textContent = `9Router Online (${availableModels.length} Models)`;
    }
  } catch (err) {
    console.error('Load models error:', err);
  }
}

async function loadRoles() {
  try {
    const res = await fetch('/api/roles');
    currentConfig = await res.json();
    renderOrgChart();
  } catch (err) {
    console.error('Load roles error:', err);
  }
}

// Render Org Chart Cards
function renderOrgChart() {
  if (!currentConfig || !currentConfig.roles) return;

  const engContainer = document.getElementById('engineering-roles-grid');
  const mediaContainer = document.getElementById('media-roles-grid');

  if (engContainer) engContainer.innerHTML = '';
  if (mediaContainer) mediaContainer.innerHTML = '';

  const roles = currentConfig.roles;

  // Render Engineering Roles
  const engKeys = ['design', 'critique', 'judgment', 'implementation', 'audit', 'report'];
  engKeys.forEach(k => {
    const role = roles[k];
    if (!role) return;
    const card = createRoleCardElement(k, role);
    if (engContainer) engContainer.appendChild(card);
  });

  // Add Human Founder / CEO Card
  if (engContainer) {
    const ceoCard = document.createElement('div');
    ceoCard.className = 'glass-panel rounded-xl p-4 border border-amber-500/40 bg-amber-950/20 relative';
    ceoCard.innerHTML = `
      <div class="flex items-start justify-between">
        <div class="flex items-center space-x-3">
          <div class="w-10 h-10 rounded-xl bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-amber-400">
            <i data-lucide="crown" class="w-5 h-5"></i>
          </div>
          <div>
            <h4 class="text-sm font-bold text-white">CEO / Human Founder</h4>
            <span class="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-300">Human Gate</span>
          </div>
        </div>
      </div>
      <p class="text-xs text-slate-400 mt-2 line-clamp-2">Quyền kiểm duyệt tối cao. Cấp phép merge hoặc yêu cầu sửa đổi trước khi đẩy lên main.</p>
      <div class="mt-3 pt-3 border-t border-slate-800/80 flex items-center justify-between text-[11px] font-mono text-slate-400">
        <span>Owner: Bạn (Human)</span>
        <span class="text-amber-400 font-bold">Supreme Gate</span>
      </div>
    `;
    engContainer.appendChild(ceoCard);
  }

  // Render Media Roles
  const mediaKeys = ['scriptwriter', 'prompter', 'voice', 'video_editor'];
  mediaKeys.forEach(k => {
    const role = roles[k];
    if (!role) return;
    const card = createRoleCardElement(k, role);
    if (mediaContainer) mediaContainer.appendChild(card);
  });

  lucide.createIcons();
}

function createRoleCardElement(key, role) {
  const card = document.createElement('div');
  card.id = `role-card-${key}`;
  card.className = `role-card glass-panel rounded-xl p-4 border border-slate-800 hover:border-cyan-500/50 cursor-pointer group transition-all`;
  card.onclick = () => openRoleModal(key);

  const iconName = role.icon || 'bot';
  const assignedSkills = role.skills || [];
  const skillsHtml = assignedSkills.length > 0
    ? `<div class="mt-2.5 flex flex-wrap gap-1 max-h-12 overflow-hidden">
        ${assignedSkills.slice(0, 3).map(s => `<span class="px-1.5 py-0.5 rounded bg-emerald-950/60 border border-emerald-800/60 text-emerald-300 text-[10px] font-mono truncate max-w-[130px]" title="${s}">✨ ${s}</span>`).join('')}
        ${assignedSkills.length > 3 ? `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-[9px] font-mono">+${assignedSkills.length - 3} skills</span>` : ''}
      </div>`
    : '';

  card.innerHTML = `
    <div class="flex items-start justify-between">
      <div class="flex items-center space-x-3">
        <div class="avatar-ring w-10 h-10 rounded-xl bg-slate-900 border border-slate-700 flex items-center justify-center text-cyan-400 group-hover:text-cyan-300">
          <i data-lucide="${iconName}" class="w-5 h-5"></i>
        </div>
        <div>
          <h4 class="text-sm font-bold text-white group-hover:text-cyan-300 transition">${role.name}</h4>
          <span class="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-slate-900 text-slate-400">${key}</span>
        </div>
      </div>
      <div class="status-indicator flex items-center space-x-1 text-[10px] font-mono text-slate-400" id="role-status-${key}">
        <span class="w-2 h-2 rounded-full bg-slate-600"></span>
        <span>Idle</span>
      </div>
    </div>
    
    <p class="text-xs text-slate-400 mt-2 line-clamp-2 h-8">${role.description || ''}</p>
    ${skillsHtml}

    <div class="mt-3 pt-3 border-t border-slate-800/80 flex items-center justify-between text-[11px] font-mono">
      <div class="truncate max-w-[170px] text-cyan-400/90 font-medium" title="${role.primary}">
        <i data-lucide="cpu" class="w-3 h-3 inline mr-1"></i>${role.primary}
      </div>
      <span class="text-slate-500 text-[10px]">${(role.fallbacks || []).length} fallbacks</span>
    </div>
  `;
  return card;
}

// Role Staffing & Customization Modal
function openRoleModal(roleKey) {
  if (!currentConfig || !currentConfig.roles || !currentConfig.roles[roleKey]) return;
  currentEditingRole = roleKey;
  const role = currentConfig.roles[roleKey];

  document.getElementById('modal-role-name').textContent = `Tùy Chỉnh: ${role.name}`;
  document.getElementById('modal-role-id').textContent = `role: ${roleKey} • dept: ${role.department || 'engineering'}`;
  document.getElementById('modal-system-prompt').value = role.system || '';

  populateModelSelect('modal-primary-model', role.primary);
  populateModelSelect('modal-fallback-model-1', (role.fallbacks && role.fallbacks[0]) || '');
  populateModelSelect('modal-fallback-model-2', (role.fallbacks && role.fallbacks[1]) || '');

  renderRoleSkillsCheckboxes(role.skills || []);

  document.getElementById('role-modal').classList.remove('hidden');
  lucide.createIcons();
}

function closeRoleModal() {
  document.getElementById('role-modal').classList.add('hidden');
  currentEditingRole = null;
}

function renderRoleSkillsCheckboxes(activeSkills) {
  const container = document.getElementById('modal-skills-list');
  const countEl = document.getElementById('modal-skills-count');
  if (!container) return;
  container.innerHTML = '';

  const activeSet = new Set(activeSkills || []);
  if (countEl) countEl.textContent = `${activeSet.size}/${installedSkills.length} skills`;

  if (installedSkills.length === 0) {
    container.innerHTML = '<div class="col-span-2 text-slate-500 text-center py-2">Chưa tìm thấy skill trong ~/.agents/skills/</div>';
    return;
  }

  installedSkills.forEach(s => {
    const label = document.createElement('label');
    label.className = 'flex items-center space-x-2 p-1.5 rounded hover:bg-slate-900 cursor-pointer border border-slate-800/60 select-none';
    const isChecked = activeSet.has(s.name);
    label.innerHTML = `
      <input type="checkbox" value="${s.name}" ${isChecked ? 'checked' : ''} onchange="updateModalSkillsCount()" class="rounded border-slate-700 text-emerald-500 focus:ring-0 bg-slate-900">
      <span class="truncate text-slate-300 text-[11px]" title="${s.summary || s.name}">${s.name}</span>
    `;
    container.appendChild(label);
  });
}

function updateModalSkillsCount() {
  const container = document.getElementById('modal-skills-list');
  const countEl = document.getElementById('modal-skills-count');
  if (!container || !countEl) return;
  const checked = container.querySelectorAll('input[type="checkbox"]:checked');
  countEl.textContent = `${checked.length}/${installedSkills.length} skills`;
}

function getCheckedSkillsFromModal() {
  const container = document.getElementById('modal-skills-list');
  if (!container) return [];
  const checked = container.querySelectorAll('input[type="checkbox"]:checked');
  return Array.from(checked).map(c => c.value);
}

// Skill Manager Modal Functions
function openSkillModal() {
  loadSkills();
  document.getElementById('skill-modal').classList.remove('hidden');
  lucide.createIcons();
}

function closeSkillModal() {
  document.getElementById('skill-modal').classList.add('hidden');
}

function renderSkillManagerList() {
  const container = document.getElementById('skill-manager-list');
  if (!container) return;
  container.innerHTML = '';

  if (installedSkills.length === 0) {
    container.innerHTML = '<div class="text-slate-500 text-center py-4">Chưa có skill nào được cài đặt.</div>';
    return;
  }

  installedSkills.forEach(s => {
    const item = document.createElement('div');
    item.className = 'p-2.5 rounded-lg bg-slate-900/80 border border-slate-800 flex items-start justify-between space-x-3';
    const sizeKb = (s.size / 1024).toFixed(1);
    item.innerHTML = `
      <div class="space-y-0.5 min-w-0 flex-1">
        <div class="flex items-center space-x-2">
          <span class="font-bold text-white text-xs font-mono">${s.name}</span>
          <span class="text-[10px] text-slate-500 font-mono">${sizeKb} KB</span>
        </div>
        <p class="text-[11px] text-slate-400 truncate" title="${s.summary || ''}">${s.summary || ''}</p>
      </div>
      <span class="px-2 py-0.5 rounded bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-[10px] font-mono shrink-0">Installed</span>
    `;
    container.appendChild(item);
  });
  lucide.createIcons();
}

async function installSkillPackage() {
  const input = document.getElementById('input-install-skill');
  const btn = document.getElementById('btn-submit-install-skill');
  const pkg = input ? input.value.trim() : '';
  if (!pkg) {
    alert('Vui lòng nhập tên gói skill (ví dụ: vercel-labs/agent-skills@web-design-guidelines)');
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i><span>Đang cài...</span>';
    lucide.createIcons();
  }

  try {
    const res = await fetch('/api/skills/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ package: pkg })
    });
    const result = await res.json();
    if (result.ok) {
      showToast(`Đã cài đặt thành công: ${pkg}`);
      if (input) input.value = '';
      await loadSkills();
    } else {
      alert(`Lỗi cài đặt skill:\n${result.error || result.stderr || 'Thất bại'}`);
    }
  } catch (err) {
    alert('Lỗi kết nối khi cài đặt skill: ' + err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="download" class="w-4 h-4"></i><span>Cài Đặt</span>';
      lucide.createIcons();
    }
  }
}

function populateModelSelect(elementId, selectedValue) {
  const select = document.getElementById(elementId);
  if (!select) return;
  select.innerHTML = '<option value="">-- Chọn Model --</option>';

  // Group by provider
  const providers = {};
  availableModels.forEach(m => {
    providers[m.provider] = providers[m.provider] || [];
    providers[m.provider].push(m);
  });

  Object.keys(providers).sort().forEach(p => {
    const optGroup = document.createElement('optgroup');
    optGroup.label = `Provider: ${p.toUpperCase()} (${providers[p].length} models)`;
    providers[p].forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = `${m.id} (${(m.context_window / 1000).toFixed(0)}k ctx)`;
      if (m.id === selectedValue) opt.selected = true;
      optGroup.appendChild(opt);
    });
    select.appendChild(optGroup);
  });

  // If selected value not in 9Router list (e.g. edge-tts or ffmpeg), add custom
  if (selectedValue && !availableModels.some(m => m.id === selectedValue)) {
    const customOpt = document.createElement('option');
    customOpt.value = selectedValue;
    customOpt.textContent = `[Custom/Engine] ${selectedValue}`;
    customOpt.selected = true;
    select.prepend(customOpt);
  }
}

async function saveCurrentRoleConfig() {
  if (!currentEditingRole || !currentConfig) return;

  const primary = document.getElementById('modal-primary-model').value;
  const fb1 = document.getElementById('modal-fallback-model-1').value;
  const fb2 = document.getElementById('modal-fallback-model-2').value;
  const system = document.getElementById('modal-system-prompt').value;
  const skills = getCheckedSkillsFromModal();

  const fallbacks = [];
  if (fb1 && fb1 !== primary) fallbacks.push(fb1);
  if (fb2 && fb2 !== primary && fb2 !== fb1) fallbacks.push(fb2);

  currentConfig.roles[currentEditingRole].primary = primary;
  currentConfig.roles[currentEditingRole].fallbacks = fallbacks;
  currentConfig.roles[currentEditingRole].system = system;
  currentConfig.roles[currentEditingRole].skills = skills;

  try {
    const res = await fetch('/api/roles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(currentConfig)
    });
    const result = await res.json();
    if (result.ok) {
      renderOrgChart();
      closeRoleModal();
      showToast('Đã lưu cấu hình Role thành công!');
    } else {
      alert('Lỗi lưu cấu hình: ' + (result.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Không thể kết nối đến UI server: ' + err);
  }
}

async function syncModelsFromRouter() {
  const icon = document.getElementById('icon-sync-router');
  if (icon) icon.classList.add('animate-spin');

  try {
    const res = await fetch('/api/models?refresh=1');
    const data = await res.json();
    if (data.models && data.models.length > 0) {
      allModels = data.models;
      const statusText = document.getElementById('router-status-text');
      if (statusText) statusText.textContent = `9Router Online (${data.models.length} Models)`;
      showToast(`Đã đồng bộ thành công ${data.models.length} models từ 9Router!`);
    } else {
      showToast('9Router trả về 0 models hoặc có lỗi.', true);
    }
  } catch (err) {
    showToast('Không thể kết nối tới 9Router: ' + err, true);
  } finally {
    if (icon) {
      setTimeout(() => icon.classList.remove('animate-spin'), 600);
    }
  }
}

async function quickApplyGeminiFlash() {
  const targetModel = 'ag/gemini-3.8-flash-high';
  const fallbacks = ['ag/gemini-3.8-flash-medium', 'ag/gemini-3.7-flash-high'];

  try {
    const res = await fetch('/api/roles/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: targetModel, fallbacks, department: 'all' })
    });
    const data = await res.json();
    if (data.ok && data.config) {
      currentConfig = data.config;
      renderOrgChart();
      updatePresetButtons('quality');
      showToast(`⚡ Đã thiết lập thành công Gemini 3.8 Flash High cho toàn bộ ${data.updated_count || 'tất cả'} Agent!`);
    } else {
      alert('Lỗi gán model: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Không thể kết nối đến server: ' + err);
  }
}

async function applyPreset(presetName) {
  try {
    const res = await fetch('/api/roles/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset: presetName })
    });
    const data = await res.json();
    if (data.ok && data.config) {
      currentConfig = data.config;
      renderOrgChart();
      updatePresetButtons(presetName);
      const labels = {
        balanced: 'Tối Ưu Tốc Độ & Chất Lượng (Lead: 3.8-high, Worker: 3.8-med)',
        speed: 'Siêu Tốc (Toàn bộ 3.8-flash)',
        quality: 'Chất Lượng Tối Đa (Toàn bộ 3.8-flash-high)'
      };
      showToast(`⚡ Cấu hình: ${labels[presetName] || presetName}`);
    } else {
      alert('Lỗi gán preset: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Không thể kết nối đến server: ' + err);
  }
}

function updatePresetButtons(activePreset) {
  ['balanced', 'speed', 'quality'].forEach(p => {
    const btn = document.getElementById(`btn-preset-${p}`);
    if (!btn) return;
    if (p === activePreset) {
      btn.classList.add('ring-2', 'ring-cyan-400', 'bg-cyan-950/80', 'text-cyan-200');
    } else {
      btn.classList.remove('ring-2', 'ring-cyan-400', 'bg-cyan-950/80', 'text-cyan-200');
    }
  });
}

async function applyPrimaryModelToAllRoles() {
  const selectedModel = document.getElementById('modal-primary-model').value;
  if (!selectedModel) return;

  const fb1 = document.getElementById('modal-fallback-model-1').value;
  const fb2 = document.getElementById('modal-fallback-model-2').value;
  const fallbacks = [];
  if (fb1 && fb1 !== selectedModel) fallbacks.push(fb1);
  if (fb2 && fb2 !== selectedModel && fb2 !== fb1) fallbacks.push(fb2);

  try {
    const res = await fetch('/api/roles/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: selectedModel, fallbacks, department: 'all' })
    });
    const data = await res.json();
    if (data.ok && data.config) {
      currentConfig = data.config;
      renderOrgChart();
      closeRoleModal();
      showToast(`Đã áp dụng model [${selectedModel}] cho toàn bộ vai trò!`);
    } else {
      alert('Lỗi: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Lỗi kết nối: ' + err);
  }
}

// Coding Mission Control
async function handleShipSubmit(e) {
  e.preventDefault();
  const repo = document.getElementById('ship-repo').value.trim();
  const goal = document.getElementById('ship-goal').value.trim();
  const verify = document.getElementById('ship-verify').value.trim();
  const requireApproval = document.getElementById('ship-require-approval').checked;

  const btn = document.getElementById('btn-dispatch-task');
  btn.disabled = true;
  btn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i><span>Đang khởi tạo...</span>';
  lucide.createIcons();

  try {
    const res = await fetch('/api/ship', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo, goal, verify: verify || null, require_approval: requireApproval })
    });
    const data = await res.json();
    if (data.ok && data.project_id) {
      activeProjectId = data.project_id;
      loadProjectsList();
      selectProject(activeProjectId);
      showToast(`Đã dispatch task thành công: ${activeProjectId}`);
    } else {
      alert('Lỗi tạo task: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Lỗi: ' + err);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i data-lucide="send" class="w-4 h-4"></i><span>GIAO VIỆC CHO CÔNG TY (DISPATCH)</span>';
    lucide.createIcons();
  }
}

async function loadProjectsList() {
  try {
    const res = await fetch('/api/projects');
    const data = await res.json();
    const listEl = document.getElementById('projects-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    const projects = data.projects || [];
    if (projects.length === 0) {
      listEl.innerHTML = '<div class="text-xs text-slate-500 text-center py-4">Chưa có dự án nào.</div>';
      return;
    }

    projects.forEach(p => {
      const item = document.createElement('div');
      item.className = `p-2.5 rounded-lg border cursor-pointer transition ${activeProjectId === p.id ? 'bg-cyan-950/40 border-cyan-500/60' : 'bg-slate-900 border-slate-800 hover:border-slate-700'}`;
      item.onclick = () => selectProject(p.id);

      const statusColor = p.status === 'DONE' ? 'text-emerald-400 bg-emerald-950' : (p.status.includes('FAIL') ? 'text-rose-400 bg-rose-950' : 'text-cyan-400 bg-cyan-950');

      item.innerHTML = `
        <div class="flex items-center justify-between">
          <span class="text-xs font-mono font-bold text-white">${p.id}</span>
          <span class="text-[10px] font-mono px-1.5 py-0.5 rounded ${statusColor}">${p.status}</span>
        </div>
        <div class="text-xs text-slate-300 mt-1 truncate" title="${p.goal}">${p.goal}</div>
        <div class="text-[10px] text-slate-500 mt-1 font-mono truncate">${p.repo}</div>
      `;
      listEl.appendChild(item);
    });
  } catch (err) {
    console.error('Load projects error:', err);
  }
}

function selectProject(projectId) {
  activeProjectId = projectId;
  document.getElementById('current-project-id').textContent = projectId;
  document.getElementById('btn-soft-cancel').classList.remove('hidden');
  document.getElementById('btn-hard-cancel').classList.remove('hidden');

  loadProjectDetails(projectId);
  startEventStream(projectId);
  loadProjectsList();
}

async function loadProjectDetails(projectId) {
  try {
    const res = await fetch(`/api/projects/${projectId}`);
    const data = await res.json();
    if (data.error) return;

    const p = data.project;
    document.getElementById('current-project-goal').textContent = p.goal;
    document.getElementById('current-project-status').textContent = p.status;

    // Diff Viewer
    const diffEl = document.getElementById('diff-viewer');
    if (data.diff && data.diff.trim()) {
      diffEl.innerHTML = formatDiff(data.diff);
    } else {
      diffEl.textContent = 'Chưa có code diff hoặc chưa đến bước generate diff.';
    }

    // Tasks DAG
    const tasksEl = document.getElementById('tasks-breakdown');
    if (data.tasks && data.tasks.length > 0) {
      tasksEl.innerHTML = data.tasks.map(t => `
        <div class="p-3 mb-2 rounded-lg bg-slate-900 border border-slate-800">
          <div class="flex items-center justify-between mb-1">
            <span class="font-mono font-bold text-cyan-400">${t.id}</span>
            <span class="text-[10px] px-2 py-0.5 rounded font-mono ${t.status === 'DONE' ? 'bg-emerald-950 text-emerald-400' : 'bg-slate-800 text-slate-400'}">${t.status}</span>
          </div>
          <div class="text-xs text-slate-200">${t.goal}</div>
          <div class="text-[11px] font-mono text-slate-400 mt-1">Files: ${t.allowed_files.join(', ')}</div>
          <div class="text-[11px] font-mono text-slate-500 mt-0.5">Verify: ${t.verify_command}</div>
        </div>
      `).join('');
    } else {
      tasksEl.textContent = 'Chưa có Task DAG nào được phê duyệt.';
    }

    // Artifacts
    const artEl = document.getElementById('artifacts-view');
    if (data.stages && data.stages.length > 0) {
      artEl.innerHTML = data.stages.map(s => `
        <div class="mb-4 pb-4 border-b border-slate-800">
          <div class="flex items-center space-x-2 mb-2">
            <span class="px-2 py-0.5 rounded bg-cyan-950 text-cyan-400 font-mono uppercase font-bold text-xs">${s.stage}</span>
            <span class="text-slate-400 text-xs font-mono">Model: ${s.model}</span>
          </div>
          <div class="text-xs text-slate-300 bg-slate-900/60 p-3 rounded-lg border border-slate-800 whitespace-pre-wrap">${escapeHtml(s.artifact)}</div>
        </div>
      `).join('');
    }
  } catch (err) {
    console.error('Load project details error:', err);
  }
}

let liveStreamTextBuffer = '';
let liveThinkingTextBuffer = '';
let autoScrollEnabled = true;
let thinkingExpanded = true;
let isFirstLiveChunk = true;

function toggleAutoScroll() {
  autoScrollEnabled = !autoScrollEnabled;
  const btn = document.getElementById('btn-toggle-autoscroll');
  if (btn) {
    btn.innerHTML = `<i data-lucide="arrow-down" class="w-3 h-3"></i><span>Auto-scroll: ${autoScrollEnabled ? 'ON' : 'OFF'}</span>`;
    lucide.createIcons();
    if (autoScrollEnabled) {
      btn.classList.add('text-cyan-300');
      btn.classList.remove('text-slate-500');
    } else {
      btn.classList.remove('text-cyan-300');
      btn.classList.add('text-slate-500');
    }
  }
}

function toggleThinkingDetails() {
  thinkingExpanded = !thinkingExpanded;
  const content = document.getElementById('live-thinking-content');
  const icon = document.getElementById('thinking-expand-icon');
  if (content && icon) {
    if (thinkingExpanded) {
      content.classList.remove('hidden');
      icon.textContent = 'Thu gọn ▲';
    } else {
      content.classList.add('hidden');
      icon.textContent = 'Mở rộng ▼';
    }
  }
}

function handleLiveStreamEvent(ev) {
  const eventName = ev.event || '';

  const streamTextEl = document.getElementById('live-stream-text');
  const streamBox = document.getElementById('live-stream-box');
  const thinkingBox = document.getElementById('live-thinking-box');
  const thinkingContent = document.getElementById('live-thinking-content');
  const ttftBadge = document.getElementById('live-ttft-badge');
  const tokenCounter = document.getElementById('live-tokens-counter');
  const roleTitleEl = document.getElementById('live-role-title');
  const modelBadgeEl = document.getElementById('live-model-badge');
  const statusSubEl = document.getElementById('live-status-sub');
  const cursorEl = document.getElementById('live-cursor');

  if (eventName.endsWith('_start') && eventName !== 'ship_start' && eventName !== 'implement_start') {
    const stageName = ev.stage || eventName.replace('_start', '');
    isFirstLiveChunk = true;
    liveStreamTextBuffer = '';
    liveThinkingTextBuffer = '';

    if (streamTextEl) streamTextEl.textContent = `[${stageName.toUpperCase()}] Đang kết nối 9Router...`;
    if (thinkingBox) thinkingBox.classList.add('hidden');
    if (thinkingContent) thinkingContent.textContent = '';
    if (ttftBadge) ttftBadge.classList.add('hidden');
    if (tokenCounter) tokenCounter.textContent = '0 ký tự';

    const stageTitles = {
      design: 'Chief Architect đang thiết kế giải pháp hệ thống...',
      critique: 'Senior Critic đang phân tích phản biện & tìm lỗ hổng...',
      judgment: 'Tech Lead đang tổng hợp & lập Task DAG...',
      implementation: 'Senior Worker đang lập trình giải pháp...',
      verification: 'QA Engineer đang chạy máy ảo test...',
      audit: 'Security Auditor đang rà soát an ninh mã nguồn...'
    };

    if (roleTitleEl) roleTitleEl.textContent = stageTitles[stageName] || `Đang thực hiện ${stageName}...`;
    if (statusSubEl) statusSubEl.textContent = 'Đang nhận diện ngữ cảnh và chuẩn bị sinh token...';
    if (cursorEl) cursorEl.classList.remove('hidden');
    return;
  }

  if (eventName === 'worker_start') {
    isFirstLiveChunk = true;
    liveStreamTextBuffer = '';
    liveThinkingTextBuffer = '';

    if (streamTextEl) streamTextEl.textContent = `[TASK: ${ev.task_id}] Worker bắt đầu triển khai code...`;
    if (thinkingBox) thinkingBox.classList.add('hidden');
    if (thinkingContent) thinkingContent.textContent = '';
    if (ttftBadge) ttftBadge.classList.add('hidden');
    if (tokenCounter) tokenCounter.textContent = '0 ký tự';

    if (roleTitleEl) roleTitleEl.textContent = `Worker Agent đang triển khai ${ev.task_id}`;
    if (modelBadgeEl && ev.model) modelBadgeEl.textContent = ev.model;
    if (statusSubEl) statusSubEl.textContent = 'Bắt đầu luồng mã nguồn SSE...';
    if (cursorEl) cursorEl.classList.remove('hidden');
    return;
  }

  if (eventName === 'stream_ttft') {
    if (ttftBadge) {
      ttftBadge.textContent = `⚡ TTFT: ${ev.ttft_ms}ms`;
      ttftBadge.classList.remove('hidden');
    }
    if (modelBadgeEl && ev.model) modelBadgeEl.textContent = ev.model;
    if (statusSubEl) statusSubEl.textContent = `Streaming token từ 9Router (TTFT: ${ev.ttft_ms}ms)`;
    if (cursorEl) cursorEl.classList.remove('hidden');
    return;
  }

  if (eventName === 'stream_thinking') {
    if (thinkingBox) thinkingBox.classList.remove('hidden');
    if (thinkingContent) {
      liveThinkingTextBuffer += ev.chunk || '';
      thinkingContent.textContent = liveThinkingTextBuffer;
      if (autoScrollEnabled) {
        thinkingContent.scrollTop = thinkingContent.scrollHeight;
      }
    }
    return;
  }

  if (eventName === 'stream_chunk') {
    if (isFirstLiveChunk) {
      liveStreamTextBuffer = '';
      isFirstLiveChunk = false;
    }
    liveStreamTextBuffer += ev.chunk || '';
    if (streamTextEl) {
      streamTextEl.textContent = liveStreamTextBuffer;
    }
    if (tokenCounter) {
      tokenCounter.textContent = `${liveStreamTextBuffer.length} ký tự`;
    }
    if (cursorEl) cursorEl.classList.remove('hidden');
    if (autoScrollEnabled && streamBox) {
      streamBox.scrollTop = streamBox.scrollHeight;
    }
    return;
  }

  if (eventName.endsWith('_done') || eventName.endsWith('_complete')) {
    if (cursorEl) cursorEl.classList.add('hidden');
    if (statusSubEl) {
      const charCount = ev.chars || liveStreamTextBuffer.length;
      statusSubEl.textContent = `Hoàn thành (${charCount} ký tự${ev.model ? ', ' + ev.model : ''})`;
    }
    if (activeProjectId) {
      loadProjectDetails(activeProjectId);
    }
    return;
  }

  if (eventName === 'task_dag') {
    if (activeProjectId) {
      loadProjectDetails(activeProjectId);
    }
    return;
  }

  if (eventName === 'ship_failed') {
    if (cursorEl) cursorEl.classList.add('hidden');
    if (statusSubEl) statusSubEl.textContent = `Thất bại: ${ev.error || 'Unknown error'}`;
    if (activeProjectId) {
      loadProjectDetails(activeProjectId);
    }
  }
}

function startEventStream(projectId) {
  if (currentEventSource) {
    currentEventSource.close();
  }

  const logsEl = document.getElementById('terminal-logs');
  logsEl.innerHTML = `<div class="text-slate-500">// Bắt đầu stream event cho project ${projectId}...</div>`;

  liveStreamTextBuffer = '';
  liveThinkingTextBuffer = '';
  isFirstLiveChunk = true;

  const streamTextEl = document.getElementById('live-stream-text');
  if (streamTextEl) streamTextEl.textContent = `Đang kết nối live stream cho dự án ${projectId}...`;
  const thinkingBox = document.getElementById('live-thinking-box');
  if (thinkingBox) thinkingBox.classList.add('hidden');
  const ttftBadge = document.getElementById('live-ttft-badge');
  if (ttftBadge) ttftBadge.classList.add('hidden');
  const tokenCounter = document.getElementById('live-tokens-counter');
  if (tokenCounter) tokenCounter.textContent = '0 ký tự';

  currentEventSource = new EventSource(`/api/events/${projectId}`);

  currentEventSource.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data);
      appendTerminalLog(ev);
      updateStageFlow(ev.event, ev);
      handleLiveStreamEvent(ev);
    } catch (err) {
      console.error(err);
    }
  };

  currentEventSource.onerror = () => {
    // Keep alive or closed
  };
}

function appendTerminalLog(ev) {
  if (ev.event === 'stream_chunk' || ev.event === 'stream_thinking') {
    return; // Don't spam terminal log with thousands of raw token chunks
  }

  const logsEl = document.getElementById('terminal-logs');
  const line = document.createElement('div');
  line.className = 'font-mono text-[11px] leading-tight py-0.5 flex space-x-2';

  const timeStr = new Date(ev.ts * 1000).toLocaleTimeString();
  const eventName = ev.event || 'log';

  let eventClass = 'text-cyan-400';
  if (eventName.includes('fail') || eventName.includes('error')) eventClass = 'text-rose-400 font-bold';
  if (eventName.includes('approve') || eventName.includes('success')) eventClass = 'text-emerald-400 font-bold';
  if (eventName === 'stream_ttft') eventClass = 'text-amber-300 font-bold';

  line.innerHTML = `
    <span class="text-slate-600 select-none">[${timeStr}]</span>
    <span class="${eventClass} font-semibold">${eventName}</span>
    <span class="text-slate-300 break-all">${escapeHtml(JSON.stringify(ev))}</span>
  `;

  logsEl.appendChild(line);
  logsEl.scrollTop = logsEl.scrollHeight;
}

function updateStageFlow(eventName, ev) {
  const stages = ['design', 'critique', 'judgment', 'implementation', 'verification', 'audit'];
  
  stages.forEach(s => {
    const badge = document.getElementById(`stage-badge-${s}`);
    if (!badge) return;
    if (eventName === `${s}_start`) {
      badge.className = 'p-2 rounded-lg border border-cyan-500 bg-cyan-950/80 text-cyan-300 font-bold animate-pulse';
      highlightRoleCard(s, 'working');
    } else if (eventName === `${s}_done` || eventName === `${s}_complete`) {
      badge.className = 'p-2 rounded-lg border border-emerald-500 bg-emerald-950/50 text-emerald-400 font-bold';
      highlightRoleCard(s, 'approved');
    }
  });

  if (eventName === 'critique_start') {
    highlightRoleCard('critique', 'critiquing');
  }

  // Check for Human Approval required
  if (eventName === 'human_approval_required' || (eventName === 'audit_done' && ev.approved)) {
    document.getElementById('human-approval-banner').classList.remove('hidden');
    highlightRoleCard('audit', 'approved');
  }

  if (eventName === 'ship_complete' || eventName === 'merge_done') {
    document.getElementById('current-project-status').textContent = 'DONE';
    document.getElementById('human-approval-banner').classList.add('hidden');
  }
}

function highlightRoleCard(roleKey, state) {
  const card = document.getElementById(`role-card-${roleKey}`);
  const statusEl = document.getElementById(`role-status-${roleKey}`);
  if (!card || !statusEl) return;

  card.classList.remove('state-working', 'state-critiquing', 'state-approved');

  if (state === 'working') {
    card.classList.add('state-working');
    statusEl.innerHTML = '<span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span><span class="text-cyan-400 font-bold">Working</span>';
  } else if (state === 'critiquing') {
    card.classList.add('state-critiquing');
    statusEl.innerHTML = '<span class="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span><span class="text-amber-400 font-bold">Critiquing</span>';
  } else if (state === 'approved') {
    card.classList.add('state-approved');
    statusEl.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400"></span><span class="text-emerald-400 font-bold">Approved</span>';
  }
}

async function handleApproveProject() {
  if (!activeProjectId) return;
  try {
    const res = await fetch('/api/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: activeProjectId, note: 'Approved by CEO via UI' })
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('human-approval-banner').classList.add('hidden');
      showToast('Đã cấp quyền Human Approval! Quá trình merge đang diễn ra.');
    }
  } catch (err) {
    alert('Lỗi duyệt: ' + err);
  }
}

async function handleCancelProject(hard) {
  if (!activeProjectId) return;
  if (!confirm(hard ? 'DỪNG KHẨN CẤP: Tiến trình sẽ bị kill ngay lập tức?' : 'Bạn muốn gửi tín hiệu huỷ mềm?')) return;
  try {
    const res = await fetch('/api/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: activeProjectId, soft: !hard })
    });
    const data = await res.json();
    if (data.ok) {
      showToast('Đã gửi yêu cầu dừng dự án.');
    }
  } catch (err) {
    alert('Lỗi huỷ: ' + err);
  }
}

// Media Studio
async function handleGenerateScript() {
  const product = document.getElementById('media-product').value.trim();
  const topic = document.getElementById('media-topic').value.trim();
  const audience = document.getElementById('media-audience').value.trim();

  if (!product && !topic) {
    alert('Vui lòng nhập tên sản phẩm hoặc điểm nhấn!');
    return;
  }

  const btn = document.getElementById('btn-generate-script');
  btn.disabled = true;
  btn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i><span>Đang viết kịch bản...</span>';
  lucide.createIcons();

  highlightRoleCard('scriptwriter', 'working');

  try {
    const res = await fetch('/api/media/script', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product, topic, target_audience: audience })
    });
    const data = await res.json();
    highlightRoleCard('scriptwriter', 'approved');

    if (data.ok && data.content) {
      renderMediaScenes(data.content);
      showToast('Kịch bản video đã được sinh thành công!');
    } else {
      alert('Lỗi sinh kịch bản: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Lỗi kết nối: ' + err);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i data-lucide="sparkles" class="w-4 h-4"></i><span>SINH KỊCH BẢN VIDEO (AI)</span>';
    lucide.createIcons();
  }
}

function renderMediaScenes(rawContent) {
  const container = document.getElementById('media-scenes-output');
  container.innerHTML = '';

  let scenes = [];
  try {
    const jsonMatch = rawContent.match(/\[\s*\{[\s\S]*\}\s*\]/);
    if (jsonMatch) {
      scenes = JSON.parse(jsonMatch[0]);
    }
  } catch (e) {
    console.error('Cannot parse scenes json, displaying raw:', e);
  }

  if (scenes.length === 0) {
    container.innerHTML = `<div class="bg-slate-950 p-4 rounded-xl border border-slate-800 text-xs text-slate-300 whitespace-pre-wrap">${escapeHtml(rawContent)}</div>`;
    return;
  }

  scenes.forEach((s, idx) => {
    const sceneCard = document.createElement('div');
    sceneCard.className = 'bg-slate-950 p-4 rounded-xl border border-slate-800 space-y-2';
    sceneCard.innerHTML = `
      <div class="flex items-center justify-between">
        <span class="text-xs font-bold font-mono px-2 py-0.5 rounded bg-purple-950 text-purple-400 border border-purple-800">CẢNH ${s.scene || idx + 1} (${s.duration_s || 3}s)</span>
        <button onclick="handleGeneratePrompts(${idx}, '${escapeQuotes(s.visual || '')}')" id="btn-prompt-${idx}" class="text-[11px] px-2.5 py-1 rounded bg-purple-900/60 hover:bg-purple-800 text-purple-200 border border-purple-700 flex items-center space-x-1">
          <i data-lucide="sparkles" class="w-3 h-3"></i>
          <span>Sinh Prompt Ảnh/Video</span>
        </button>
      </div>
      <div class="text-xs text-slate-300"><strong class="text-slate-400">Hình ảnh:</strong> ${s.visual || ''}</div>
      <div class="text-xs text-emerald-300 font-medium"><strong class="text-slate-400">Lời thoại:</strong> "${s.narration || ''}"</div>
      <div class="text-[11px] text-cyan-400 font-mono"><strong class="text-slate-400">Chữ trên màn hình:</strong> ${s.onscreen_text || ''}</div>
      <div id="prompts-result-${idx}" class="hidden mt-2 pt-2 border-t border-slate-800/80 text-[11px] font-mono text-slate-300 bg-slate-900/80 p-2.5 rounded-lg whitespace-pre-wrap"></div>
    `;
    container.appendChild(sceneCard);
  });
  lucide.createIcons();
}

async function handleGeneratePrompts(idx, visualDesc) {
  const btn = document.getElementById(`btn-prompt-${idx}`);
  const resultEl = document.getElementById(`prompts-result-${idx}`);
  if (!btn || !resultEl) return;

  btn.disabled = true;
  btn.textContent = 'Đang sinh prompt...';
  highlightRoleCard('prompter', 'working');

  try {
    const res = await fetch('/api/media/prompts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scene_description: visualDesc })
    });
    const data = await res.json();
    highlightRoleCard('prompter', 'approved');

    if (data.ok && data.content) {
      resultEl.classList.remove('hidden');
      resultEl.textContent = data.content;
      btn.textContent = 'Đã sinh prompt ✓';
    }
  } catch (err) {
    alert('Lỗi: ' + err);
    btn.disabled = false;
    btn.textContent = 'Sinh Prompt Ảnh/Video';
  }
}

// Token & Cost Ledger
async function loadUsage() {
  try {
    const res = await fetch('/api/usage');
    const data = await res.json();

    const totals = data.totals || {};
    document.getElementById('total-cost-usd').textContent = `$${(totals.cost_usd || 0).toFixed(4)}`;
    document.getElementById('total-model-calls').textContent = `${totals.calls || 0} calls`;
    document.getElementById('total-tokens').textContent = `${((totals.tokens_in || 0) + (totals.tokens_out || 0)).toLocaleString()} tokens`;

    const tbody = document.getElementById('usage-breakdown-body');
    if (!tbody) return;
    tbody.innerHTML = '';

    const breakdown = data.breakdown || [];
    if (breakdown.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center py-6 text-slate-500">Chưa có bản ghi chi phí nào.</td></tr>';
      return;
    }

    breakdown.forEach(row => {
      const tr = document.createElement('tr');
      tr.className = 'hover:bg-slate-900/40 transition';
      tr.innerHTML = `
        <td class="py-2.5 px-4 font-bold text-cyan-400">${row.role}</td>
        <td class="py-2.5 px-4 text-slate-300">${row.model}</td>
        <td class="py-2.5 px-4 text-slate-400">${row.calls}</td>
        <td class="py-2.5 px-4 text-slate-400">${(row.tokens_in || 0).toLocaleString()}</td>
        <td class="py-2.5 px-4 text-slate-400">${(row.tokens_out || 0).toLocaleString()}</td>
        <td class="py-2.5 px-4 text-right font-bold text-emerald-400">$${(row.cost || 0).toFixed(4)}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error('Load usage error:', err);
  }
}

// Diff Formatter Helper
function formatDiff(rawDiff) {
  const lines = rawDiff.split('\n');
  return lines.map(line => {
    if (line.startsWith('+') && !line.startsWith('+++')) {
      return `<div class="diff-add px-2 py-0.5">${escapeHtml(line)}</div>`;
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      return `<div class="diff-del px-2 py-0.5">${escapeHtml(line)}</div>`;
    } else if (line.startsWith('diff --git') || line.startsWith('@@')) {
      return `<div class="diff-header px-2 py-0.5 font-bold">${escapeHtml(line)}</div>`;
    }
    return `<div class="px-2 py-0.5 text-slate-400">${escapeHtml(line)}</div>`;
  }).join('');
}

// Helpers
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escapeQuotes(str) {
  if (!str) return '';
  return str.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function showToast(message) {
  const toast = document.createElement('div');
  toast.className = 'fixed bottom-6 right-6 z-50 px-4 py-2.5 rounded-xl bg-slate-900 border border-cyan-500/50 text-white text-xs shadow-2xl flex items-center space-x-2 animate-bounce';
  toast.innerHTML = `<i data-lucide="check-circle" class="w-4 h-4 text-cyan-400"></i><span>${message}</span>`;
  document.body.appendChild(toast);
  lucide.createIcons();
  setTimeout(() => toast.remove(), 3500);
}

// Start
document.addEventListener('DOMContentLoaded', init);
