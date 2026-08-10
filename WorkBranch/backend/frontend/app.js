'use strict';

const F = (key, label, opts = {}) => Object.assign({ key, label, type: 'text', where: 'body' }, opts);

const LOG_EVENTS = [
  'create_conversation', 'delete_conversation', 'switch_conversation', 'send_message',
  'stream_completed', 'stream_failed', 'client.restored', 'workspace.loaded',
  'auto_arrange_conversations', 'move_conversation_node',
];

const LOG_FIELDS = [
  F('level', 'level', { type: 'select', options: [['INFO', 'INFO'], ['WARNING', 'WARNING'], ['ERROR', 'ERROR']] }),
  F('event', 'event', { type: 'select', options: LOG_EVENTS.map((e) => [e, e]) }),
  F('msg', 'msg', { placeholder: '日志消息（≤512字符）' }),
  F('extra', 'extra (JSON)', { type: 'json', placeholder: '{"conversation_id":"..."}', wide: true }),
  F('client_ts', 'client_ts', { placeholder: '客户端时间戳，可选' }),
];

const GROUPS = [
  {
    name: '健康检查',
    endpoints: [
      { id: 'health', method: 'GET', path: '/health', auth: false, desc: '服务健康状态、实例信息、资源使用与活跃任务。', fields: [] },
      { id: 'health-ready', method: 'GET', path: '/health/ready', auth: false, desc: '就绪探针；排空期间返回 503。', fields: [] },
      { id: 'admin-drain', method: 'POST', path: '/admin/drain', confirm: true, desc: '开始排空：拒绝新会话直至服务恢复。', fields: [] },
      { id: 'logs', method: 'POST', path: '/api/logs', desc: '前端日志上报（同源校验；level/event 必须在白名单内）。', fields: LOG_FIELDS },
      { id: 'logs-alias', method: 'POST', path: '/logs', desc: '/api/logs 的别名接口，请求体相同。', fields: LOG_FIELDS },
    ],
  },
  {
    name: '用户',
    endpoints: [
      { id: 'user-profile', method: 'GET', path: '/user/profile', desc: '当前用户信息（由 X-User-ID 决定）。', fields: [] },
      { id: 'user-name', method: 'PUT', path: '/user/profile/name', desc: '修改当前用户昵称。', fields: [F('name', 'name', { placeholder: '新昵称' })] },
    ],
  },
  {
    name: '会话 Session',
    endpoints: [
      { id: 'session-list', method: 'GET', path: '/session/sessions', desc: '当前用户的会话列表。', fields: [] },
      { id: 'session-create', method: 'POST', path: '/session/sessions', desc: '创建新会话。', fields: [F('title', 'title', { placeholder: '新会话' })] },
      { id: 'session-title', method: 'POST', path: '/session/sessions/{session_id}/title:generate', desc: '调用 LLM 生成会话标题。', fields: [F('session_id', 'session_id', { type: 'number', where: 'path' })] },
      { id: 'session-get', method: 'GET', path: '/session/sessions/{session_id}', desc: '会话详情。', fields: [F('session_id', 'session_id', { type: 'number', where: 'path' })] },
      { id: 'session-delete', method: 'DELETE', path: '/session/sessions/{session_id}', confirm: true, desc: '删除会话及其全部对话。', fields: [F('session_id', 'session_id', { type: 'number', where: 'path' })] },
      { id: 'session-conversations', method: 'GET', path: '/session/sessions/{session_id}/conversations', desc: '会话下的对话列表。', fields: [F('session_id', 'session_id', { type: 'number', where: 'path' })] },
      { id: 'session-conversation-create', method: 'POST', path: '/session/sessions/{session_id}/conversations', desc: '创建对话并写入首条用户消息；user_content_parts 支持图片理解：{"type":"image","url":"<工作区相对路径或http(s)/data:链接>"}，单条最多5张。', fields: [
        F('session_id', 'session_id', { type: 'number', where: 'path' }),
        F('user_content', 'user_content', { placeholder: '首条用户消息' }),
        F('user_content_parts', 'user_content_parts (JSON)', { type: 'json', placeholder: '[{"type":"text","text":"分析这张图"},{"type":"image","url":"测试图片.png"}]', wide: true }),
        F('idempotency_key', 'idempotency_key', { placeholder: '幂等键，可选' }),
      ] },
    ],
  },
  {
    name: '对话 Conversation',
    endpoints: [
      { id: 'conv-get', method: 'GET', path: '/session/conversations/{conversation_id}', desc: '对话详情（含消息与状态）。', fields: [F('conversation_id', 'conversation_id', { where: 'path' })] },
      { id: 'conv-delete', method: 'DELETE', path: '/session/conversations/{conversation_id}', confirm: true, desc: '删除单个对话。', fields: [F('conversation_id', 'conversation_id', { where: 'path' })] },
      { id: 'conv-cancel', method: 'POST', path: '/session/conversations/{conversation_id}/cancel', desc: '取消正在运行的对话。', fields: [F('conversation_id', 'conversation_id', { where: 'path' })] },
      { id: 'conv-cascade', method: 'DELETE', path: '/session/conversations/{conversation_id}/cascade', confirm: true, desc: '级联删除该对话及其之后的所有对话（回退）。', fields: [F('conversation_id', 'conversation_id', { where: 'path' })] },
      { id: 'conv-messages', method: 'POST', path: '/session/conversations/{conversation_id}/messages', desc: '准备消息：更新用户消息内容并返回消息 ID，不执行 Agent；message_parts 支持图片理解：{"type":"image","url":"<工作区相对路径或http(s)/data:链接>"}，单条最多5张。', fields: [
        F('conversation_id', 'conversation_id', { where: 'path' }),
        F('message', 'message', { placeholder: '用户消息' }),
        F('message_parts', 'message_parts (JSON)', { type: 'json', placeholder: '[{"type":"text","text":"分析这张图"},{"type":"image","url":"测试图片.png"}]', wide: true }),
        F('enable_context', 'enable_context', { type: 'checkbox' }),
      ] },
      { id: 'conv-stream', method: 'GET', path: '/session/conversations/{conversation_id}/stream', sse: true, desc: 'SSE 流式执行对话；首个请求会触发 Agent 运行，完成后自动结束。', fields: [
        F('conversation_id', 'conversation_id', { where: 'path' }),
        F('last_seq', 'last_seq', { type: 'number', where: 'query', default: 0 }),
        F('mode', 'mode', { type: 'select', where: 'query', options: [['', '（默认 interactive）'], ['interactive', 'interactive'], ['silent', 'silent']] }),
      ] },
      { id: 'conv-resume', method: 'POST', path: '/session/conversations/{conversation_id}/resume', desc: '恢复被 ask_user_question 中断的对话。', fields: [
        F('conversation_id', 'conversation_id', { where: 'path' }),
        F('answer', 'answer', { placeholder: '用户回答', wide: true }),
        F('call_seq', 'call_seq', { type: 'number', placeholder: '可选' }),
      ] },
    ],
  },
  {
    name: '工作区 Workspace',
    endpoints: [
      { id: 'ws-list', method: 'GET', path: '/workspaces', desc: '全部工作区列表（含目录）。', fields: [] },
      { id: 'ws-get', method: 'GET', path: '/workspaces/{workspace_id}', desc: '工作区详情。', fields: [F('workspace_id', 'workspace_id', { where: 'path' })] },
      { id: 'ws-files', method: 'GET', path: '/workspaces/{workspace_id}/files', desc: '工作区文件列表。', fields: [F('workspace_id', 'workspace_id', { where: 'path' })] },
      { id: 'ws-upload', method: 'POST', path: '/workspaces/{workspace_id}/files', formData: true, desc: '上传文件到工作区（multipart，可多文件）。', fields: [
        F('workspace_id', 'workspace_id', { where: 'path' }),
        F('files', 'files', { type: 'file', where: 'form', multiple: true }),
        F('sub_dir', 'sub_dir', { where: 'form', placeholder: '子目录，可选' }),
      ] },
    ],
  },
  {
    name: '计划 Plan',
    endpoints: [
      { id: 'plan-get', method: 'GET', path: '/plan/{workspace_id}', desc: '读取计划文件内容与元信息。', fields: [F('workspace_id', 'workspace_id', { where: 'path' })] },
      { id: 'plan-update', method: 'POST', path: '/plan/update', desc: '更新计划内容（用户编辑后保存）。', fields: [
        F('workspace_id', 'workspace_id'),
        F('plan_content', 'plan_content', { type: 'textarea', wide: true }),
      ] },
      { id: 'plan-status', method: 'GET', path: '/plan/{workspace_id}/status', desc: '计划状态查询。', fields: [F('workspace_id', 'workspace_id', { where: 'path' })] },
      { id: 'plan-delete', method: 'DELETE', path: '/plan/{workspace_id}', confirm: true, desc: '删除计划文件。', fields: [F('workspace_id', 'workspace_id', { where: 'path' })] },
    ],
  },
  {
    name: '设置 Settings',
    endpoints: [
      { id: 'settings-get', method: 'GET', path: '/api/settings', desc: '读取全部设置（敏感字段已脱敏）。', fields: [] },
      { id: 'settings-meta', method: 'GET', path: '/api/settings/metadata', desc: '设置元数据（字段说明/分组）。', fields: [] },
      { id: 'settings-patch', method: 'PATCH', path: '/api/settings', desc: '局部更新设置，请求体为任意 JSON 对象。', fields: [F('$', '设置 JSON', { type: 'json', wide: true, placeholder: '{"logging":{"enabled":true}}' })] },
    ],
  },
  {
    name: 'RAG · 知识库',
    endpoints: [
      { id: 'rag-kb-list', method: 'GET', path: '/rag/api/knowledge-bases', desc: '知识库列表。', fields: [] },
      { id: 'rag-kb-create', method: 'POST', path: '/rag/api/knowledge-bases', desc: '创建知识库（同名不可重复）。', fields: [
        F('name', 'name', { placeholder: '知识库名称' }),
        F('description', 'description', { placeholder: '描述，可选' }),
      ] },
      { id: 'rag-kb-update', method: 'PUT', path: '/rag/api/knowledge-bases/{kb_id}', desc: '更新知识库名称/描述（不传则不修改）。', fields: [
        F('kb_id', 'kb_id', { type: 'number', where: 'path' }),
        F('name', 'name', { placeholder: '新名称，可选' }),
        F('description', 'description', { placeholder: '新描述，可选' }),
      ] },
      { id: 'rag-kb-delete', method: 'DELETE', path: '/rag/api/knowledge-bases/{kb_id}', confirm: true, desc: '删除知识库。', fields: [F('kb_id', 'kb_id', { type: 'number', where: 'path' })] },
    ],
  },
  {
    name: 'RAG · 分类',
    endpoints: [
      { id: 'rag-cat-tree', method: 'GET', path: '/rag/api/categories/tree', desc: '分类树。', fields: [] },
      { id: 'rag-cat-create', method: 'POST', path: '/rag/api/categories', desc: '创建分类（可指定父分类）。', fields: [
        F('name', 'name', { placeholder: '分类名称' }),
        F('parent_id', 'parent_id', { type: 'number', placeholder: '父分类 ID，可选' }),
      ] },
      { id: 'rag-cat-update', method: 'PUT', path: '/rag/api/categories/{category_id}', desc: '更新分类名称/父分类。', fields: [
        F('category_id', 'category_id', { type: 'number', where: 'path' }),
        F('name', 'name', { placeholder: '新名称，可选' }),
        F('parent_id', 'parent_id', { type: 'number', placeholder: '新父分类 ID，可选' }),
      ] },
      { id: 'rag-cat-delete', method: 'DELETE', path: '/rag/api/categories/{category_id}', confirm: true, desc: '删除分类，可选处理挂载文档的方式。', fields: [
        F('category_id', 'category_id', { type: 'number', where: 'path' }),
        F('mode', 'mode', { type: 'select', where: 'query', options: [['keep_docs', 'keep_docs（默认）'], ['unbind_docs', 'unbind_docs'], ['recursive', 'recursive']] }),
      ] },
    ],
  },
  {
    name: 'RAG · 文档',
    endpoints: [
      { id: 'rag-doc-upload', method: 'POST', path: '/rag/api/documents/upload', formData: true, desc: '上传文档（自动入库/切块，返回 ingest job）。', fields: [
        F('file', 'file', { type: 'file', where: 'form' }),
        F('category_id', 'category_id', { type: 'number', where: 'form', placeholder: '分类 ID，可选' }),
        F('kb_id', 'kb_id', { type: 'number', where: 'form', placeholder: '知识库 ID，可选' }),
      ] },
      { id: 'rag-doc-list', method: 'GET', path: '/rag/api/documents', desc: '分页查询文档，支持分类/关键字过滤。', fields: [
        F('category_id', 'category_id', { type: 'number', where: 'query' }),
        F('keyword', 'keyword', { where: 'query', placeholder: '关键字，可选' }),
        F('page', 'page', { type: 'number', where: 'query', default: 1 }),
        F('size', 'size', { type: 'number', where: 'query', default: 20 }),
      ] },
      { id: 'rag-doc-get', method: 'GET', path: '/rag/api/documents/{document_id}', desc: '文档详情。', fields: [F('document_id', 'document_id', { type: 'number', where: 'path' })] },
      { id: 'rag-doc-file', method: 'GET', path: '/rag/api/documents/{document_id}/file', desc: '读取文档内容（Office 自动提取文本，二进制返回可读提示）。', fields: [F('document_id', 'document_id', { type: 'number', where: 'path' })] },
      { id: 'rag-doc-update', method: 'PUT', path: '/rag/api/documents/{document_id}', desc: '重命名文档。', fields: [
        F('document_id', 'document_id', { type: 'number', where: 'path' }),
        F('display_name', 'display_name', { placeholder: '新名称' }),
      ] },
      { id: 'rag-doc-delete', method: 'DELETE', path: '/rag/api/documents/{document_id}', confirm: true, desc: '删除文档（异步任务，返回 delete job）。', fields: [F('document_id', 'document_id', { type: 'number', where: 'path' })] },
      { id: 'rag-delete-job', method: 'GET', path: '/rag/api/delete-jobs/{job_id}', desc: '查询删除任务状态。', fields: [F('job_id', 'job_id', { type: 'number', where: 'path' })] },
      { id: 'rag-delete-job-retry', method: 'POST', path: '/rag/api/delete-jobs/{job_id}/retry', desc: '重试删除任务。', fields: [F('job_id', 'job_id', { type: 'number', where: 'path' })] },
      { id: 'rag-doc-bind', method: 'POST', path: '/rag/api/documents/{document_id}/categories/{category_id}', desc: '将文档挂载到分类。', fields: [
        F('document_id', 'document_id', { type: 'number', where: 'path' }),
        F('category_id', 'category_id', { type: 'number', where: 'path' }),
      ] },
      { id: 'rag-doc-unbind', method: 'DELETE', path: '/rag/api/documents/{document_id}/categories/{category_id}', desc: '从分类解除挂载。', fields: [
        F('document_id', 'document_id', { type: 'number', where: 'path' }),
        F('category_id', 'category_id', { type: 'number', where: 'path' }),
      ] },
      { id: 'rag-doc-primary', method: 'PUT', path: '/rag/api/documents/{document_id}/primary-category/{category_id}', desc: '设置主分类。', fields: [
        F('document_id', 'document_id', { type: 'number', where: 'path' }),
        F('category_id', 'category_id', { type: 'number', where: 'path' }),
      ] },
      { id: 'rag-ingest-job', method: 'GET', path: '/rag/api/jobs/{job_id}', desc: '查询入库任务状态。', fields: [F('job_id', 'job_id', { type: 'number', where: 'path' })] },
    ],
  },
  {
    name: 'RAG · 文件（兼容）',
    endpoints: [
      { id: 'rag-files-list', method: 'GET', path: '/rag/api/files', desc: '按相对路径列出 DOCS 下文件。', fields: [F('path', 'path', { where: 'query', placeholder: '相对目录，留空=根' })] },
      { id: 'rag-file-read', method: 'GET', path: '/rag/api/file', desc: '读取文件（已废弃，请改用文档接口）。', fields: [F('path', 'path', { where: 'query', placeholder: '相对文件路径' })] },
      { id: 'rag-file-create', method: 'POST', path: '/rag/api/file', desc: '创建文件或目录。', fields: [
        F('path', 'path', { placeholder: '相对路径，如 raw/a.txt' }),
        F('type', 'type', { type: 'select', options: [['file', 'file'], ['dir', 'dir']] }),
        F('content', 'content', { type: 'textarea', wide: true }),
        F('overwrite', 'overwrite', { type: 'checkbox' }),
      ] },
      { id: 'rag-file-update', method: 'PUT', path: '/rag/api/file', desc: '覆盖文件内容。', fields: [
        F('path', 'path', { placeholder: '相对文件路径' }),
        F('content', 'content', { type: 'textarea', wide: true }),
      ] },
      { id: 'rag-file-delete', method: 'DELETE', path: '/rag/api/file', confirm: true, desc: '删除文件或目录。', fields: [F('path', 'path', { where: 'query', placeholder: '相对路径' })] },
    ],
  },
];

