<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  AlertTriangle,
  ArrowRight,
  Bell,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  ClipboardCheck,
  Clock3,
  Download,
  FileCheck2,
  FileSearch,
  FileText,
  Filter,
  Gauge,
  History,
  ListChecks,
  LoaderCircle,
  MapPin,
  Menu,
  Moon,
  Paperclip,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Sun,
  TestTube2,
  Trash2,
  Upload,
  UserCheck,
  X,
  XCircle,
} from 'lucide-vue-next'
import { stages as stageDefinitions } from './data'
import {
  createFeatureModule,
  createProject,
  deleteFeatureModule,
  deleteProject,
  generateConfirmationChecklist,
  getActiveArtifact,
  getProject,
  getProjectInputPreview,
  getWorkflow,
  listFeatureModules,
  listProjects,
  projectInputContentUrl,
  runWorkflow,
  submitApproval,
  submitExecution,
  updateFeatureModule,
  updateProject,
  uploadEvidence,
  uploadProjectInput,
} from './api'

const iconByStage = {
  'requirement-review': FileSearch,
  'case-design': ListChecks,
  'case-review': ClipboardCheck,
  execution: TestTube2,
  report: FileCheck2,
}

const stateStage = {
  requirement_received: 'requirement-review',
  requirement_reviewing: 'requirement-review',
  waiting_product_revision: 'requirement-review',
  requirement_analyzing: 'case-design',
  testcase_designing: 'case-design',
  testcase_reviewing: 'case-review',
  waiting_case_revision: 'case-review',
  waiting_testcase_approval: 'case-review',
  waiting_manual_execution: 'execution',
  generating_report: 'report',
  waiting_report_approval: 'report',
  completed: 'report',
}

const stateLabels = {
  requirement_received: '需求已接收',
  requirement_reviewing: 'AI 评审中',
  waiting_product_revision: '等待产品修改',
  requirement_analyzing: '需求分析中',
  testcase_designing: '用例设计中',
  testcase_reviewing: '用例评审中',
  waiting_case_revision: '等待用例修订',
  waiting_testcase_approval: '等待人工审批',
  waiting_manual_execution: '等待测试执行',
  generating_report: '报告生成中',
  waiting_report_approval: '等待报告审批',
  manual_intervention_required: '需要人工介入',
  completed: '已完成',
  cancelled: '已取消',
}

const stateStep = {
  requirement_received: 0,
  requirement_reviewing: 1,
  waiting_product_revision: 2,
  requirement_analyzing: 0,
  testcase_designing: 2,
  testcase_reviewing: 0,
  waiting_case_revision: 2,
  waiting_testcase_approval: 4,
  waiting_manual_execution: 0,
  generating_report: 2,
  waiting_report_approval: 3,
  completed: 4,
}

const humanWaitingStates = new Set([
  'waiting_product_revision',
  'waiting_case_revision',
  'waiting_testcase_approval',
  'waiting_manual_execution',
  'waiting_report_approval',
  'manual_intervention_required',
])

const activeStageId = ref('requirement-review')
const currentSteps = reactive({
  'requirement-review': 0,
  'case-design': 0,
  'case-review': 0,
  execution: 0,
  report: 0,
})
const theme = ref(localStorage.getItem('butterfly-theme') || 'light')
const findingFilter = ref('全部')
const selectedFindingId = ref('F-001')
const auditOpen = ref(false)
const sidebarOpen = ref(false)
const executionCases = ref([])
const environmentReady = ref(false)
const executionEnvironment = ref('QA-02 · v2.8.14-rc3')
const reportApproved = ref(false)
const reviewApproved = ref(false)
const approvalBusy = ref(false)
const executionBusy = ref(false)
const evidenceBusy = ref(false)
const confirmationChecklistBusy = ref(false)
const confirmationChecklistMarkdown = ref('')
const evidenceFileInput = ref(null)
const evidenceTarget = ref(null)
const toast = ref('')
const projects = ref([])
const currentProject = ref(null)
const modules = ref([])
const currentModule = ref(null)
let contextRequestId = 0
const workflow = ref(null)
const requirementInputPreview = ref(null)
const artifacts = reactive({
  requirement_review: null,
  product_confirmation_checklist: null,
  requirement_analysis: null,
  test_design: null,
  testcase_review: null,
  test_report: null,
})
const projectMenuOpen = ref(false)
const createProjectOpen = ref(false)
const createModuleOpen = ref(false)
const manageDialog = reactive({
  open: false,
  kind: '',
  projectId: '',
  moduleId: '',
  name: '',
  busy: false,
})
const deleteDialog = reactive({
  open: false,
  kind: '',
  projectId: '',
  moduleId: '',
  title: '',
  description: '',
  busy: false,
})
const projectsLoading = ref(false)
const creatingProject = ref(false)
const creatingModule = ref(false)
const uploadingRequirement = ref(false)
const runningWorkflow = ref(false)
const requirementFileInput = ref(null)
const syncLabel = ref('正在连接')
const newProject = reactive({
  project_id: '',
  name: '',
  created_by: 'admin',
})
const newModule = reactive({
  module_id: '',
  name: '',
  created_by: 'admin',
})
const approvalDialog = reactive({
  open: false,
  approvalType: '',
  decision: 'changes_requested',
  title: '',
  comment: '',
})
let toastTimer

const workflowStageId = computed(() => stateStage[workflow.value?.state] || 'requirement-review')
const workflowStageIndex = computed(() => (
  stageDefinitions.findIndex((stage) => stage.id === workflowStageId.value)
))
const stages = computed(() => stageDefinitions.map((stage, index) => {
  const issueCount = stage.id === 'requirement-review'
    ? (artifacts.requirement_review?.issues || []).length
    : stage.id === 'case-review'
      ? (artifacts.testcase_review?.issues || []).length
      : 0
  if (!workflow.value) {
    return { ...stage, status: index === 0 ? '尚未开始' : '待开始', tone: 'idle', issueCount: 0 }
  }
  if (workflow.value.state === 'completed') {
    return { ...stage, status: '已完成', tone: 'completed', issueCount: 0 }
  }
  if (index < workflowStageIndex.value) {
    return { ...stage, status: '已完成', tone: 'completed', issueCount: 0 }
  }
  if (index === workflowStageIndex.value) {
    return {
      ...stage,
      status: stateLabels[workflow.value.state] || workflow.value.state,
      tone: humanWaitingStates.has(workflow.value.state) ? 'waiting' : 'active',
      issueCount,
    }
  }
  return { ...stage, status: '待开始', tone: 'idle', issueCount: 0 }
}))
const activeStage = computed(() => stages.value.find((stage) => stage.id === activeStageId.value))
const currentProjectName = computed(() => currentProject.value?.name || '尚未选择项目')
const currentContextName = computed(() => (
  currentModule.value?.name || currentProjectName.value
))
const currentContextId = computed(() => (
  currentModule.value?.module_id || 'V1 兼容流程'
))
const currentStateLabel = computed(() => (
  stateLabels[workflow.value?.state] || (workflow.value ? workflow.value.state : '新建或选择项目')
))
const workflowProgress = computed(() => {
  if (!workflow.value) return 0
  if (workflow.value.state === 'completed') return 100
  return Math.min(95, workflowStageIndex.value * 20 + 10)
})
const requirementDocument = computed(() => {
  const latest = requirementInputPreview.value?.input
  return {
    name: latest?.original_name || '尚未导入产品需求',
    source: '产品需求文档',
    updatedAt: latest ? formatDateTime(latest.imported_at) : '等待导入',
  }
})
const requirementSourceLines = computed(() => {
  if (requirementInputPreview.value?.preview_kind !== 'text') return []
  return String(requirementInputPreview.value.content || '').split(/\r?\n/)
})

function parseLocationRanges(location) {
  return [...String(location || '').matchAll(/第\s*(\d+)(?:\s*[-–—至~]\s*(\d+))?\s*行/g)]
    .map((match) => {
      const start = Number(match[1])
      const end = Number(match[2] || match[1])
      return { start: Math.min(start, end), end: Math.max(start, end) }
    })
}

