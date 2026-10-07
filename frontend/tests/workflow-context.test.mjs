import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { createContext, runInContext } from 'node:vm'

const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const functionNames = [
  'isCurrentWorkflowContext', 'stopRunPolling', 'scheduleRunPolling',
  'pollWorkflowRun', 'restoreLatestRun', 'continueWorkflow',
]
// Run the component's actual functions with deferred requests and fake timers.
const workflowFunctions = functionNames.map((name) => {
  const match = appSource.match(new RegExp(`^(?:async )?function ${name}\\([^]*?^\\}`, 'm'))
  assert.ok(match, `App.vue must define ${name}`)
  return match[0]
}).join('\n')

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function createHarness() {
  const timers = new Map()
  const clearedTimers = []
  const toasts = []
  const starts = []
  let nextTimerId = 1
  const state = createContext({
    contextRequestId: 1, workflowStartRequestId: 0, runPollingTimer: null,
    currentProject: { value: { project_id: 'project-a' } },
    currentModule: { value: { module_id: 'module-a' } },
    runningWorkflow: { value: false }, activeRun: { value: null },
    syncLabel: { value: 'synced' }, refreshCount: 0, createProjectCount: 0,
    setTimeout(callback, delay) {
      const id = nextTimerId++
      timers.set(id, { callback, delay })
      return id
    },
    clearTimeout(id) {
      clearedTimers.push(id)
      timers.delete(id)
    },
    showToast(message) { toasts.push(message) },
    openCreateProject() { state.createProjectCount += 1 },
    async refreshCurrentProject() { state.refreshCount += 1 },
    async startWorkflowRun(...args) {
      starts.push(args)
      return { run_id: 'run-a', status: 'running' }
    },
    async getWorkflowRun() { return state.activeRun.value },
    async getLatestWorkflowRun() { return state.activeRun.value },
  })
  runInContext(workflowFunctions, state)

  function selectContext(projectId, moduleId, run = null) {
    state.contextRequestId += 1
    state.stopRunPolling()
    state.currentProject.value = { project_id: projectId }
    state.currentModule.value = moduleId ? { module_id: moduleId } : null
    state.activeRun.value = run
    state.runningWorkflow.value = Boolean(run && ['queued', 'running'].includes(run.status))
    state.syncLabel.value = run ? 'current task' : 'synced'
    if (state.runningWorkflow.value) {
      state.scheduleRunPolling(projectId, moduleId, run.run_id, state.contextRequestId)
    }
  }

  function snapshot() {
    return {
      run: state.activeRun.value, running: state.runningWorkflow.value,
      label: state.syncLabel.value, timers: [...timers.entries()],
      clearedTimers: [...clearedTimers], toasts: [...toasts],
      refreshCount: state.refreshCount,
    }
  }
  return { state, timers, toasts, starts, selectContext, snapshot }
}

test('current start updates the task and schedules polling', async () => {
  const harness = createHarness()
  await harness.state.continueWorkflow()
  assert.deepEqual(harness.starts, [['project-a', null, 'module-a']])
  assert.equal(harness.state.activeRun.value.run_id, 'run-a')
  assert.equal(harness.state.runningWorkflow.value, true)
  assert.equal(harness.timers.size, 1)
  assert.equal(harness.toasts.length, 1)
})

test('current start failure allows retry without scheduling a task', async () => {
  const harness = createHarness()
  harness.state.startWorkflowRun = async () => { throw new Error('start failed') }
  await harness.state.continueWorkflow()
  assert.equal(harness.state.runningWorkflow.value, false)
  assert.equal(harness.state.activeRun.value, null)
  assert.equal(harness.timers.size, 0)
  assert.deepEqual(harness.toasts, ['start failed'])
})

