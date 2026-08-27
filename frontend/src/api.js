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
    throw new ApiClientError('无法连接 Butterfly QA API，请确认后端服务已启动', {
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

export function getWorkflow(projectId) {
  return request(`/projects/${encodeURIComponent(projectId)}/workflow`)
}

export function uploadProjectInput(projectId, file, { category, importedBy, inputId } = {}) {
  const body = new FormData()
  body.append('file', file)
  body.append('category', category || 'requirement')
  body.append('imported_by', importedBy)
  if (inputId) body.append('input_id', inputId)
  return request(`/projects/${encodeURIComponent(projectId)}/inputs`, {
    method: 'POST',
    body,
  })
}

export function getProjectInputPreview(projectId, inputId) {
  return request(
    `/projects/${encodeURIComponent(projectId)}/inputs/${encodeURIComponent(inputId)}`,
  )
}

export function projectInputContentUrl(projectId, inputId) {
  return `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/inputs/${encodeURIComponent(inputId)}/content`
}

export function runWorkflow(projectId, model = null) {
  return request(`/projects/${encodeURIComponent(projectId)}/runs`, {
    method: 'POST',
    body: JSON.stringify(model ? { model } : {}),
  })
}

export function getActiveArtifact(projectId, artifactType) {
  return request(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactType)}`,
  )
}

export function submitApproval(projectId, payload) {
  return request(`/projects/${encodeURIComponent(projectId)}/approvals`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function submitExecution(projectId, payload) {
  return request(`/projects/${encodeURIComponent(projectId)}/executions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function uploadEvidence(projectId, file, { evidenceType, description, evidenceId } = {}) {
  const body = new FormData()
  body.append('file', file)
  body.append('evidence_type', evidenceType)
  body.append('description', description)
  if (evidenceId) body.append('evidence_id', evidenceId)
  return request(`/projects/${encodeURIComponent(projectId)}/evidence`, {
    method: 'POST',
    body,
  })
}
