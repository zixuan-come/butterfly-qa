export const stages = [
  {
    id: 'requirement-review',
    index: '01',
    title: '需求评审',
    shortTitle: '需求评审',
    owner: '需求与用例 Agent',
    status: '进行中',
    tone: 'active',
    issueCount: 5,
    progress: 62,
    steps: ['导入产品需求', 'AI 评审', '产品修改', '重新评审', '需求准入'],
  },
  {
    id: 'case-design',
    index: '02',
    title: '需求分析与用例设计',
    shortTitle: '分析与设计',
    owner: '需求与用例 Agent',
    status: '等待中',
    tone: 'waiting',
    issueCount: 0,
    progress: 20,
    steps: ['需求拆解', '提取测试点', '设计测试用例', '结构校验', '提交评审'],
  },
  {
    id: 'case-review',
    index: '03',
    title: '测试用例评审',
    shortTitle: '用例评审',
    owner: '用例评审 Agent',
    status: '待开始',
    tone: 'idle',
    issueCount: 3,
    progress: 0,
    steps: ['AI 独立评审', '查看问题', '修订用例', '重新评审', '人工审批'],
  },
  {
    id: 'execution',
    index: '04',
    title: '测试执行',
    shortTitle: '测试执行',
    owner: '主流程 Agent + 测试人员',
    status: '待开始',
    tone: 'idle',
    issueCount: 0,
    progress: 0,
    steps: ['确认环境', '执行用例', '录入结果', '上传证据', '完成执行'],
  },
  {
    id: 'report',
    index: '05',
    title: '测试报告',
    shortTitle: '测试报告',
    owner: '主流程 Agent',
    status: '待开始',
    tone: 'idle',
    issueCount: 0,
    progress: 0,
    steps: ['汇总结果', '校验事实', '生成报告', '人工审批', '报告归档'],
  },
]

export const requirementDocument = {
  name: '修改收货地址需求说明 V1.3',
  source: '产品需求文档',
  updatedAt: '2026-08-25 16:40',
  sections: [
    {
      id: 'background',
      number: '1',
      title: '背景与目标',
      paragraphs: [
        { text: '用户下单后发现收货地址有误，希望在订单发货前自行修改，减少联系客服的操作成本。' },
        { text: '本期支持用户端修改，同时支持客服在必要时为用户代客修改。', findingId: 'F-002', mark: '客服在必要时为用户代客修改' },
      ],
    },
    {
      id: 'rules',
      number: '2',
      title: '业务规则',
      paragraphs: [
        { text: '订单发货前，用户可在订单详情页点击「修改地址」，每个订单最多修改 3 次。', findingId: 'F-001', mark: '订单发货前' },
        { text: '修改成功后，新地址应同步至订单中心、仓储系统及配送系统。', findingId: 'F-003', mark: '同步至订单中心、仓储系统及配送系统' },
        { text: '若修改失败，页面提示用户稍后重试，原地址保持不变。', findingId: 'F-004', mark: '稍后重试' },
      ],
    },
    {
      id: 'fields',
      number: '3',
      title: '地址字段',
      paragraphs: [
        { text: '收货地址包含收货人、手机号、省市区、详细地址四部分。详细地址不得为空。', findingId: 'F-005', mark: '详细地址不得为空' },
        { text: '用户提交后，系统展示二次确认弹窗，确认后立即更新订单。' },
      ],
    },
  ],
}

