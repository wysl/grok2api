const state = {
  providers: [],
  routeStrategy: '',
  editingId: null,
  drawerProviderId: null,
};

const modelTypeOptions = ['chat', 'reasoning', 'image', 'image_edit', 'video', 'embedding', 'audio'];

function tr(key, params, fallback) {
  if (typeof window.t !== 'function') return fallback ?? key;
  const value = window.t(key, params);
  return value === key ? (fallback ?? key) : value;
}

function esc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function normalizeList(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.providers)) return payload.providers;
  if (Array.isArray(payload?.data)) return payload.data;
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

function normalizeModels(models) {
  if (!models) return [];
  if (Array.isArray(models)) return models;
  if (Array.isArray(models.data)) return models.data.map((item) => item.id || item.name || item.model || String(item));
  if (typeof models === 'object') return Object.keys(models);
  return [];
}

function getProviderId(provider) {
  return provider.id ?? provider.provider_id ?? provider.name;
}

function getProviderById(id) {
  return state.providers.find((provider) => String(getProviderId(provider)) === String(id));
}

function modelCount(provider) {
  return normalizeModels(provider.models).length;
}

function formatDateTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function statusLabel(status, enabled) {
  if (!enabled) return tr('upstreams.statusDisabled', null, '禁用');
  const key = `upstreams.status${String(status || 'active').replace(/^./, (c) => c.toUpperCase())}`;
  return tr(key, null, status || 'active');
}

function statusClass(status, enabled) {
  if (!enabled) return 'badge-disabled';
  if (status === 'error') return 'badge-error';
  if (status === 'cooling') return 'badge-cooling';
  if (status === 'disabled') return 'badge-disabled';
  return 'badge-active';
}