const baseUrlInput = document.getElementById('baseUrl');
const userIdInput = document.getElementById('userId');
const filterInput = document.getElementById('filter');
const groupNav = document.getElementById('groupNav');
const endpointList = document.getElementById('endpointList');

function baseUrl() {
  return baseUrlInput.value.trim().replace(/\/+$/, '');
}

function getUserId() {
  const v = userIdInput.value.trim();
  return v === '' ? '' : v;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderNav() {
  for (const group of GROUPS) {
    const box = el('div', 'nav-group');
    box.appendChild(el('div', 'nav-title', `${group.name}（${group.endpoints.length}）`));
    for (const ep of group.endpoints) {
      const a = el('a', 'nav-item');
      a.href = `#${ep.id}`;
      a.dataset.id = ep.id;
      a.dataset.search = `${ep.method} ${ep.path} ${ep.desc} ${group.name}`.toLowerCase();
      const m = el('span', `method-dot ${ep.method}`, ep.method);
      const p = el('span', 'nav-path', ep.path);
      a.append(m, p);
      a.addEventListener('click', () => focusCard(ep.id));
      box.appendChild(a);
    }
    groupNav.appendChild(box);
  }
}

function buildFieldControl(fld) {
  let control;
  if (fld.type === 'checkbox') {
    const row = el('div', 'checkbox-row');
    control = document.createElement('input');
    control.type = 'checkbox';
    control.checked = !!fld.default;
    row.appendChild(control);
    row.appendChild(el('span', null, fld.default ? '默认勾选' : '默认不勾选'));
    return { control, row };
  }
  if (fld.type === 'select') {
    control = document.createElement('select');
    for (const [value, label] of fld.options) {
      const opt = document.createElement('option');
      opt.value = value;
      opt.textContent = label;
      control.appendChild(opt);
    }
    if (fld.default !== undefined) control.value = String(fld.default);
    return { control };
  }
  if (fld.type === 'textarea' || fld.type === 'json') {
    control = document.createElement('textarea');
    control.rows = fld.type === 'json' ? 4 : 3;
    if (fld.placeholder) control.placeholder = fld.placeholder;
    if (fld.default !== undefined) control.value = String(fld.default);
    return { control };
  }
  if (fld.type === 'file') {
    control = document.createElement('input');
    control.type = 'file';
    if (fld.multiple) control.multiple = true;
    return { control };
  }
  control = document.createElement('input');
  control.type = fld.type === 'number' ? 'number' : 'text';
  if (fld.placeholder) control.placeholder = fld.placeholder;
  if (fld.default !== undefined) control.value = String(fld.default);
  return { control };
}

function renderCard(ep) {
  const card = el('article', 'card');
  card.id = ep.id;
  card.dataset.search = `${ep.method} ${ep.path} ${ep.desc}`.toLowerCase();

  const head = el('div', 'card-head');
  head.appendChild(el('span', `method ${ep.method}`, ep.method));
  head.appendChild(el('code', 'path', ep.path));
  if (ep.sse) head.appendChild(el('span', 'tag sse', 'SSE'));
  if (ep.confirm) head.appendChild(el('span', 'tag danger', '破坏性'));
  if (ep.auth === false) head.appendChild(el('span', 'tag', '公开'));
  card.appendChild(head);

  card.appendChild(el('p', 'desc', ep.desc));

  const fieldsBox = el('div', 'fields');
  for (const fld of ep.fields) {
    const wrap = el('div', `field${fld.wide ? ' wide' : ''}`);
    wrap.appendChild(el('label', null, `${fld.label}${fld.type === 'checkbox' ? '' : '（' + fld.where + '）'}`));
    const { control, row } = buildFieldControl(fld);
    control.dataset.field = fld.key;
    wrap.appendChild(row || control);
    fieldsBox.appendChild(wrap);
  }
  card.appendChild(fieldsBox);

  const actions = el('div', 'actions');
  const sendBtn = el('button', null, '发送');
  actions.appendChild(sendBtn);
  let stopBtn = null;
  if (ep.sse) {
    stopBtn = el('button', 'stop', '停止');
    stopBtn.disabled = true;
    actions.appendChild(stopBtn);
  }
  actions.appendChild(el('span', 'req-preview'));
  card.appendChild(actions);

  if (ep.sse) {
    card.appendChild(el('div', 'sse-log'));
  } else {
    card.appendChild(el('div', 'resp'));
  }

  sendBtn.addEventListener('click', () => {
    if (ep.sse) startSSE(ep, card, sendBtn, stopBtn);
    else sendRequest(ep, card, sendBtn);
  });
  if (stopBtn) {
    stopBtn.addEventListener('click', () => stopSSE(card));
  }

  return card;
}

function renderCards() {
  for (const group of GROUPS) {
    const section = el('section', 'group-section');
    section.dataset.group = group.name;
    section.appendChild(el('h2', null, group.name));
    for (const ep of group.endpoints) {
      section.appendChild(renderCard(ep));
    }
    endpointList.appendChild(section);
  }
}

function collectValues(ep, card) {
  const values = {};
  for (const fld of ep.fields) {
    const control = card.querySelector(`[data-field="${fld.key}"]`);
    if (!control) continue;
    let v;
    if (fld.type === 'checkbox') v = control.checked;
    else if (fld.type === 'file') v = Array.from(control.files || []);
    else v = control.value;
    if (fld.type === 'json' && typeof v === 'string' && v.trim() !== '') {
      try {
        v = JSON.parse(v);
      } catch (err) {
        return { ok: false, error: `字段 ${fld.key} 不是合法 JSON` };
      }
    }
    if (fld.type === 'number' && v !== '' && v !== undefined && v !== null) v = Number(v);
    values[fld.key] = v;
  }
  return { ok: true, values };
}

function buildUrl(ep, values) {
  let url = ep.path;
  const qs = [];
  for (const fld of ep.fields) {
    if (fld.where === 'path') {
      const v = values[fld.key];
      url = url.replace(`{${fld.key}}`, encodeURIComponent(v === undefined || v === null ? '' : String(v)));
    } else if (fld.where === 'query') {
      const v = values[fld.key];
      if (v === undefined || v === null || v === '') continue;
      qs.push(`${encodeURIComponent(fld.key)}=${encodeURIComponent(String(v))}`);
    }
  }
  return qs.length ? `${url}?${qs.join('&')}` : url;
}

function buildPayload(ep, values) {
  const headers = {};
  const userId = getUserId();
  if (userId) headers['X-User-ID'] = userId;

  if (ep.formData) {
    const fd = new FormData();
    for (const fld of ep.fields) {
      const v = values[fld.key];
      if (v === undefined || v === null || v === '') continue;
      if (fld.type === 'file') {
        for (const file of v) fd.append(fld.key, file);
      } else if (fld.where === 'form') {
        fd.append(fld.key, String(v));
      }
    }
    const lines = [];
    for (const pair of fd.entries()) {
      const item = pair[1];
      lines.push(`${pair[0]}: ${item instanceof File ? `${item.name} (${item.size} B)` : item}`);
    }
    return { headers, body: fd, bodyText: lines.join('\n') };
  }

  if (ep.method === 'GET' || ep.method === 'DELETE') {
    return { headers, body: undefined, bodyText: '' };
  }

  const body = {};
  for (const fld of ep.fields) {
    if (fld.where !== 'body') continue;
    const v = values[fld.key];
    if (fld.key === '$') {
      return { headers, body: v, bodyText: JSON.stringify(v, null, 2) };
    }
    if (v === undefined) continue;
    if (fld.type === 'checkbox') {
      body[fld.key] = !!v;
      continue;
    }
    if (v === '' || v === null) continue;
    body[fld.key] = v;
  }
  const bodyText = Object.keys(body).length ? JSON.stringify(body, null, 2) : '';
  headers['Content-Type'] = 'application/json';
  return { headers, body: bodyText || undefined, bodyText };
}

function prettyBody(text) {
  if (!text) return '(空响应体)';
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch (err) {
    return text;
  }
}

function setPreview(card, text) {
  const prev = card.querySelector('.req-preview');
  prev.textContent = text;
}

function showError(card, message) {
  const resp = card.querySelector('.resp');
  if (!resp) return;
  resp.classList.add('visible');
  const head = resp.querySelector('.resp-head') || el('div', 'resp-head');
  const body = resp.querySelector('.resp-body') || el('div', 'resp-body');
  if (!resp.querySelector('.resp-head')) {
    head.appendChild(el('span', 'status-code err', '✗'));
    resp.appendChild(head);
    resp.appendChild(body);
  }
  body.textContent = message;
}

function clearError(card) {
  const resp = card.querySelector('.resp');
  if (resp) resp.classList.remove('visible');
}

function renderResponse(card, info) {
  const resp = card.querySelector('.resp');
  resp.classList.add('visible');
  resp.innerHTML = '';

  const head = el('div', 'resp-head');
  const codeClass = info.status < 300 ? 'ok' : info.status < 500 ? 'warn' : 'err';
  head.appendChild(el('span', `status-code ${codeClass}`, `${info.status} ${info.statusText}`));
  if (info.requestId) head.appendChild(el('span', null, `X-Request-Id: ${info.requestId}`));
  head.appendChild(el('span', null, `${info.ms} ms`));
  resp.appendChild(head);

  const body = el('div', 'resp-body', prettyBody(info.bodyText));
  resp.appendChild(body);
}

async function sendRequest(ep, card, btn) {
  if (ep.confirm && !confirm(`确认执行 ${ep.method} ${ep.path}？`)) return;

  const collected = collectValues(ep, card);
  if (!collected.ok) {
    showError(card, collected.error);
    return;
  }
  clearError(card);

  const url = buildUrl(ep, collected.values);
  const { headers, body, bodyText } = buildPayload(ep, collected.values);
  const fullUrl = baseUrl() + url;
  const previewLines = [`${ep.method} ${fullUrl}`];
  for (const [k, v] of Object.entries(headers)) previewLines.push(`${k}: ${v}`);
  if (bodyText) previewLines.push(bodyText);
  setPreview(card, previewLines.join('\n'));

  btn.disabled = true;
  const start = performance.now();
  try {
    const response = await fetch(fullUrl, { method: ep.method, headers, body });
    const text = await response.text();
    renderResponse(card, {
      status: response.status,
      statusText: response.statusText,
      bodyText: text,
      requestId: response.headers.get('X-Request-Id') || '',
      ms: Math.round(performance.now() - start),
    });
  } catch (err) {
    showError(card, `请求失败：${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

function sseLogLine(card, className, text) {
  const log = card.querySelector('.sse-log');
  log.appendChild(el('div', `sse-line ${className}`, text));
  log.scrollTop = log.scrollHeight;
}

function stopSSE(card) {
  const controller = card.__sseController;
  if (controller) controller.abort();
}

async function startSSE(ep, card, sendBtn, stopBtn) {
  if (ep.confirm && !confirm(`确认执行 ${ep.method} ${ep.path}？`)) return;

  const collected = collectValues(ep, card);
  if (!collected.ok) {
    sseLogLine(card, 'sse-error', collected.error);
    return;
  }

  const url = buildUrl(ep, collected.values);
  const { headers, body, bodyText } = buildPayload(ep, collected.values);
  const fullUrl = baseUrl() + url;
  const previewLines = [`${ep.method} ${fullUrl}`];
  for (const [k, v] of Object.entries(headers)) previewLines.push(`${k}: ${v}`);
  setPreview(card, previewLines.join('\n'));

  const log = card.querySelector('.sse-log');
  log.textContent = '';
  log.appendChild(el('div', 'sse-line', `连接 ${fullUrl} …`));

  const controller = new AbortController();
  card.__sseController = controller;
  sendBtn.disabled = true;
  stopBtn.disabled = false;

  let buffer = '';
  const decoder = new TextDecoder();
  let frameCount = 0;
  let heartbeatCount = 0;

  try {
    const response = await fetch(fullUrl, { method: ep.method, headers, signal: controller.signal });
    if (!response.ok || !response.body) {
      const text = await response.text();
      sseLogLine(card, 'sse-error', `HTTP ${response.status} ${response.statusText}\n${text}`);
      return;
    }

    const reader = response.body.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        handleSSEFrame(card, frame, () => frameCount++, () => heartbeatCount++);
      }
    }
    sseLogLine(card, 'sse-done', '— 流已结束 —');
  } catch (err) {
    if (err.name === 'AbortError') {
      sseLogLine(card, 'sse-heartbeat', '— 已手动停止 —');
    } else {
      sseLogLine(card, 'sse-error', `连接失败：${err.message}`);
    }
  } finally {
    sendBtn.disabled = false;
    stopBtn.disabled = true;
    card.__sseController = null;
  }
}

function handleSSEFrame(card, frame, onEvent, onHeartbeat) {
  const lines = frame.split('\n');
  let id = '';
  let data = '';
  let isHeartbeat = false;
  for (const line of lines) {
    if (line.startsWith(':')) {
      isHeartbeat = true;
    } else if (line.startsWith('id:')) {
      id = line.slice(3).trim();
    } else if (line.startsWith('data:')) {
      data += line.slice(5).trim();
    }
  }
  if (isHeartbeat && !data) {
    onHeartbeat();
    sseLogLine(card, 'sse-heartbeat', `: heartbeat #${arguments[3] || ''}`.trim());
    return;
  }
  if (!data) return;
  onEvent();
  let payload;
  try {
    payload = JSON.parse(data);
  } catch (err) {
    payload = data;
  }
  const type = payload && typeof payload === 'object' ? payload.type || 'message' : 'message';
  const seq = id || (payload && typeof payload === 'object' ? payload.seq : '');
  const pretty = typeof payload === 'object' ? JSON.stringify(payload, null, 2) : payload;
  const cls = type === 'error' ? 'sse-error' : type === 'done' || type === 'stream_completed' ? 'sse-done' : '';
  sseLogLine(card, cls, `[#${seq || '?'}] type=${type}\n${pretty}`);
}

function focusCard(id) {
  const card = document.getElementById(id);
  if (!card) return;
  card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  card.classList.add('flash');
  const nav = groupNav.querySelector(`[data-id="${id}"]`);
  if (nav) nav.classList.add('flash');
  setTimeout(() => {
    card.classList.remove('flash');
    if (nav) nav.classList.remove('flash');
  }, 1200);
}

filterInput.addEventListener('input', () => {
  const q = filterInput.value.trim().toLowerCase();
  for (const group of GROUPS) {
    for (const ep of group.endpoints) {
      const card = document.getElementById(ep.id);
      const nav = groupNav.querySelector(`[data-id="${ep.id}"]`);
      const hit = !q || ep.path.toLowerCase().includes(q) || ep.desc.toLowerCase().includes(q) || ep.method.toLowerCase().includes(q);
      if (card) card.style.display = hit ? '' : 'none';
      if (nav) nav.style.display = hit ? '' : 'none';
    }
  }
});

renderNav();
renderCards();