for (const result of ['success', 'failure']) {
  for (const switchType of ['module', 'project']) {
    test(`delayed start ${result} preserves another ${switchType}'s task and polling`, async () => {
      const harness = createHarness()
      const pending = deferred()
      harness.state.startWorkflowRun = () => pending.promise
      const start = harness.state.continueWorkflow()
      harness.selectContext(
        switchType === 'project' ? 'project-b' : 'project-a',
        switchType === 'module' ? 'module-b' : 'module-a',
        { run_id: 'run-b', status: 'running' },
      )
      const before = harness.snapshot()
      if (result === 'success') pending.resolve({ run_id: 'run-a', status: 'running' })
      else pending.reject(new Error('old start failed'))
      await start
      assert.deepEqual(harness.snapshot(), before)
    })
  }

  test(`A -> B -> A ignores the original start ${result} when IDs match again`, async () => {
    const harness = createHarness()
    const pending = deferred()
    harness.state.startWorkflowRun = () => pending.promise
    const start = harness.state.continueWorkflow()
    harness.selectContext('project-a', 'module-b')
    harness.selectContext('project-a', 'module-a', { run_id: 'restored-a', status: 'running' })
    const before = harness.snapshot()
    if (result === 'success') pending.resolve({ run_id: 'old-a', status: 'running' })
    else pending.reject(new Error('old A failed'))
    await start
    assert.deepEqual(harness.snapshot(), before)
  })

  test(`newer start wins over older ${result} in the same context`, async () => {
    const harness = createHarness()
    const first = deferred()
    const second = deferred()
    let calls = 0
    harness.state.startWorkflowRun = () => (++calls === 1 ? first.promise : second.promise)
    const oldStart = harness.state.continueWorkflow()
    // Simulate a refresh making another start available before the old HTTP response.
    harness.state.runningWorkflow.value = false
    const newStart = harness.state.continueWorkflow()
    second.resolve({ run_id: 'new-run', status: 'running' })
    await newStart
    const before = harness.snapshot()
    if (result === 'success') first.resolve({ run_id: 'old-run', status: 'running' })
    else first.reject(new Error('old request failed'))
    await oldStart
    assert.deepEqual(harness.snapshot(), before)
  })

  test(`delayed latest-task lookup ${result} cannot reset a newly started task`, async () => {
    const harness = createHarness()
    const pending = deferred()
    harness.state.getLatestWorkflowRun = () => pending.promise
    const restore = harness.state.restoreLatestRun({ project_id: 'project-a' }, 1)
    await harness.state.continueWorkflow()
    const before = harness.snapshot()
    if (result === 'success') pending.resolve(null)
    else pending.reject(new Error('lookup failed'))
    await restore
    assert.deepEqual(harness.snapshot(), before)
  })

  test(`A -> B -> A ignores earlier poll ${result} for the same run ID`, async () => {
    const harness = createHarness()
    const pending = deferred()
    harness.state.activeRun.value = { run_id: 'run-a', status: 'running' }
    harness.state.getWorkflowRun = () => pending.promise
    const poll = harness.state.pollWorkflowRun('project-a', 'module-a', 'run-a', 1)
    harness.selectContext('project-a', 'module-b')
    harness.selectContext('project-a', 'module-a', { run_id: 'run-a', status: 'running' })
    const before = harness.snapshot()
    if (result === 'success') pending.resolve({ run_id: 'run-a', status: 'succeeded' })
    else pending.reject(new Error('old poll failed'))
    await poll
    assert.deepEqual(harness.snapshot(), before)
  })
}

test('repeated clicks while starting or running issue only one request', async () => {
  const harness = createHarness()
  const pending = deferred()
  let calls = 0
  harness.state.startWorkflowRun = () => {
    calls += 1
    return pending.promise
  }
  const first = harness.state.continueWorkflow()
  await harness.state.continueWorkflow()
  assert.equal(calls, 1)
  pending.resolve({ run_id: 'run-a', status: 'running' })
  await first
  await harness.state.continueWorkflow()
  assert.equal(calls, 1)
})

test('current polling updates the task and schedules the next poll', async () => {
  const harness = createHarness()
  harness.state.activeRun.value = { run_id: 'run-a', status: 'queued' }
  harness.state.getWorkflowRun = async () => ({ run_id: 'run-a', status: 'running', message: 'working' })
  await harness.state.pollWorkflowRun('project-a', 'module-a', 'run-a', 1)
  assert.equal(harness.state.activeRun.value.message, 'working')
  assert.equal(harness.state.runningWorkflow.value, true)
  assert.equal(harness.timers.size, 1)
})

test('current polling failure retries without clearing the task', async () => {
  const harness = createHarness()
  const run = { run_id: 'run-a', status: 'running' }
  harness.state.activeRun.value = run
  harness.state.getWorkflowRun = async () => { throw new Error('temporary failure') }
  await harness.state.pollWorkflowRun('project-a', 'module-a', 'run-a', 1)
  assert.equal(harness.state.activeRun.value, run)
  assert.equal(harness.timers.size, 1)
})

test('completed current task refreshes the workflow', async () => {
  const harness = createHarness()
  harness.state.activeRun.value = { run_id: 'run-a', status: 'running' }
  harness.state.getWorkflowRun = async () => ({ run_id: 'run-a', status: 'succeeded' })
  await harness.state.pollWorkflowRun('project-a', 'module-a', 'run-a', 1)
  assert.equal(harness.state.runningWorkflow.value, false)
  assert.equal(harness.state.refreshCount, 1)
  assert.equal(harness.toasts.length, 1)
})

test('timer invalidated by context change issues no request', async () => {
  const harness = createHarness()
  harness.state.activeRun.value = { run_id: 'run-a', status: 'running' }
  harness.state.scheduleRunPolling('project-a', 'module-a', 'run-a', 1)
  const [{ callback }] = harness.timers.values()
  let calls = 0
  harness.state.getWorkflowRun = async () => { calls += 1 }
  harness.state.contextRequestId += 1
  await callback()
  assert.equal(calls, 0)
})

test('legacy project context supports a null module', async () => {
  const harness = createHarness()
  harness.state.currentModule.value = null
  await harness.state.continueWorkflow()
  assert.deepEqual(harness.starts, [['project-a', null, null]])
  assert.equal(harness.timers.size, 1)
})