export const findings = [
  {
    id: 'F-001',
    severity: '高',
    category: '状态规则',
    title: '“发货前”无法对应唯一订单状态',
    detail: '未明确已出库、已分配运力但未揽收等边界状态是否允许修改，验收结果将不可判定。',
    suggestion: '列出允许与禁止修改的订单状态枚举，并说明状态变化瞬间的处理规则。',
    anchorId: 'rules',
    status: '待产品确认',
  },
  {
    id: 'F-002',
    severity: '高',
    category: '权限边界',
    title: '客服代客修改的权限边界缺失',
    detail: '未定义客服角色、授权范围、二次确认及操作审计要求，存在越权修改风险。',
    suggestion: '补充 RBAC 角色、可操作订单范围、审批规则和审计字段。',
    anchorId: 'background',
    status: '待产品确认',
  },
  {
    id: 'F-003',
    severity: '高',
    category: '一致性',
    title: '跨系统同步一致性规则缺失',
    detail: '三个系统部分成功时缺少补偿、重试和最终状态定义，可能出现配送地址不一致。',
    suggestion: '明确同步顺序、超时阈值、幂等键、补偿机制和最终一致性时限。',
    anchorId: 'rules',
    status: '待技术确认',
  },
  {
    id: 'F-004',
    severity: '中',
    category: '异常流程',
    title: '“稍后重试”缺少可判定规则',
    detail: '失败提示、自动重试次数和用户可再次提交的时间均不明确。',
    suggestion: '定义错误码到提示语的映射，并给出重试次数、间隔和失败兜底。',
    anchorId: 'rules',
    status: '待产品确认',
  },
  {
    id: 'F-005',
    severity: '中',
    category: '字段边界',
    title: '地址字段校验边界未定义',
    detail: '仅说明详细地址非空，缺少字符类型、长度、特殊字符和手机号格式约束。',
    suggestion: '补充各字段必填性、长度、字符集、格式及前后空格处理。',
    anchorId: 'fields',
    status: '待产品确认',
  },
]

export const requirements = [
  { id: 'REQ-01', title: '订单状态与修改入口', points: 6, cases: 12, coverage: 100, risk: '高' },
  { id: 'REQ-02', title: '地址表单与字段校验', points: 9, cases: 21, coverage: 89, risk: '中' },
  { id: 'REQ-03', title: '多系统地址同步', points: 8, cases: 18, coverage: 75, risk: '高' },
  { id: 'REQ-04', title: '异常、重试与审计', points: 7, cases: 14, coverage: 71, risk: '高' },
]

export const testCases = [
  { id: 'TC-001', title: '待发货订单修改地址成功', priority: 'P0', type: '功能', requirement: 'REQ-01', status: '结构完整' },
  { id: 'TC-008', title: '已出库订单禁止修改地址', priority: 'P0', type: '边界', requirement: 'REQ-01', status: '待补预期' },
  { id: 'TC-019', title: '详细地址达到最大长度', priority: 'P1', type: '边界', requirement: 'REQ-02', status: '结构完整' },
  { id: 'TC-034', title: '仓储同步超时后执行补偿', priority: 'P0', type: '异常', requirement: 'REQ-03', status: '待确认规则' },
  { id: 'TC-047', title: '重复提交使用同一幂等键', priority: 'P1', type: '并发', requirement: 'REQ-04', status: '结构完整' },
]

export const reviewIssues = [
  { id: 'RI-01', severity: '高', caseId: 'TC-034', dimension: '可执行性', title: '前置条件缺少可构造的超时方式', status: '待修订' },
  { id: 'RI-02', severity: '中', caseId: 'TC-008', dimension: '可判定性', title: '预期结果未校验入口隐藏与接口拒绝', status: '修订中' },
  { id: 'RI-03', severity: '低', caseId: 'TC-019', dimension: '清晰度', title: '最大长度应使用明确数值而非变量描述', status: '已修订' },
]

export const executionCases = [
  { id: 'TC-001', title: '待发货订单修改地址成功', priority: 'P0', result: '通过', executor: '林子轩', evidence: 2 },
  { id: 'TC-008', title: '已出库订单禁止修改地址', priority: 'P0', result: '失败', executor: '林子轩', evidence: 3 },
  { id: 'TC-019', title: '详细地址达到最大长度', priority: 'P1', result: '通过', executor: '林子轩', evidence: 1 },
  { id: 'TC-034', title: '仓储同步超时后执行补偿', priority: 'P0', result: '阻塞', executor: '待分配', evidence: 0 },
  { id: 'TC-047', title: '重复提交使用同一幂等键', priority: 'P1', result: '未执行', executor: '待分配', evidence: 0 },
]

export const auditEvents = [
  { time: '16:48', actor: '需求与用例 Agent', action: '完成首轮需求评审', detail: '识别 5 个问题，其中高风险 3 个' },
  { time: '16:46', actor: '系统', action: '生成需求文档指纹', detail: 'SHA-256 · 6f2a…d918' },
  { time: '16:42', actor: '林子轩', action: '更新项目输入', detail: '上传 修改收货地址需求说明 V1.3' },
  { time: '16:40', actor: '主流程 Agent', action: '创建评审任务', detail: '任务 BR-20260825-014' },
]
