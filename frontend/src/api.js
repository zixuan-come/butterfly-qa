const DEFAULT_API_BASE_URL = import.meta.env.DEV
  ? 'http://127.0.0.1:8000/api/v1'
  : '/api/v1'

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL
).replace(/\/$/, '')

export class ApiClientError extends Error {
  constructor(message, { code = 'REQUEST_FAILED', status = 0, data = null, requestId = null } = {}) {
    super(message)
    this.name = 'ApiClientError'
    this.code = code
    this.status = status
    this.data = data
    this.requestId = requestId
  }
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers)
  const isFormData = options.body instanceof FormData
  if (options.body && !isFormData && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  let response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers })
  } catch (error) {
    throw new ApiClientError('无法连接 Butterfly Agent API，请确认后端服务已启动', {
      code: 'NETWORK_ERROR',
    })
  }

  const payload = await response.json().catch(() => null)
  if (!response.ok || !payload || payload.code !== 'OK') {
    throw new ApiClientError(payload?.message || `请求失败（HTTP ${response.status}）`, {
      code: payload?.code,
      status: response.status,
      data: payload?.data,
      requestId: payload?.request_id || response.headers.get('X-Request-ID'),
    })
  }
  return payload.data
}

export function listProjects() {
  return request('/projects')
}

export function createProject(payload) {
  return request('/projects', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getProject(projectId) {
  return request(`/projects/${encodeURIComponent(projectId)}`)
}

export function updateProject(projectId, payload) {
  return request(`/projects/${encodeURIComponent(projectId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  })
}

export function deleteProject(projectId) {
  return request(`/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
  })
}
export function listFeatureModules(projectId) {
  return request(`/projects/${encodeURIComponent(projectId)}/modules`)
}

export function createFeatureModule(projectId, payload) {
  return request(`/projects/${encodeURIComponent(projectId)}/modules`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateFeatureModule(projectId, moduleId, payload) {
  return request(`/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  })
}

export function deleteFeatureModule(projectId, moduleId) {
  return request(`/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}`, {
    method: "DELETE",
  })
}
function withModule(path, moduleId) {
  if (!moduleId) return path
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}module_id=${encodeURIComponent(moduleId)}`
}

export function getWorkflow(projectId, moduleId = null) {
  return request(withModule(
    `/projects/${encodeURIComponent(projectId)}/workflow`,
    moduleId,
  ))
}

export function uploadProjectInput(
  projectId,
  file,
  { category, importedBy, inputId, moduleId } = {},
) {
  const body = new FormData()
  body.append('file', file)
  body.append('category', category || 'requirement')
  body.append('imported_by', importedBy)
  if (inputId) body.append('input_id', inputId)
  return request(withModule(
    `/projects/${encodeURIComponent(projectId)}/inputs`,
    moduleId,
  ), {
    method: 'POST',
    body,
  })
}

export function getProjectInputPreview(projectId, inputId, moduleId = null) {
  return request(withModule(
    `/projects/${encodeURIComponent(projectId)}/inputs/${encodeURIComponent(inputId)}`,
    moduleId,
  ))
}

export function projectInputContentUrl(projectId, inputId, moduleId = null) {
  const path = `/projects/${encodeURIComponent(projectId)}/inputs/${encodeURIComponent(inputId)}/content`
  return `${API_BASE_URL}${withModule(path, moduleId)}`
}

export function runWorkflow(projectId, model = null, moduleId = null) {
  return request(withModule(
    `/projects/${encodeURIComponent(projectId)}/runs`,
    moduleId,
  ), {
    method: 'POST',
    body: JSON.stringify(model ? { model } : {}),
  })
}

export function getActiveArtifact(projectId, artifactType, moduleId = null) {
  return request(withModule(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactType)}`,
    moduleId,
  ))
}

export function generateConfirmationChecklist(projectId, moduleId = null) {
  return request(withModule(
    `/projects/${encodeURIComponent(projectId)}/confirmation-checklists`,
    moduleId,
  ), {
    method: 'POST',
  })
}

export function submitApproval(projectId, payload, moduleId = null) {
  return request(withModule(
    `/projects/${encodeURIComponent(projectId)}/approvals`,
    moduleId,
  ), {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function submitExecution(projectId, payload, moduleId = null) {
  return request(withModule(
    `/projects/${encodeURIComponent(projectId)}/executions`,
    moduleId,
  ), {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function uploadEvidence(
  projectId,
  file,
  { evidenceType, description, evidenceId, moduleId } = {},
) {
  const body = new FormData()
  body.append('file', file)
  body.append('evidence_type', evidenceType)
  body.append('description', description)
  if (evidenceId) body.append('evidence_id', evidenceId)
  return request(withModule(
    `/projects/${encodeURIComponent(projectId)}/evidence`,
    moduleId,
  ), {
    method: 'POST',
    body,
  })
}