async function api(method, path, body) {
  const key = await adminKey.get();
  const response = await fetch(ADMIN_API + path, {
    method,
    headers: {
      ...(body != null && { 'Content-Type': 'application/json' }),
      Authorization: `Bearer ${key}`,
    },
    ...(body != null && { body: JSON.stringify(body) }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.message || response.status);
  return data;
}

async function loadProviders() {
  const payload = await api('GET', '/upstreams');
  state.providers = normalizeList(payload);
  state.routeStrategy = payload?.route_strategy || payload?.strategy || payload?.routing_strategy || '';
  render();
}

function renderStats() {
  const enabled = state.providers.filter((provider) => provider.enabled !== false).length;
  const active = state.providers.filter((provider) => provider.enabled !== false && (provider.status || 'active') === 'active').length;
  const models = state.providers.reduce((sum, provider) => sum + modelCount(provider), 0);
  document.getElementById('stat-total').textContent = state.providers.length;
  document.getElementById('stat-enabled').textContent = enabled;
  document.getElementById('stat-active').textContent = active;
  document.getElementById('stat-models').textContent = models;
  document.getElementById('strategy-pill').textContent = state.routeStrategy || tr('upstreams.strategyUnknown', null, '未配置');
  document.getElementById('table-summary').textContent = tr('upstreams.tableSummary', { n: state.providers.length }, `共 ${state.providers.length} 个 Provider`);
}

function renderProviders() {
  const tbody = document.getElementById('provider-tbody');
  if (!state.providers.length) {
    tbody.innerHTML = `<tr><td class="table-empty" colspan="10">${esc(tr('upstreams.empty', null, '暂无 Provider'))}</td></tr>`;
    return;
  }

  tbody.innerHTML = state.providers.map((provider) => {
    const id = getProviderId(provider);
    const enabled = provider.enabled !== false;
    const status = provider.status || (enabled ? 'active' : 'disabled');
    const apiKey = provider.api_key_mask || provider.masked_api_key || provider.api_key || '';
    const lastError = provider.last_fail_reason || provider.last_error || '-';
    return `
      <tr data-provider-id="${esc(id)}">
        <td>
          <div class="provider-name">
            <strong>${esc(provider.name || '-')}</strong>
            <small>${esc(apiKey || tr('upstreams.noApiKey', null, '未设置 key'))}</small>
          </div>
        </td>
        <td><span class="badge badge-basic">${esc(provider.provider_type || provider.type || '-')}</span></td>
        <td class="mono">${esc(provider.base_url || '-')}</td>
        <td>
          <label class="toggle">
            <input type="checkbox" data-action="toggle" ${enabled ? 'checked' : ''}>
            <span class="toggle-track"></span>
          </label>
        </td>
        <td class="mono">${esc(provider.priority ?? 0)} / ${esc(provider.weight ?? 1)}</td>
        <td><span class="badge ${statusClass(status, enabled)}">${esc(statusLabel(status, enabled))}</span></td>
        <td><button class="page-action-btn" type="button" data-action="models">${esc(tr('upstreams.modelCount', { n: modelCount(provider) }, `${modelCount(provider)} 个`))}</button></td>
        <td class="mono">${esc(formatDateTime(provider.last_health_at))}</td>
        <td class="muted">${esc(lastError)}</td>
        <td>
          <div class="row-actions">
            <button class="row-action" type="button" data-action="test" title="${esc(tr('upstreams.actionTest', null, '测试连接'))}"><svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg></button>
            <button class="row-action" type="button" data-action="refresh-models" title="${esc(tr('upstreams.actionRefreshModels', null, '刷新模型'))}"><svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg></button>
            <button class="row-action" type="button" data-action="edit" title="${esc(tr('upstreams.actionEdit', null, '编辑'))}"><svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg></button>
            <button class="row-action row-action-danger" type="button" data-action="delete" title="${esc(tr('upstreams.actionDelete', null, '删除'))}"><svg viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg></button>
          </div>
        </td>
      </tr>`;
  }).join('');
}

function render() {
  renderStats();
  renderProviders();
}

function openModal(provider = null) {
  state.editingId = provider ? getProviderId(provider) : null;
  document.getElementById('provider-modal-title').textContent = provider
    ? tr('upstreams.editTitle', null, '编辑 Provider')
    : tr('upstreams.addTitle', null, '新增 Provider');
  document.getElementById('provider-name').value = provider?.name || '';
  document.getElementById('provider-type').value = provider?.provider_type || provider?.type || 'openai_compatible';
  document.getElementById('provider-enabled').checked = provider?.enabled !== false;
  document.getElementById('provider-base-url').value = provider?.base_url || '';
  document.getElementById('provider-api-key').value = '';
  document.getElementById('provider-api-key').placeholder = provider?.api_key_mask || provider?.masked_api_key || '';
  document.getElementById('provider-priority').value = provider?.priority ?? 0;
  document.getElementById('provider-weight').value = provider?.weight ?? 1;
  document.getElementById('provider-model-types').value = provider?.model_types ? JSON.stringify(provider.model_types, null, 2) : '';
  document.getElementById('api-key-hint').textContent = provider
    ? tr('upstreams.apiKeyHintEdit', null, '留空表示不修改，保存后不回显明文。')
    : tr('upstreams.apiKeyHintNew', null, '保存后不回显明文。');
  document.getElementById('provider-modal').classList.add('open');
  document.getElementById('provider-modal').setAttribute('aria-hidden', 'false');
}

function closeModal() {
  document.getElementById('provider-modal').classList.remove('open');
  document.getElementById('provider-modal').setAttribute('aria-hidden', 'true');
  state.editingId = null;
}

function readForm() {
  const payload = {
    name: document.getElementById('provider-name').value.trim(),
    provider_type: document.getElementById('provider-type').value,
    base_url: document.getElementById('provider-base-url').value.trim(),
    enabled: document.getElementById('provider-enabled').checked,
    priority: Number(document.getElementById('provider-priority').value || 0),
    weight: Number(document.getElementById('provider-weight').value || 1),
  };
  const apiKey = document.getElementById('provider-api-key').value.trim();
  if (apiKey) payload.api_key = apiKey;
  const modelTypes = document.getElementById('provider-model-types').value.trim();
  if (modelTypes) payload.model_types = JSON.parse(modelTypes);
  return payload;
}

async function saveProvider(event) {
  event.preventDefault();
  let payload;
  try {
    payload = readForm();
  } catch {
    showToast(tr('upstreams.invalidModelTypes', null, '模型能力标注 JSON 无效'), 'error');
    return;
  }
  if (!payload.name || !payload.base_url) {
    showToast(tr('upstreams.requiredFields', null, '请填写名称和 Base URL'), 'error');
    return;
  }
  const saveButton = document.getElementById('provider-save');
  saveButton.disabled = true;
  try {
    if (state.editingId != null) await api('PATCH', `/upstreams/${encodeURIComponent(state.editingId)}`, payload);
    else await api('POST', '/upstreams', payload);
    closeModal();
    showToast(tr('upstreams.saveDone', null, '保存完成'));
    await loadProviders();
  } catch (error) {
    showToast(`${tr('upstreams.saveFailed', null, '保存失败')}: ${error.message}`, 'error');
  } finally {
    saveButton.disabled = false;
  }
}

async function updateProvider(id, patch, successMessage) {
  await api('PATCH', `/upstreams/${encodeURIComponent(id)}`, patch);
  showToast(successMessage);
  await loadProviders();
}

async function testProvider(id) {
  const result = await api('POST', `/upstreams/${encodeURIComponent(id)}/test`);
  showToast(result.message || tr('upstreams.testDone', null, '连接测试完成'), result.ok === false || result.status === 'error' ? 'error' : 'success');
  await loadProviders();
}

async function refreshModels(id) {
  const result = await api('POST', `/upstreams/${encodeURIComponent(id)}/refresh-models`);
  showToast(result.message || tr('upstreams.refreshModelsDone', null, '模型刷新完成'));
  await loadProviders();
}

async function deleteProvider(id) {
  const provider = getProviderById(id);
  const name = provider?.name || id;
  if (!confirm(tr('upstreams.deleteConfirm', { name }, `确认删除 ${name}？`))) return;
  await api('DELETE', `/upstreams/${encodeURIComponent(id)}`);
  showToast(tr('upstreams.deleteDone', null, '已删除'));
  await loadProviders();
}

function openModelDrawer(id) {
  const provider = getProviderById(id);
  if (!provider) return;
  state.drawerProviderId = id;
  const models = normalizeModels(provider.models);
  const modelTypes = provider.model_types || {};
  document.getElementById('model-drawer-sub').textContent = `${provider.name || id} · ${models.length}`;
  document.getElementById('model-list').innerHTML = models.length ? models.map((model) => {
    const modelId = typeof model === 'string' ? model : (model.id || model.name || model.model || String(model));
    const value = modelTypes[modelId] || model.capability || model.type || 'chat';
    return `<div class="model-item" data-model="${esc(modelId)}">
      <div class="model-name">${esc(modelId)}</div>
      <select class="model-type">${modelTypeOptions.map((option) => `<option value="${esc(option)}" ${option === value ? 'selected' : ''}>${esc(option)}</option>`).join('')}</select>
    </div>`;
  }).join('') : `<div class="table-empty">${esc(tr('upstreams.noModels', null, '暂无模型，请先刷新模型列表。'))}</div>`;
  document.getElementById('model-drawer').classList.add('open');
  document.getElementById('model-drawer').setAttribute('aria-hidden', 'false');
}

function closeModelDrawer() {
  document.getElementById('model-drawer').classList.remove('open');
  document.getElementById('model-drawer').setAttribute('aria-hidden', 'true');
  state.drawerProviderId = null;
}

async function saveModelTypes() {
  if (state.drawerProviderId == null) return;
  const modelTypes = {};
  document.querySelectorAll('#model-list .model-item').forEach((item) => {
    const model = item.dataset.model;
    const type = item.querySelector('.model-type')?.value;
    if (model && type) modelTypes[model] = type;
  });
  await updateProvider(state.drawerProviderId, { model_types: modelTypes }, tr('upstreams.modelTypesDone', null, '能力标注已保存'));
  closeModelDrawer();
}

async function handleTableClick(event) {
  const actionNode = event.target.closest('[data-action]');
  if (!actionNode) return;
  const row = event.target.closest('tr[data-provider-id]');
  if (!row) return;
  const id = row.dataset.providerId;
  const action = actionNode.dataset.action;
  if (action === 'toggle') return;
  actionNode.disabled = true;
  try {
    if (action === 'models') openModelDrawer(id);
    if (action === 'test') await testProvider(id);
    if (action === 'refresh-models') await refreshModels(id);
    if (action === 'edit') openModal(getProviderById(id));
    if (action === 'delete') await deleteProvider(id);
  } catch (error) {
    showToast(`${tr('upstreams.operationFailed', null, '操作失败')}: ${error.message}`, 'error');
  } finally {
    actionNode.disabled = false;
  }
}

async function handleToggle(event) {
  const input = event.target.closest('input[data-action="toggle"]');
  if (!input) return;
  const row = event.target.closest('tr[data-provider-id]');
  if (!row) return;
  const id = row.dataset.providerId;
  input.disabled = true;
  try {
    await updateProvider(id, { enabled: input.checked }, input.checked ? tr('upstreams.enabledDone', null, '已启用') : tr('upstreams.disabledDone', null, '已禁用'));
  } catch (error) {
    input.checked = !input.checked;
    showToast(`${tr('upstreams.operationFailed', null, '操作失败')}: ${error.message}`, 'error');
  } finally {
    input.disabled = false;
  }
}

async function init() {
  await renderAdminHeader?.();
  document.title = tr('upstreams.pageTitle', null, 'Grok2API - 数据源接入');
  const key = await adminKey.get();
  if (!key || !await verifyKey(ADMIN_API + '/verify', key).catch(() => false)) {
    location.href = '/admin/login';
    return;
  }

  document.getElementById('add-btn').addEventListener('click', () => openModal());
  document.getElementById('refresh-btn').addEventListener('click', async () => {
    try {
      await loadProviders();
      showToast(tr('upstreams.refreshDone', null, '已刷新'));
    } catch (error) {
      showToast(`${tr('upstreams.loadFailed', null, '加载失败')}: ${error.message}`, 'error');
    }
  });
  document.getElementById('provider-form').addEventListener('submit', saveProvider);
  document.getElementById('provider-cancel').addEventListener('click', closeModal);
  document.getElementById('provider-modal').addEventListener('click', (event) => {
    if (event.target.id === 'provider-modal') closeModal();
  });
  document.getElementById('provider-tbody').addEventListener('click', handleTableClick);
  document.getElementById('provider-tbody').addEventListener('change', handleToggle);
  document.getElementById('model-drawer-close').addEventListener('click', closeModelDrawer);
  document.getElementById('model-drawer').addEventListener('click', (event) => {
    if (event.target.id === 'model-drawer') closeModelDrawer();
  });
  document.getElementById('model-save').addEventListener('click', async () => {
    try {
      await saveModelTypes();
    } catch (error) {
      showToast(`${tr('upstreams.saveFailed', null, '保存失败')}: ${error.message}`, 'error');
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeModal();
      closeModelDrawer();
    }
  });

  try {
    await loadProviders();
  } catch (error) {
    document.getElementById('provider-tbody').innerHTML = `<tr><td class="table-empty" colspan="10">${esc(tr('upstreams.loadFailed', null, '加载失败'))}: ${esc(error.message)}</td></tr>`;
    showToast(`${tr('upstreams.loadFailed', null, '加载失败')}: ${error.message}`, 'error');
  }
}

if (window.I18n?.onReady) I18n.onReady(init);
else document.addEventListener('DOMContentLoaded', init);