const findings = computed(() => (artifacts.requirement_review?.issues || []).map((issue) => ({
  id: issue.issue_id,
  severity: severityLabel(issue.severity),
  category: issue.issue_type,
  title: issue.description,
  detail: issue.impact,
  suggestion: issue.suggestion,
  location: issue.location,
  lineRanges: parseLocationRanges(issue.location),
  status: issue.needs_product_confirmation ? '待产品确认' : '待处理',
})))
const selectedRequirementLines = computed(() => {
  const selected = findings.value.find((item) => item.id === selectedFindingId.value)
  const lines = new Set()
  ;(selected?.lineRanges || []).forEach(({ start, end }) => {
    for (let line = start; line <= Math.min(end, start + 500); line += 1) {
      lines.add(line)
    }
  })
  return lines
})
const requirements = computed(() => (artifacts.requirement_analysis?.requirements || []).map((item) => {
  const points = (artifacts.test_design?.test_points || [])
    .filter((point) => point.requirement_refs.includes(item.requirement_id))
  const cases = (artifacts.test_design?.test_cases || [])
    .filter((testCase) => testCase.requirement_refs.includes(item.requirement_id))
  return {
    id: item.requirement_id,
    title: item.title,
    points: points.length,
    cases: cases.length,
    coverage: points.length ? 100 : 0,
    risk: severityLabel(points.some((point) => point.risk === 'high') ? 'high' : 'medium'),
  }
}))
const testCases = computed(() => (artifacts.test_design?.test_cases || []).map((item) => ({
  id: item.case_id,
  title: item.title,
  priority: item.priority,
  type: item.tags?.[0] || '功能',
  requirement: item.requirement_refs.join('、'),
  status: item.steps?.length ? '结构完整' : '结构不完整',
})))
const reviewIssues = computed(() => (artifacts.testcase_review?.issues || []).map((issue) => ({
  id: issue.issue_id,
  severity: severityLabel(issue.severity),
  caseId: issue.case_id,
  dimension: issue.issue_type,
  title: issue.description,
  status: '待修订',
})))
const designStats = computed(() => {
  const points = artifacts.test_design?.test_points || []
  const cases = artifacts.test_design?.test_cases || []
  return {
    requirements: requirements.value.length,
    points: points.length,
    highRiskPoints: points.filter((item) => item.risk === 'high').length,
    cases: cases.length,
    p0Cases: cases.filter((item) => item.priority === 'P0').length,
    coverage: requirements.value.length
      ? Math.round((requirements.value.filter((item) => item.points > 0).length / requirements.value.length) * 100)
      : 0,
  }
})
const testcaseHealthScore = computed(() => {
  const deductions = reviewIssues.value.reduce((total, issue) => {
    return total + ({ 高: 12, 中: 6, 低: 2 }[issue.severity] || 0)
  }, 0)
  return Math.max(0, 100 - deductions)
})
const reportData = computed(() => artifacts.test_report)
const auditEvents = computed(() => [...(workflow.value?.transition_history || [])]
  .reverse()
  .map((event) => ({
    time: formatDateTime(event.occurred_at),
    actor: event.triggered_by,
    action: `${stateLabels[event.from_state] || event.from_state} → ${stateLabels[event.to_state] || event.to_state}`,
    detail: event.reason,
  })))
const canSubmitExecution = computed(() => (
  workflow.value?.state === 'waiting_manual_execution'
  && executionCases.value.length > 0
  && executionCases.value.every((item) => item.result !== '未执行' && item.actualResult.trim())
))
const filteredFindings = computed(() => {
  if (findingFilter.value === '全部') return findings.value
  return findings.value.filter((finding) => finding.severity === findingFilter.value)
})
const findingCounts = computed(() => ({
  全部: findings.value.length,
  高: findings.value.filter((item) => item.severity === '高').length,
  中: findings.value.filter((item) => item.severity === '中').length,
}))
const executionStats = computed(() => {
  const total = executionCases.value.length
  const count = (result) => executionCases.value.filter((item) => item.result === result).length
  const passed = count('通过')
  return {
    total,
    passed,
    failed: count('失败'),
    blocked: count('阻塞'),
    pending: count('未执行'),
    rate: total ? Math.round((passed / total) * 100) : 0,
  }
})

watch(theme, (value) => {
  document.documentElement.dataset.theme = value
  localStorage.setItem('butterfly-theme', value)
})

onMounted(async () => {
  document.documentElement.dataset.theme = theme.value
  await loadProjects()
})

function switchStage(id) {
  activeStageId.value = id
  sidebarOpen.value = false
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function setCurrentStep(index) {
  currentSteps[activeStageId.value] = index
}

async function loadProjects(preferredProjectId = null) {
  projectsLoading.value = true
  syncLabel.value = '正在同步'
  try {
    const result = await listProjects()
    projects.value = result.items
    const remembered = localStorage.getItem('butterfly-project-id')
    const projectId = (preferredProjectId && projects.value.some(
      (item) => item.project_id === preferredProjectId,
    ) ? preferredProjectId : null)
      || (remembered && projects.value.some((item) => item.project_id === remembered) ? remembered : null)
      || projects.value[0]?.project_id
    if (projectId) {
      await selectProject(projectId)
    } else {
      currentProject.value = null
      currentModule.value = null
      modules.value = []
      workflow.value = null
      resetArtifacts()
      executionCases.value = []
      syncLabel.value = '暂无项目'
    }
  } catch (error) {
    syncLabel.value = '连接失败'
    showToast(error.message)
  } finally {
    projectsLoading.value = false
  }
}

async function selectProject(projectId, preferredModuleId = undefined) {
  const requestId = ++contextRequestId
  projectMenuOpen.value = false
  syncLabel.value = '正在同步'
  try {
    const [projectData, moduleData] = await Promise.all([
      getProject(projectId),
      listFeatureModules(projectId),
    ])
    const storageKey = `butterfly-module-id:${projectId}`
    const rememberedModuleId = localStorage.getItem(storageKey)
    const requestedModuleId = preferredModuleId === undefined
      ? rememberedModuleId
      : preferredModuleId
    const nextModule = moduleData.items.find(
      (item) => item.module_id === requestedModuleId,
    ) || (
      preferredModuleId === undefined && !rememberedModuleId
        ? moduleData.items[0] || null
        : null
    )
    const workflowData = await getWorkflow(
      projectId,
      nextModule?.module_id,
    )
    if (requestId !== contextRequestId) return
    currentProject.value = projectData
    modules.value = moduleData.items
    currentModule.value = nextModule
    await applyWorkflowContext(workflowData, requestId)
    if (requestId !== contextRequestId) return
    localStorage.setItem('butterfly-project-id', projectId)
    if (currentModule.value) {
      localStorage.setItem(storageKey, currentModule.value.module_id)
    }
  } catch (error) {
    if (requestId !== contextRequestId) return
    syncLabel.value = '同步失败'
    showToast(error.message)
  }
}
async function selectModule(moduleId) {
  if (!currentProject.value) return
  const requestId = ++contextRequestId
  projectMenuOpen.value = false
  syncLabel.value = '正在同步'
  try {
    const nextModule = modules.value.find(
      (item) => item.module_id === moduleId,
    ) || null
    const workflowData = await getWorkflow(
      currentProject.value.project_id,
      nextModule?.module_id,
    )
    if (requestId !== contextRequestId) return
    currentModule.value = nextModule
    await applyWorkflowContext(workflowData, requestId)
    if (requestId !== contextRequestId) return
    const storageKey = `butterfly-module-id:${currentProject.value.project_id}`
    if (currentModule.value) {
      localStorage.setItem(storageKey, currentModule.value.module_id)
    } else {
      localStorage.removeItem(storageKey)
    }
  } catch (error) {
    if (requestId !== contextRequestId) return
    syncLabel.value = '同步失败'
    showToast(error.message)
  }
}
async function applyWorkflowContext(workflowData, requestId = contextRequestId) {
  if (requestId !== contextRequestId) return
  workflow.value = workflowData
  await Promise.all([
    loadActiveArtifacts(workflowData, requestId),
    loadRequirementPreview(workflowData, requestId),
  ])
  if (requestId !== contextRequestId) return
  activeStageId.value = stateStage[workflowData.state] || activeStageId.value
  currentSteps[activeStageId.value] = stateStep[workflowData.state] || 0
  syncLabel.value = '已同步'
}
async function refreshCurrentProject() {
  if (!currentProject.value) return
  await selectProject(
    currentProject.value.project_id,
    currentModule.value?.module_id || null,
  )
}

async function loadActiveArtifacts(workflowData, requestId) {
  if (requestId !== contextRequestId) return
  resetArtifacts()
  const artifactTypes = Object.keys(artifacts)
    .filter((artifactType) => workflowData.active_artifacts?.[artifactType])
  await Promise.all(artifactTypes.map(async (artifactType) => {
    try {
      const result = await getActiveArtifact(
        workflowData.project_id,
        artifactType,
        currentModule.value?.module_id,
      )
      if (requestId !== contextRequestId) return
      artifacts[artifactType] = result.content
      if (artifactType === 'product_confirmation_checklist') {
        confirmationChecklistMarkdown.value = result.markdown || ''
      }
    } catch (error) {
      if (requestId !== contextRequestId) return
      artifacts[artifactType] = null
    }
  }))
  if (requestId !== contextRequestId) return
  hydrateExecutionCases()
}
async function loadRequirementPreview(workflowData, requestId) {
  if (requestId !== contextRequestId) return
  requirementInputPreview.value = null
  const requirementInputs = (workflowData.input_files || [])
    .filter((item) => item.category === 'requirement')
  const latest = requirementInputs[requirementInputs.length - 1]
  if (!latest) return
  try {
    const preview = await getProjectInputPreview(
      workflowData.project_id,
      latest.input_id,
      currentModule.value?.module_id,
    )
    if (requestId !== contextRequestId) return
    requirementInputPreview.value = {
      ...preview,
      resolvedContentUrl: projectInputContentUrl(
        workflowData.project_id,
        latest.input_id,
        currentModule.value?.module_id,
      ),
    }
  } catch (error) {
    if (requestId !== contextRequestId) return
    requirementInputPreview.value = null
  }
}
function resetArtifacts() {
  confirmationChecklistMarkdown.value = ''
  Object.keys(artifacts).forEach((artifactType) => {
    artifacts[artifactType] = null
  })
  requirementInputPreview.value = null
}

function hydrateExecutionCases() {
  const previous = new Map(executionCases.value.map((item) => [item.id, item]))
  executionCases.value = (artifacts.test_design?.test_cases || []).map((item) => {
    const existing = previous.get(item.case_id)
    return {
      id: item.case_id,
      version: item.version,
      title: item.title,
      priority: item.priority,
      result: existing?.result || '未执行',
      executor: existing?.executor || newProject.created_by,
      evidence: existing?.evidence || [],
      actualResult: existing?.actualResult || '',
    }
  })
}

function openCreateProject() {
  projectMenuOpen.value = false
  newProject.project_id = ''
  newProject.name = ''
  createProjectOpen.value = true
}

async function submitNewProject() {
  creatingProject.value = true
  try {
    const created = await createProject({ ...newProject })
    createProjectOpen.value = false
    await loadProjects(created.project_id)
    openCreateModule()
    showToast('项目已创建，请添加第一个功能模块')
  } catch (error) {
    showToast(error.message)
  } finally {
    creatingProject.value = false
  }
}

function openCreateModule() {
  if (!currentProject.value) {
    openCreateProject()
    return
  }
  projectMenuOpen.value = false
  newModule.module_id = ''
  newModule.name = ''
  newModule.created_by = newProject.created_by
  createModuleOpen.value = true
}

async function submitNewModule() {
  if (!currentProject.value) return
  creatingModule.value = true
  try {
    const created = await createFeatureModule(
      currentProject.value.project_id,
      { ...newModule },
    )
    createModuleOpen.value = false
    await selectProject(
      currentProject.value.project_id,
      created.module_id,
    )
    showToast('功能模块创建成功')
  } catch (error) {
    showToast(error.message)
  } finally {
    creatingModule.value = false
  }
}

function chooseRequirementFile() {
  if (!currentProject.value) {
    openCreateProject()
    return
  }
  requirementFileInput.value?.click()
}

async function handleRequirementFile(event) {
  const file = event.target.files?.[0]
  if (!file || !currentProject.value) return
  const isRevision = workflow.value?.state === 'waiting_product_revision'
  uploadingRequirement.value = true
  try {
    await uploadProjectInput(currentProject.value.project_id, file, {
      category: 'requirement',
      importedBy: newProject.created_by,
      moduleId: currentModule.value?.module_id,
    })
    await refreshCurrentProject()
    showToast(
      isRevision
        ? `已导入修订版 ${file.name}，可继续流程重新评审`
        : `已导入 ${file.name}`,
    )
  } catch (error) {
    showToast(error.message)
  } finally {
    uploadingRequirement.value = false
    event.target.value = ''
  }
}

async function generateProductConfirmationChecklist() {
  if (!currentProject.value || !artifacts.requirement_review) {
    showToast('请先完成需求评审，再生成产品确认清单')
    return
  }
  const projectId = currentProject.value.project_id
  const moduleId = currentModule.value?.module_id || null
  const requestId = contextRequestId
  confirmationChecklistBusy.value = true
  try {
    const result = await generateConfirmationChecklist(projectId, moduleId)
    if (requestId !== contextRequestId) return
    artifacts.product_confirmation_checklist = result.content
    confirmationChecklistMarkdown.value = result.markdown || ''
    workflow.value.active_artifacts.product_confirmation_checklist = {
      artifact_id: result.artifact_id,
      artifact_type: result.artifact_type,
      version: result.version,
    }
    showToast(`产品确认清单 v${result.version} 已生成并保存`)
  } catch (error) {
    if (requestId === contextRequestId) showToast(error.message)
  } finally {
    confirmationChecklistBusy.value = false
  }
}

function downloadConfirmationChecklist() {
  const checklist = artifacts.product_confirmation_checklist
  if (!checklist || !confirmationChecklistMarkdown.value) {
    showToast('当前没有可下载的产品确认清单')
    return
  }
  const blob = new Blob(
    [confirmationChecklistMarkdown.value],
    { type: 'text/markdown;charset=utf-8' },
  )
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `产品确认清单-v${checklist.meta.version}.md`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

async function continueWorkflow() {
  if (!currentProject.value) {
    openCreateProject()
    return
  }
  runningWorkflow.value = true
  syncLabel.value = 'Agent 运行中'
  try {
    const result = await runWorkflow(
      currentProject.value.project_id,
      null,
      currentModule.value?.module_id,
    )
    await refreshCurrentProject()
    const reason = result.action?.reason || '流程步骤执行完成'
    showToast(reason)
  } catch (error) {
    await refreshCurrentProject()
    showToast(error.message)
  } finally {
    runningWorkflow.value = false
  }
}

async function submitTestcaseApproval() {
  if (!currentProject.value || workflow.value?.state !== 'waiting_testcase_approval') return
  await submitApprovalAction('testcase_approval', 'approved')
}

async function submitReportApproval() {
  if (!currentProject.value || workflow.value?.state !== 'waiting_report_approval') return
  await submitApprovalAction('report_approval', 'approved')
}

function openApprovalDialog(approvalType, decision) {
  const expectedState = approvalType === 'testcase_approval'
    ? 'waiting_testcase_approval'
    : 'waiting_report_approval'
  if (workflow.value?.state !== expectedState) return
  approvalDialog.approvalType = approvalType
  approvalDialog.decision = decision
  approvalDialog.title = decision === 'rejected' ? '驳回当前产物' : '要求修改当前产物'
  approvalDialog.comment = ''
  approvalDialog.open = true
}

async function submitNonApproval() {
  if (!approvalDialog.comment.trim()) {
    showToast('请填写具体修改意见')
    return
  }
  await submitApprovalAction(
    approvalDialog.approvalType,
    approvalDialog.decision,
    approvalDialog.comment.trim(),
  )
}

async function submitApprovalAction(approvalType, decision, comment = '') {
  if (!currentProject.value) return
  approvalBusy.value = true
  try {
    await submitApproval(currentProject.value.project_id, {
      approval_type: approvalType,
      decision,
      decided_by: newProject.created_by,
      comment,
    }, currentModule.value?.module_id)
    approvalDialog.open = false
    reviewApproved.value = false
    reportApproved.value = false
    await refreshCurrentProject()
    const successMessage = decision === 'approved'
      ? approvalType === 'testcase_approval'
        ? '测试用例已批准，流程进入测试执行'
        : '测试报告已批准归档'
      : decision === 'rejected'
        ? '已驳回，流程返回修改阶段'
        : '修改意见已提交，流程返回修订阶段'
    showToast(successMessage)
  } catch (error) {
    showToast(error.message)
  } finally {
    approvalBusy.value = false
  }
}

function chooseEvidenceFile(item) {
  evidenceTarget.value = item
  evidenceFileInput.value?.click()
}

async function handleEvidenceFile(event) {
  const file = event.target.files?.[0]
  const target = evidenceTarget.value
  if (!file || !target || !currentProject.value) return
  evidenceBusy.value = true
  try {
    const evidence = await uploadEvidence(currentProject.value.project_id, file, {
      evidenceType: inferEvidenceType(file),
      description: `${target.id} 执行证据：${file.name}`,
      moduleId: currentModule.value?.module_id,
    })
    target.evidence.push(evidence)
    showToast(`${target.id} 已上传证据`)
  } catch (error) {
    showToast(error.message)
  } finally {
    evidenceBusy.value = false
    evidenceTarget.value = null
    event.target.value = ''
  }
}

async function submitExecutionResults() {
  if (!canSubmitExecution.value || !currentProject.value) return
  executionBusy.value = true
  try {
    await submitExecution(currentProject.value.project_id, {
      submitted_by: newProject.created_by,
      records: executionCases.value.map((item) => ({
        record_id: `record-${item.id}-v${item.version}`,
        case_id: item.id,
        case_version: item.version,
        environment: executionEnvironment.value,
        executed_by: item.executor,
        executed_at: new Date().toISOString(),
        result: { 通过: 'passed', 失败: 'failed', 阻塞: 'blocked' }[item.result],
        actual_result: item.actualResult,
        defect_refs: [],
        evidence: item.evidence,
        notes: [],
      })),
    }, currentModule.value?.module_id)
    await refreshCurrentProject()
    showToast('执行结果已提交，正在等待生成测试报告')
  } catch (error) {
    showToast(error.message)
  } finally {
    executionBusy.value = false
  }
}

function selectFinding(id) {
  selectedFindingId.value = id
  const finding = findings.value.find((item) => item.id === id)
  if (findingFilter.value !== '全部' && finding?.severity !== findingFilter.value) {
    findingFilter.value = '全部'
  }
  const firstLine = finding?.lineRanges[0]?.start
  if (firstLine && requirementInputPreview.value?.preview_kind === 'text') {
    requestAnimationFrame(() => {
      document.getElementById('requirement-line-' + firstLine)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
  }
}

function updateExecutionResult(item, result) {
  item.result = result
  if (item.executor === '待分配') item.executor = 'admin'
  if (result === '通过' && !item.actualResult) item.actualResult = '结果符合预期'
  if (result !== '通过' && item.actualResult === '结果符合预期') item.actualResult = ''
  showToast(`${item.id} 已标记为${result}`)
}

function approveReview() {
  reviewApproved.value = !reviewApproved.value
  showToast(reviewApproved.value ? '用例集已通过人工审批' : '已撤销人工审批')
}

function approveReport() {
  reportApproved.value = !reportApproved.value
  showToast(reportApproved.value ? '测试报告已批准归档' : '已撤销报告审批')
}

function showToast(message) {
  toast.value = message
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toast.value = ''
  }, 2400)
}

function severityLabel(severity) {
  return ({ blocker: '高', high: '高', medium: '中', low: '低' })[severity] || '中'
}

function formatDateTime(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

function inferEvidenceType(file) {
  if (file.type.startsWith('image/')) return 'screenshot'
  if (file.type.startsWith('video/')) return 'video'
  if (file.name.toLowerCase().endsWith('.log')) return 'log'
  return 'file'
}
function openEditProject(project) {
  projectMenuOpen.value = false
  manageDialog.open = true
  manageDialog.kind = 'project'
  manageDialog.projectId = project.project_id
  manageDialog.moduleId = ''
  manageDialog.name = project.name
}

function openEditModule(module) {
  projectMenuOpen.value = false
  manageDialog.open = true
  manageDialog.kind = 'module'
  manageDialog.projectId = currentProject.value?.project_id || ''
  manageDialog.moduleId = module.module_id
  manageDialog.name = module.name
}

async function submitManage() {
  if (!manageDialog.name.trim()) return
  manageDialog.busy = true
  try {
    if (manageDialog.kind === 'project') {
      await updateProject(manageDialog.projectId, { name: manageDialog.name.trim() })
      manageDialog.open = false
      await loadProjects(manageDialog.projectId)
      showToast('项目名称已更新')
    } else {
      await updateFeatureModule(
        manageDialog.projectId,
        manageDialog.moduleId,
        { name: manageDialog.name.trim() },
      )
      manageDialog.open = false
      await selectProject(manageDialog.projectId, manageDialog.moduleId)
      showToast('功能模块名称已更新')
    }
  } catch (error) {
    showToast(error.message)
  } finally {
    manageDialog.busy = false
  }
}

function openDeleteProject(project) {
  projectMenuOpen.value = false
  deleteDialog.open = true
  deleteDialog.kind = 'project'
  deleteDialog.projectId = project.project_id
  deleteDialog.moduleId = ''
  deleteDialog.title = '删除项目“' + project.name + '”？'
  deleteDialog.description = '项目下的全部功能模块、需求、测试用例、证据、审批和报告都会被永久删除。'
}

function openDeleteModule(module) {
  projectMenuOpen.value = false
  deleteDialog.open = true
  deleteDialog.kind = 'module'
  deleteDialog.projectId = currentProject.value?.project_id || ''
  deleteDialog.moduleId = module.module_id
  deleteDialog.title = '删除功能模块“' + module.name + '”？'
  deleteDialog.description = '该模块的需求、测试用例、证据、审批和报告都会被永久删除，不影响同项目其他模块。'
}

async function submitDelete() {
  deleteDialog.busy = true
  try {
    if (deleteDialog.kind === 'project') {
      const projectId = deleteDialog.projectId
      localStorage.removeItem('butterfly-module-id:' + projectId)
      await deleteProject(projectId)
      deleteDialog.open = false
      await loadProjects()
      showToast('项目及其全部资料已删除')
    } else {
      const projectId = deleteDialog.projectId
      const moduleId = deleteDialog.moduleId
      const deletingCurrent = currentProject.value?.project_id === projectId
        && currentModule.value?.module_id === moduleId
      localStorage.removeItem('butterfly-module-id:' + projectId)
      await deleteFeatureModule(projectId, moduleId)
      deleteDialog.open = false
      if (deletingCurrent) {
        await selectProject(projectId, null)
      } else {
        await selectProject(projectId, currentModule.value?.module_id || null)
      }
      showToast('功能模块及其全部资料已删除')
    }
  } catch (error) {
    showToast(error.message)
  } finally {
    deleteDialog.busy = false
  }
}
</script>

<template>
  <a class="skip-link" href="#main-workspace">跳到主要工作区</a>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand-block">
        <button class="icon-button mobile-menu" type="button" title="打开阶段导航" @click="sidebarOpen = true">
          <Menu :size="19" />
        </button>
        <div class="brand-mark" aria-hidden="true"><Sparkles :size="18" /></div>
        <div>
          <div class="brand-name">Butterfly Agent</div>
          <div class="brand-caption">智能测试全流程工作台</div>
        </div>
      </div>

      <div class="topbar-actions">
        <div class="sync-state" :class="{ failed: syncLabel.includes('失败') }">
          <span class="live-dot"></span>{{ syncLabel }}
        </div>
        <button class="icon-button" type="button" title="切换日间或夜间主题" @click="theme = theme === 'light' ? 'dark' : 'light'">
          <Moon v-if="theme === 'light'" :size="18" />
          <Sun v-else :size="18" />
        </button>
        <button class="icon-button notification-button" type="button" title="通知">
          <Bell :size="18" />
          <span class="notification-dot"></span>
        </button>
        <div class="user-avatar" title="当前用户：admin">A</div>
      </div>
    </header>

    <div v-if="sidebarOpen" class="sidebar-scrim" @click="sidebarOpen = false"></div>
    <aside class="stage-sidebar" :class="{ open: sidebarOpen }" aria-label="测试阶段">
      <div class="sidebar-mobile-head">
        <strong>测试阶段</strong>
        <button class="icon-button" type="button" title="关闭阶段导航" @click="sidebarOpen = false"><X :size="18" /></button>
      </div>
      <div class="project-switcher">
        <button
          class="project-context"
          type="button"
          :aria-label="`当前上下文：${currentProjectName} / ${currentContextName}`"
          :aria-expanded="projectMenuOpen"
          @click="projectMenuOpen = !projectMenuOpen"
        >
          <span class="project-context-top"><span class="project-label">当前上下文</span><ChevronDown :size="13" :class="{ rotated: projectMenuOpen }" /></span>
          <strong>{{ currentContextName }}</strong>
          <span class="project-id">{{ currentProject?.project_id || '未选择' }} / {{ currentContextId }} · {{ currentStateLabel }}</span>
        </button>
        <div v-if="projectMenuOpen" class="project-menu">
          <div class="project-menu-heading">项目列表</div>
          <div v-for="project in projects" :key="project.project_id" class="project-option-row">
            <button
              type="button"
              class="project-option"
              :class="{ active: project.project_id === currentProject?.project_id }"
              @click="selectProject(project.project_id)"
            >
              <span><strong>{{ project.name }}</strong><small>{{ project.project_id }}</small></span>
              <Check v-if="project.project_id === currentProject?.project_id" :size="14" />
            </button>
            <span class="project-option-actions">
              <button class="menu-action-button" type="button" title="编辑项目" @click.stop="openEditProject(project)"><Pencil :size="13" /></button>
              <button class="menu-action-button danger-action" type="button" title="删除项目" @click.stop="openDeleteProject(project)"><Trash2 :size="13" /></button>
            </span>
          </div>
          <div v-if="!projects.length && !projectsLoading" class="project-menu-empty">暂无项目</div>
          <div v-if="currentProject" class="module-branch">
            <div class="module-menu-heading">
              <span>功能模块</span>
              <button class="module-add-button" type="button" title="新建功能模块" @click="openCreateModule">
                <Plus :size="14" />
              </button>
            </div>
            <button
              class="module-option"
              :class="{ active: !currentModule }"
              type="button"
              @click="selectModule(null)"
            >
              <span class="module-tree-mark"></span>
              <span><strong>V1 兼容流程</strong><small>项目根级上下文</small></span>
              <Check v-if="!currentModule" :size="14" />
            </button>
            <div v-for="module in modules" :key="module.module_id" class="module-option-row">
              <button
                class="module-option"
                :class="{ active: module.module_id === currentModule?.module_id }"
                type="button"
                @click="selectModule(module.module_id)"
              >
                <span class="module-tree-mark"></span>
                <span><strong>{{ module.name }}</strong><small>{{ module.module_id }} · {{ stateLabels[module.state] || module.state }}</small></span>
                <Check v-if="module.module_id === currentModule?.module_id" :size="14" />
              </button>
              <span class="project-option-actions">
                <button class="menu-action-button" type="button" title="编辑功能模块" @click.stop="openEditModule(module)"><Pencil :size="13" /></button>
                <button class="menu-action-button danger-action" type="button" title="删除功能模块" @click.stop="openDeleteModule(module)"><Trash2 :size="13" /></button>
              </span>
            </div>
            <button class="module-create-option" type="button" @click="openCreateModule">
              <Plus :size="14" />新建功能模块
            </button>
          </div>
          <button class="project-create-option" type="button" @click="openCreateProject">
            <Plus :size="15" />新建项目
          </button>
        </div>
      </div>
      <div class="sidebar-kicker">测试生命周期</div>
      <nav class="stage-list">
        <button
          v-for="stage in stages"
          :key="stage.id"
          type="button"
          class="stage-item"
          :class="{ active: activeStageId === stage.id }"
          :aria-current="activeStageId === stage.id ? 'step' : undefined"
          @click="switchStage(stage.id)"
        >
          <span class="stage-rail">
            <span class="stage-icon"><component :is="iconByStage[stage.id]" :size="18" /></span>
          </span>
          <span class="stage-copy">
            <span class="stage-order">阶段 {{ stage.index }}</span>
            <strong>{{ stage.title }}</strong>
            <span class="stage-meta">
              <span class="status-dot" :class="stage.tone"></span>{{ stage.status }}
              <span v-if="stage.issueCount" class="issue-count">{{ stage.issueCount }} 个问题</span>
            </span>
          </span>
          <ChevronRight :size="16" class="stage-chevron" />
        </button>
      </nav>

      <div class="sidebar-summary">
        <div class="summary-heading"><Gauge :size="16" /> 项目质量概览</div>
        <div class="summary-row"><span>流程进度</span><strong>{{ workflowProgress }}%</strong></div>
        <div class="progress-track"><span :style="{ width: `${workflowProgress}%` }"></span></div>
        <div class="summary-grid">
          <div><strong>{{ workflow?.input_files?.length || 0 }}</strong><span>输入资料</span></div>
          <div><strong>{{ Object.keys(workflow?.active_artifacts || {}).length }}</strong><span>当前产物</span></div>
          <div><strong class="success-text">{{ workflow?.transition_history?.length || 0 }}</strong><span>状态变更</span></div>
        </div>
      </div>
    </aside>

    <main id="main-workspace" class="workspace">
      <section class="stage-heading">
        <div>
          <div class="breadcrumb">
            <span>{{ currentProjectName }}</span>
            <ChevronRight :size="13" />
            <span>{{ currentContextId }}</span>
            <ChevronRight :size="13" />
            <span>{{ activeStage.title }}</span>
          </div>
          <div class="title-line">
            <h1>{{ activeStage.title }}</h1>
            <span class="stage-status" :class="activeStage.tone"><span></span>{{ activeStage.status }}</span>
          </div>
          <p>当前负责人：{{ activeStage.owner }}</p>
        </div>
        <div class="heading-actions">
          <button class="button secondary" type="button" @click="auditOpen = true"><History :size="16" />审计记录</button>
          <button class="button primary" type="button" :disabled="runningWorkflow" @click="continueWorkflow">
            <LoaderCircle v-if="runningWorkflow" class="spin" :size="16" />
            <template v-else>继续流程<ArrowRight :size="16" /></template>
          </button>
        </div>
      </section>

      <section class="stepper" aria-label="当前阶段流程">
        <button
          v-for="(step, index) in activeStage.steps"
          :key="step"
          type="button"
          class="step-item"
          :class="{ completed: index < currentSteps[activeStageId], active: index === currentSteps[activeStageId] }"
          @click="setCurrentStep(index)"
        >
          <span class="step-node"><Check v-if="index < currentSteps[activeStageId]" :size="14" /><span v-else>{{ index + 1 }}</span></span>
          <span class="step-label">{{ step }}</span>
        </button>
      </section>

      <section v-if="!currentProject" class="stage-content empty-workspace">
        <div class="empty-symbol"><FileSearch :size="28" /></div>
        <h2>先创建一个测试项目</h2>
        <p>项目用于隔离产品需求、Agent 产物、人工审批和测试证据。</p>
        <button class="button primary" type="button" @click="openCreateProject"><Plus :size="16" />新建项目</button>
      </section>

      <section v-else-if="activeStageId === 'requirement-review'" class="stage-content review-workspace">
        <div class="panel document-panel">
          <div class="panel-header">
            <div>
              <div class="eyebrow">产品需求原文</div>
              <h2>{{ requirementDocument.name }}</h2>
            </div>
            <div class="document-meta"><FileText :size="15" />{{ requirementDocument.updatedAt }}</div>
          </div>
          <div class="document-toolbar">
            <span>{{ requirementDocument.source }}</span>
            <div class="document-actions">
              <button class="text-button" type="button" :disabled="uploadingRequirement" @click="chooseRequirementFile">
                <LoaderCircle v-if="uploadingRequirement" class="spin" :size="14" />
                <Upload v-else :size="14" />{{ workflow?.state === 'waiting_product_revision' ? '上传修订版' : '导入需求' }}
              </button>
              <button class="text-button" type="button" @click="refreshCurrentProject"><RefreshCw :size="14" />检查更新</button>
            </div>
            <input
              ref="requirementFileInput"
              class="visually-hidden"
              type="file"
              accept=".md,.txt,.pdf,.doc,.docx,.png,.jpg,.jpeg,.webp"
              @change="handleRequirementFile"
            />
          </div>
          <div v-if="workflow?.state === 'waiting_product_revision'" class="revision-banner">
            <AlertTriangle :size="17" />
            <div><strong>需求评审未通过</strong><span>请根据右侧问题修改需求，上传修订版后点击“继续流程”重新评审。</span></div>
            <button class="button secondary small" type="button" :disabled="uploadingRequirement" @click="chooseRequirementFile"><Upload :size="15" />上传修订版</button>
          </div>
          <article class="requirement-document">
            <div
              v-if="requirementInputPreview?.preview_kind === 'text'"
              class="requirement-source-text"
              aria-label="带行号的需求原文"
            >
              <div
                v-for="(line, index) in requirementSourceLines"
                :id="'requirement-line-' + (index + 1)"
                :key="index"
                class="requirement-source-line"
                :class="{ highlighted: selectedRequirementLines.has(index + 1) }"
              >
                <span class="requirement-line-number">{{ index + 1 }}</span>
                <span class="requirement-line-content">{{ line || ' ' }}</span>
              </div>
            </div>
            <div v-else-if="requirementInputPreview?.preview_kind === 'image'" class="requirement-media-preview">
              <img :src="requirementInputPreview.resolvedContentUrl" :alt="requirementDocument.name" />
            </div>
            <iframe
              v-else-if="requirementInputPreview?.preview_kind === 'pdf'"
              class="requirement-pdf-preview"
              :src="requirementInputPreview.resolvedContentUrl"
              :title="requirementDocument.name"
            ></iframe>
            <div v-else class="artifact-empty">
              <FileText :size="24" />
              <strong>{{ requirementInputPreview ? '当前格式不支持在线预览' : '尚未导入产品需求' }}</strong>
              <span>{{ requirementInputPreview ? requirementDocument.name : '请选择产品提供的需求文件' }}</span>
            </div>
            <div v-if="requirementInputPreview?.truncated" class="preview-notice">文件较大，当前显示前 1 MB 内容</div>
          </article>
        </div>

        <div class="review-column">
          <div class="panel findings-panel">
            <div class="panel-header compact">
              <div>
                <div class="eyebrow">AI 评审批注</div>
              <h2>发现 {{ findings.length }} 个待确认问题</h2>
              </div>
              <span class="agent-chip"><Bot :size="14" />需求与用例 Agent</span>
            </div>
            <div class="filter-tabs" aria-label="按严重级别筛选">
              <Filter :size="14" />
              <button
                v-for="filter in ['全部', '高', '中']"
                :key="filter"
                type="button"
                :class="{ active: findingFilter === filter }"
                @click="findingFilter = filter"
              >{{ filter }} <span>{{ findingCounts[filter] }}</span></button>
            </div>
            <div class="finding-list">
              <button
                v-for="finding in filteredFindings"
                :id="`finding-${finding.id}`"
                :key="finding.id"
                type="button"
                class="finding-card"
                :class="[{ selected: selectedFindingId === finding.id }, `severity-${finding.severity}`]"
                @click="selectFinding(finding.id)"
              >
                <span class="finding-topline">
                  <span class="severity-badge" :class="`severity-${finding.severity}`">{{ finding.severity }}风险</span>
                  <span class="finding-category">{{ finding.category }}</span>
                  <span class="finding-id">{{ finding.id }}</span>
                </span>
                <span class="finding-location">
                  <MapPin :size="14" />
                  <span><b>定位：</b>{{ finding.location }}</span>
                </span>
                <strong><span>问题：</span>{{ finding.title }}</strong>
                <span class="finding-detail"><b>影响：</b>{{ finding.detail }}</span>
                <span class="suggestion"><Sparkles :size="14" /><span><b>建议：</b>{{ finding.suggestion }}</span></span>
                <span class="finding-status"><Clock3 :size="13" />{{ finding.status }}</span>
              </button>
              <div v-if="!filteredFindings.length" class="artifact-empty compact-empty">
                <CheckCircle2 :size="22" />
                <strong>{{ artifacts.requirement_review ? '当前筛选下没有问题' : '等待生成需求评审产物' }}</strong>
              </div>
            </div>
          </div>

          <div class="quality-gate">
            <div class="gate-icon"><ShieldCheck :size="20" /></div>
            <div class="gate-copy">
              <div><strong>需求准入门禁</strong><span :class="artifacts.requirement_review?.decision === 'pass' ? 'verified-chip' : 'gate-blocked'">{{ artifacts.requirement_review?.decision === 'pass' ? '已通过' : '暂未通过' }}</span></div>
              <p>{{ findings.length ? `${findingCounts['高']} 个高风险问题、${findingCounts['中']} 个中风险问题待处理。` : '等待 AI 完成需求评审并给出准入结论。' }}</p>
            </div>
            <button
              class="button secondary small"
              type="button"
              :disabled="!artifacts.requirement_review || confirmationChecklistBusy"
              @click="generateProductConfirmationChecklist"
            >
              <LoaderCircle v-if="confirmationChecklistBusy" class="spin" :size="14" />
              <ListChecks v-else :size="14" />
              {{ artifacts.product_confirmation_checklist ? '重新生成清单' : '生成确认清单' }}
            </button>
          </div>

          <div v-if="artifacts.product_confirmation_checklist" class="confirmation-checklist panel">
            <div class="panel-header compact">
              <div>
                <div class="eyebrow">产品确认清单</div>
                <h2>
                  {{ artifacts.product_confirmation_checklist.items.length }} 项待产品确认
                  · v{{ artifacts.product_confirmation_checklist.meta.version }}
                </h2>
              </div>
              <button class="button secondary small" type="button" @click="downloadConfirmationChecklist">
                <Download :size="14" />下载 Markdown
              </button>
            </div>
            <div class="confirmation-items">
              <div
                v-for="item in artifacts.product_confirmation_checklist.items"
                :key="item.item_id"
                class="confirmation-item"
              >
                <div class="confirmation-item-heading">
                  <span class="severity-badge" :class="`severity-${severityLabel(item.severity)}`">
                    {{ severityLabel(item.severity) }}风险
                  </span>
                  <strong>{{ item.item_id }} · {{ item.question }}</strong>
                </div>
                <p><MapPin :size="13" /><span><b>定位：</b>{{ item.location }}</span></p>
                <p><span><b>问题：</b>{{ item.problem }}</span></p>
                <p><span><b>影响：</b>{{ item.impact }}</span></p>
                <p><span><b>建议：</b>{{ item.suggestion }}</span></p>
              </div>
              <div
                v-if="!artifacts.product_confirmation_checklist.items.length"
                class="artifact-empty compact-empty"
              >
                <CheckCircle2 :size="22" />
                <strong>当前需求评审没有待确认事项</strong>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section v-else-if="activeStageId === 'case-design'" class="stage-content vertical-workspace">
        <div class="metric-strip">
          <div><span>需求模块</span><strong>{{ designStats.requirements }}</strong><small>结构化需求</small></div>
          <div><span>测试点</span><strong>{{ designStats.points }}</strong><small>高风险 {{ designStats.highRiskPoints }} 项</small></div>
          <div><span>测试用例</span><strong>{{ designStats.cases }}</strong><small>P0 用例 {{ designStats.p0Cases }} 条</small></div>
          <div><span>需求覆盖率</span><strong>{{ designStats.coverage }}%</strong><small>按测试点关联计算</small></div>
        </div>
        <div class="two-column-layout analysis-layout">
          <div class="panel requirement-map-panel">
            <div class="panel-header compact">
              <div><div class="eyebrow">需求拆解</div><h2>需求—测试点覆盖图</h2></div>
              <button class="button secondary small" type="button" :disabled="runningWorkflow" @click="continueWorkflow"><Sparkles :size="15" />重新提取</button>
            </div>
            <div class="requirement-map">
              <button v-for="item in requirements" :key="item.id" type="button" class="requirement-row" @click="showToast(`已选择 ${item.id}`)">
                <span class="requirement-id">{{ item.id }}</span>
                <span class="requirement-name"><strong>{{ item.title }}</strong><small>{{ item.points }} 个测试点 · {{ item.cases }} 条用例</small></span>
                <span class="coverage-cell"><span>{{ item.coverage }}%</span><i><b :style="{ width: `${item.coverage}%` }"></b></i></span>
                <span class="risk-label" :class="`severity-${item.risk}`">{{ item.risk }}风险</span>
                <ChevronRight :size="15" />
              </button>
              <div v-if="!requirements.length" class="artifact-empty compact-empty"><strong>等待生成需求分析产物</strong></div>
            </div>
          </div>
          <div class="panel design-aside">
            <div class="panel-header compact"><div><div class="eyebrow">结构校验</div><h2>用例集健康度</h2></div><span class="score-ring">{{ designStats.coverage }}</span></div>
            <div class="check-list">
              <div class="passed"><CheckCircle2 :size="17" /><span><strong>字段结构完整</strong><small>{{ designStats.cases }} / {{ designStats.cases }} 条</small></span></div>
              <div class="passed"><CheckCircle2 :size="17" /><span><strong>需求可追溯</strong><small>{{ designStats.coverage }}% 已关联测试点</small></span></div>
              <div :class="designStats.highRiskPoints ? 'warning' : 'passed'"><AlertTriangle :size="17" /><span><strong>高风险测试点</strong><small>{{ designStats.highRiskPoints }} 项需重点评审</small></span></div>
            </div>
            <button class="button primary full-width" type="button" :disabled="runningWorkflow" @click="continueWorkflow">提交用例评审<ArrowRight :size="16" /></button>
          </div>
        </div>
        <div class="panel table-panel">
          <div class="panel-header compact"><div><div class="eyebrow">用例设计</div><h2>测试用例预览</h2></div><button class="button secondary small" type="button" :disabled="runningWorkflow" @click="continueWorkflow"><Sparkles :size="15" />生成用例</button></div>
          <div class="table-scroll">
            <table>
              <thead><tr><th>用例 ID</th><th>测试用例</th><th>优先级</th><th>类型</th><th>关联需求</th><th>结构状态</th></tr></thead>
              <tbody><tr v-for="item in testCases" :key="item.id"><td><b>{{ item.id }}</b></td><td>{{ item.title }}</td><td><span class="priority-badge">{{ item.priority }}</span></td><td>{{ item.type }}</td><td>{{ item.requirement }}</td><td><span class="case-status" :class="{ warning: item.status !== '结构完整' }">{{ item.status }}</span></td></tr></tbody>
            </table>
          </div>
        </div>
      </section>

      <section v-else-if="activeStageId === 'case-review'" class="stage-content vertical-workspace">
        <div class="review-summary-band">
          <div class="review-score"><span>用例问题健康度</span><strong>{{ testcaseHealthScore }}</strong><small>/ 100</small></div>
          <div class="dimension-scores">
            <div><span>问题总数</span><strong>{{ reviewIssues.length }}</strong><i><b :style="{ width: `${Math.min(100, reviewIssues.length * 10)}%` }"></b></i></div>
            <div><span>高风险</span><strong>{{ reviewIssues.filter((item) => item.severity === '高').length }}</strong><i><b :style="{ width: `${Math.min(100, reviewIssues.filter((item) => item.severity === '高').length * 20)}%` }"></b></i></div>
            <div><span>中风险</span><strong>{{ reviewIssues.filter((item) => item.severity === '中').length }}</strong><i><b :style="{ width: `${Math.min(100, reviewIssues.filter((item) => item.severity === '中').length * 20)}%` }"></b></i></div>
            <div><span>低风险</span><strong>{{ reviewIssues.filter((item) => item.severity === '低').length }}</strong><i><b :style="{ width: `${Math.min(100, reviewIssues.filter((item) => item.severity === '低').length * 20)}%` }"></b></i></div>
          </div>
          <div class="review-decision"><span>评审结论</span><strong><AlertTriangle :size="17" />{{ artifacts.testcase_review?.decision || '等待评审' }}</strong><small>{{ artifacts.testcase_review?.coverage_summary || '等待用例评审 Agent 输出结论' }}</small></div>
        </div>
        <div class="two-column-layout review-issues-layout">
          <div class="panel">
            <div class="panel-header compact"><div><div class="eyebrow">独立评审问题</div><h2>待处理问题</h2></div><span class="count-pill">{{ reviewIssues.length }} 个问题</span></div>
            <div class="review-issue-list">
              <div v-for="issue in reviewIssues" :key="issue.id" class="review-issue">
                <span class="severity-badge" :class="`severity-${issue.severity}`">{{ issue.severity }}</span>
                <div><div class="issue-title-line"><strong>{{ issue.title }}</strong><span>{{ issue.id }}</span></div><p>{{ issue.caseId }} · {{ issue.dimension }}</p></div>
                <span class="case-status" :class="{ warning: issue.status !== '已修订' }">{{ issue.status }}</span>
              </div>
              <div v-if="!reviewIssues.length" class="artifact-empty compact-empty"><strong>{{ artifacts.testcase_review ? '本轮未发现评审问题' : '等待生成用例评审产物' }}</strong></div>
            </div>
          </div>
          <div class="panel approval-panel">
            <div class="approval-symbol" :class="{ approved: reviewApproved }"><UserCheck :size="25" /></div>
            <div class="eyebrow">人工质量门禁</div>
            <h2>{{ workflow?.state === 'waiting_case_revision' ? '等待修订测试用例' : reviewApproved ? '用例集已确认' : '等待测试负责人审批' }}</h2>
            <p>{{ artifacts.testcase_review?.coverage_summary || 'AI 负责发现问题和给出证据，最终准入决定由测试负责人确认。' }}</p>
            <template v-if="workflow?.state === 'waiting_testcase_approval'">
              <label class="approval-check"><input type="checkbox" :checked="reviewApproved" @change="approveReview" /><span><Check :size="14" /></span>我已检查高风险用例与修订记录</label>
              <div class="approval-actions">
                <button class="button secondary" type="button" :disabled="approvalBusy" @click="openApprovalDialog('testcase_approval', 'changes_requested')"><RefreshCw :size="15" />要求修改</button>
                <button class="button danger" type="button" :disabled="approvalBusy" @click="openApprovalDialog('testcase_approval', 'rejected')"><XCircle :size="15" />驳回</button>
                <button class="button primary approval-primary" type="button" :disabled="!reviewApproved || approvalBusy" @click="submitTestcaseApproval">
                  <LoaderCircle v-if="approvalBusy" class="spin" :size="16" />
                  <template v-else>确认通过<ArrowRight :size="16" /></template>
                </button>
              </div>
            </template>
            <button v-else-if="workflow?.state === 'waiting_case_revision'" class="button primary full-width" type="button" :disabled="runningWorkflow" @click="continueWorkflow">
              <LoaderCircle v-if="runningWorkflow" class="spin" :size="16" />
              <template v-else>开始修订用例<RefreshCw :size="16" /></template>
            </button>
            <div v-else class="approval-state-note">当前流程尚未进入人工审批节点</div>
          </div>
        </div>
      </section>

      <section v-else-if="activeStageId === 'execution'" class="stage-content vertical-workspace">
        <div class="execution-toolbar panel">
          <div><div class="eyebrow">执行批次</div><h2>地址修改功能测试 · 第 1 轮</h2></div>
          <div class="environment-control">
            <label for="environment">测试环境</label>
            <select id="environment" v-model="executionEnvironment"><option>QA-02 · v2.8.14-rc3</option><option>QA-01 · v2.8.13</option></select>
            <label class="switch-control"><input v-model="environmentReady" type="checkbox" /><span></span>环境已确认</label>
          </div>
          <button class="button primary" type="button" :disabled="!environmentReady" @click="showToast('测试批次已开始执行')"><Play :size="16" />开始执行</button>
        </div>
        <div class="metric-strip execution-metrics">
          <div><span>总用例</span><strong>{{ executionStats.total }}</strong><small>当前测试设计</small></div>
          <div><span>通过</span><strong class="success-text">{{ executionStats.passed }}</strong><small>通过率 {{ executionStats.rate }}%</small></div>
          <div><span>失败</span><strong class="danger-text">{{ executionStats.failed }}</strong><small>需要缺陷单</small></div>
          <div><span>阻塞 / 未执行</span><strong class="warning-text">{{ executionStats.blocked + executionStats.pending }}</strong><small>等待环境与执行</small></div>
        </div>
        <div class="panel table-panel">
          <div class="panel-header compact">
            <div><div class="eyebrow">执行工作台</div><h2>测试用例与证据</h2></div>
            <div class="execution-actions">
              <div class="search-box"><Search :size="15" /><input aria-label="搜索用例" placeholder="搜索用例 ID 或标题" /></div>
              <button class="button primary small" type="button" :disabled="!canSubmitExecution || executionBusy" @click="submitExecutionResults">
                <LoaderCircle v-if="executionBusy" class="spin" :size="15" />
                <template v-else>提交执行结果<ArrowRight :size="15" /></template>
              </button>
            </div>
          </div>
          <div class="table-scroll">
            <table class="execution-table">
              <thead><tr><th>用例</th><th>优先级</th><th>执行人</th><th>证据</th><th>执行结果</th></tr></thead>
              <tbody>
                <tr v-for="item in executionCases" :key="item.id">
                  <td><b>{{ item.id }}</b><small>{{ item.title }}</small><input v-model.trim="item.actualResult" class="actual-result-input" :aria-label="`${item.id} 实际结果`" placeholder="填写实际结果" /></td><td><span class="priority-badge">{{ item.priority }}</span></td><td>{{ item.executor }}</td>
                  <td><button class="text-button" type="button" :disabled="evidenceBusy" @click="chooseEvidenceFile(item)"><Paperclip :size="14" />{{ item.evidence.length ? `${item.evidence.length} 份` : '上传' }}</button></td>
                  <td><div class="result-control"><button v-for="result in ['通过', '失败', '阻塞']" :key="result" type="button" :class="[result, { active: item.result === result }]" :title="`标记为${result}`" @click="updateExecutionResult(item, result)"><Check v-if="result === '通过'" :size="14" /><XCircle v-else-if="result === '失败'" :size="14" /><Clock3 v-else :size="14" /><span>{{ result }}</span></button></div></td>
                </tr>
              </tbody>
            </table>
            <div v-if="!executionCases.length" class="artifact-empty"><strong>等待测试设计产物进入执行阶段</strong></div>
          </div>
          <input ref="evidenceFileInput" class="visually-hidden" type="file" @change="handleEvidenceFile" />
        </div>
      </section>

      <section v-else class="stage-content vertical-workspace">
        <div class="report-hero">
          <div><div class="eyebrow">测试结论</div><h2>{{ reportData?.conclusion || '等待生成测试报告' }}</h2><p>{{ reportData?.scope || '完成测试执行后，主流程 Agent 将基于事实生成报告。' }}</p></div>
          <div class="report-verdict"><AlertTriangle :size="25" /><span><strong>{{ reportData?.risk_summary?.length || 0 }} 项遗留风险</strong><small>{{ reportData ? '需在发布前确认' : '尚无报告事实' }}</small></span></div>
        </div>
        <div class="metric-strip report-metrics">
          <div><span>计划用例</span><strong>{{ reportData?.total_cases || 0 }}</strong><small>报告事实快照</small></div>
          <div><span>通过率</span><strong>{{ reportData?.total_cases ? Math.round((reportData.passed / reportData.total_cases) * 1000) / 10 : 0 }}%</strong><small class="success-text">{{ reportData?.passed || 0 }} 条通过</small></div>
          <div><span>失败 / 阻塞</span><strong>{{ (reportData?.failed || 0) + (reportData?.blocked || 0) }}</strong><small class="danger-text">失败 {{ reportData?.failed || 0 }} · 阻塞 {{ reportData?.blocked || 0 }}</small></div>
          <div><span>遗留风险</span><strong>{{ reportData?.risk_summary?.length || 0 }}</strong><small class="warning-text">缺陷 {{ reportData?.defect_refs?.length || 0 }} 个</small></div>
        </div>
        <div class="two-column-layout report-layout">
          <div class="panel">
            <div class="panel-header compact"><div><div class="eyebrow">风险与事实</div><h2>发布前置条件</h2></div><span class="verified-chip"><ShieldCheck :size="14" />事实已校验</span></div>
            <div class="risk-list">
              <div v-for="(risk, index) in reportData?.risk_summary || []" :key="risk"><span class="risk-index">{{ String(index + 1).padStart(2, '0') }}</span><div><strong>{{ risk }}</strong><p>来源：测试报告事实快照</p></div><span class="severity-badge severity-中">待确认</span></div>
              <div v-if="!reportData?.risk_summary?.length" class="artifact-empty compact-empty"><strong>{{ reportData ? '当前报告未记录遗留风险' : '等待生成测试报告产物' }}</strong></div>
            </div>
            <div class="report-notes"><strong>报告环境</strong><p>{{ reportData ? `${reportData.environment} · ${reportData.scope}` : '尚无可核验的报告事实' }}</p></div>
          </div>
          <div class="panel approval-panel report-approval">
            <div class="approval-symbol" :class="{ approved: reportApproved }"><FileCheck2 :size="25" /></div>
            <div class="eyebrow">报告审批</div><h2>{{ reportApproved ? '报告已批准归档' : '等待测试负责人审批' }}</h2>
            <p>批准后锁定报告事实快照，后续变更将生成新的报告版本。</p>
            <template v-if="workflow?.state === 'waiting_report_approval'">
              <label class="approval-check"><input type="checkbox" :checked="reportApproved" @change="approveReport" /><span><Check :size="14" /></span>我确认报告数据与发布建议</label>
              <div class="approval-actions">
                <button class="button secondary" type="button" :disabled="approvalBusy" @click="openApprovalDialog('report_approval', 'changes_requested')"><RefreshCw :size="15" />要求修改</button>
                <button class="button danger" type="button" :disabled="approvalBusy" @click="openApprovalDialog('report_approval', 'rejected')"><XCircle :size="15" />驳回</button>
                <button class="button primary approval-primary" type="button" :disabled="!reportApproved || approvalBusy" @click="submitReportApproval">
                  <LoaderCircle v-if="approvalBusy" class="spin" :size="16" />
                  <template v-else>批准归档<Upload :size="16" /></template>
                </button>
              </div>
            </template>
            <button v-else-if="workflow?.state === 'generating_report'" class="button primary full-width" type="button" :disabled="runningWorkflow" @click="continueWorkflow">
              <LoaderCircle v-if="runningWorkflow" class="spin" :size="16" />
              <template v-else>重新生成报告<RefreshCw :size="16" /></template>
            </button>
            <div v-else class="approval-state-note">当前流程尚未进入报告审批节点</div>
          </div>
        </div>
      </section>
    </main>

    <aside class="audit-drawer" :class="{ open: auditOpen }" aria-label="质量门禁与审计流">
      <div class="drawer-header"><div><div class="eyebrow">可追溯记录</div><h2>质量门禁与审计流</h2></div><button class="icon-button" type="button" title="关闭审计流" @click="auditOpen = false"><X :size="18" /></button></div>
      <div class="gate-summary"><ShieldCheck :size="20" /><div><strong>当前状态：{{ currentStateLabel }}</strong><span>{{ findings.length }} 个需求问题 · {{ reviewIssues.length }} 个用例问题</span></div><span :class="workflow?.awaiting_human ? 'gate-blocked' : 'verified-chip'">{{ workflow?.awaiting_human ? '等待人工' : '流转中' }}</span></div>
      <div class="audit-timeline">
        <div v-for="event in auditEvents" :key="`${event.time}-${event.action}`" class="audit-event">
          <span class="audit-time">{{ event.time }}</span><span class="timeline-node"></span><div><strong>{{ event.action }}</strong><p>{{ event.actor }} · {{ event.detail }}</p></div>
        </div>
        <div v-if="!auditEvents.length" class="artifact-empty compact-empty"><strong>暂无状态迁移记录</strong></div>
      </div>
    </aside>

    <div v-if="approvalDialog.open" class="modal-scrim" @click.self="approvalDialog.open = false">
      <form class="modal-dialog approval-dialog" @submit.prevent="submitNonApproval">
        <div class="modal-header">
          <div><div class="eyebrow">人工审批意见</div><h2>{{ approvalDialog.title }}</h2></div>
          <button class="icon-button" type="button" title="关闭" @click="approvalDialog.open = false"><X :size="18" /></button>
        </div>
        <div class="decision-notice" :class="approvalDialog.decision">
          <RefreshCw v-if="approvalDialog.decision === 'changes_requested'" :size="18" />
          <XCircle v-else :size="18" />
          <span><strong>{{ approvalDialog.decision === 'changes_requested' ? '返回修订' : '驳回当前版本' }}</strong><small>审批意见将进入审计记录，并作为下一轮 Agent 修订依据。</small></span>
        </div>
        <label class="form-field">
          <span>修改意见</span>
          <textarea v-model.trim="approvalDialog.comment" required maxlength="4000" rows="5" placeholder="请说明需要修改的问题、范围和验收标准"></textarea>
          <small>{{ approvalDialog.comment.length }} / 4000</small>
        </label>
        <div class="modal-actions">
          <button class="button secondary" type="button" @click="approvalDialog.open = false">取消</button>
          <button class="button" :class="approvalDialog.decision === 'rejected' ? 'danger' : 'primary'" type="submit" :disabled="approvalBusy || !approvalDialog.comment.trim()">
            <LoaderCircle v-if="approvalBusy" class="spin" :size="16" />
            <template v-else>提交审批意见</template>
          </button>
        </div>
      </form>
    </div>

    <div v-if="createProjectOpen" class="modal-scrim" @click.self="createProjectOpen = false">
      <form class="modal-dialog" @submit.prevent="submitNewProject">
        <div class="modal-header">
          <div><div class="eyebrow">Butterfly Agent</div><h2>新建测试项目</h2></div>
          <button class="icon-button" type="button" title="关闭" @click="createProjectOpen = false"><X :size="18" /></button>
        </div>
        <label class="form-field">
          <span>项目名称</span>
          <input v-model.trim="newProject.name" required maxlength="120" placeholder="例如：修改收货地址" />
        </label>
        <label class="form-field">
          <span>项目 ID</span>
          <input v-model.trim="newProject.project_id" required pattern="[A-Za-z0-9_.\-]+" placeholder="例如：address-change" />
          <small>仅支持英文字母、数字、点、短横线和下划线</small>
        </label>
        <label class="form-field">
          <span>创建人</span>
          <input v-model.trim="newProject.created_by" required maxlength="120" />
        </label>
        <div class="modal-actions">
          <button class="button secondary" type="button" @click="createProjectOpen = false">取消</button>
          <button class="button primary" type="submit" :disabled="creatingProject">
            <LoaderCircle v-if="creatingProject" class="spin" :size="16" />
            <template v-else>创建项目</template>
          </button>
        </div>
      </form>
    </div>

    <div v-if="createModuleOpen" class="modal-scrim" @click.self="createModuleOpen = false">
      <form class="modal-dialog" @submit.prevent="submitNewModule">
        <div class="modal-header">
          <div><div class="eyebrow">{{ currentProjectName }}</div><h2>新建功能模块</h2></div>
          <button class="icon-button" type="button" title="关闭" @click="createModuleOpen = false"><X :size="18" /></button>
        </div>
        <label class="form-field">
          <span>功能模块名称</span>
          <input v-model.trim="newModule.name" required maxlength="120" placeholder="例如：收货地址管理" />
        </label>
        <label class="form-field">
          <span>模块 ID</span>
          <input v-model.trim="newModule.module_id" required pattern="[A-Za-z0-9_.\-]+" placeholder="例如：address-management" />
          <small>模块拥有独立的需求、测试点、用例、证据和报告上下文</small>
        </label>
        <label class="form-field">
          <span>创建人</span>
          <input v-model.trim="newModule.created_by" required maxlength="120" />
        </label>
        <div class="modal-actions">
          <button class="button secondary" type="button" @click="createModuleOpen = false">取消</button>
          <button class="button primary" type="submit" :disabled="creatingModule">
            <LoaderCircle v-if="creatingModule" class="spin" :size="16" />
            <template v-else>创建功能模块</template>
          </button>
        </div>
      </form>
    </div>


    <div v-if="manageDialog.open" class="modal-scrim" @click.self="manageDialog.open = false">
      <form class="modal-dialog" @submit.prevent="submitManage">
        <div class="modal-header">
          <div><div class="eyebrow">{{ manageDialog.kind === 'project' ? '项目管理' : '功能模块管理' }}</div><h2>编辑{{ manageDialog.kind === 'project' ? '项目' : '功能模块' }}</h2></div>
          <button class="icon-button" type="button" title="关闭" @click="manageDialog.open = false"><X :size="18" /></button>
        </div>
        <label class="form-field">
          <span>{{ manageDialog.kind === 'project' ? '项目名称' : '功能模块名称' }}</span>
          <input v-model.trim="manageDialog.name" required maxlength="120" />
        </label>
        <div class="management-id-note">{{ manageDialog.kind === 'project' ? '项目 ID' : '模块 ID' }}：<strong>{{ manageDialog.kind === 'project' ? manageDialog.projectId : manageDialog.moduleId }}</strong><small>ID 创建后不可修改，用于保持历史资料关联。</small></div>
        <div class="modal-actions">
          <button class="button secondary" type="button" @click="manageDialog.open = false">取消</button>
          <button class="button primary" type="submit" :disabled="manageDialog.busy">
            <LoaderCircle v-if="manageDialog.busy" class="spin" :size="16" />
            <template v-else>保存修改</template>
          </button>
        </div>
      </form>
    </div>

    <div v-if="deleteDialog.open" class="modal-scrim" @click.self="deleteDialog.open = false">
      <form class="modal-dialog danger-dialog" @submit.prevent="submitDelete">
        <div class="modal-header">
          <div><div class="eyebrow danger-eyebrow">不可撤销操作</div><h2>{{ deleteDialog.title }}</h2></div>
          <button class="icon-button" type="button" title="关闭" @click="deleteDialog.open = false"><X :size="18" /></button>
        </div>
        <div class="delete-warning"><Trash2 :size="20" /><p>{{ deleteDialog.description }}<strong>删除后无法恢复，请确认你要继续。</strong></p></div>
        <div class="modal-actions">
          <button class="button secondary" type="button" @click="deleteDialog.open = false">取消</button>
          <button class="button danger" type="submit" :disabled="deleteDialog.busy">
            <LoaderCircle v-if="deleteDialog.busy" class="spin" :size="16" />
            <template v-else>确认永久删除</template>
          </button>
        </div>
      </form>
    </div>
    <Transition name="toast"><div v-if="toast" class="toast-message"><CheckCircle2 :size="17" />{{ toast }}</div></Transition>
  </div>
</template>
