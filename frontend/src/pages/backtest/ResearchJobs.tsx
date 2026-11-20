import { useCallback, useEffect, useState } from 'react'
import { dateTime } from '../../shell/time'
import { Button, Input, InputNumber, Modal, Result, Space, Table, Tooltip } from 'antd'
import { cancelResearchJob, fetchResearchJobs, researchArtifactUrl, resubmitResearchJob, submitResearchJob, type ResearchJob, type ResearchJobList } from '../../api/research'
import { useConfirmAction } from '../../page/ConfirmAction'
import { Freshness } from '../../page/Freshness'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { usePolling } from '../../page/usePolling'
import { usePalette } from '../../theme/context'
import type { Status } from '../../theme/status'
import { Missing } from '../../ui/Missing'
import { MONO } from '../../ui/Num'
import { StatusTag } from '../../ui/StatusTag'

const ACTIVE = new Set(['queued', 'claimed', 'running'])
const RESUBMITTABLE = new Set(['failed', 'cancelled', 'interrupted'])
const STATE: Record<string, [Status, string]> = {
  succeeded: ['ok', '成功'], failed: ['bad', '失败'], interrupted: ['bad', '中断'], cancelled: ['closed', '已取消'],
  queued: ['running', '排队中'], claimed: ['running', '已领取'], running: ['running', '运行中'],
}

function Hash({ v }: { v: string | null }) {
  if (!v) return <span>未知</span>
  return <Tooltip title={v}><span style={{ fontFamily: MONO }}>{v.slice(0, 12)}</span></Tooltip>
}

/** 研究任务队列（§8.11）：失败可按原参数重新提交（新任务 ID，原记录保留）；取消需确认。 */
export function ResearchJobs() {
  const p = usePalette()
  const { confirm, holder } = useConfirmAction()
  const fetcher = useCallback((s: AbortSignal) => fetchResearchJobs(s), [])
  const [hasActive, setHasActive] = useState(false)
  const poll = usePolling(fetcher, { intervalMs: 15_000, enabled: hasActive })
  const d = poll.data as ResearchJobList | null
  useEffect(() => { if (d?.enabled) setHasActive(d.jobs.some((j) => ACTIVE.has(j.state))) }, [d])
  const [err, setErr] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [task, setTask] = useState('')
  const [params, setParams] = useState('{}')
  const [timeout, setTimeoutS] = useState<number | null>(null)
  const [formErr, setFormErr] = useState<string | null>(null)

  if (!d) return poll.error ? <Hero s="bad" title="研究任务接口无法访问" subtitle={poll.error} /> : <Hero s="running" title="加载中" />
  if (!d.enabled) return <Result status="info" title="研究任务未启用" subTitle="当前部署没有接入研究任务运行器。" />

  const act = async (title: string, impact: string, run: () => Promise<unknown>) => {
    if (!(await confirm(title, impact))) return
    try { await run(); setErr(null); poll.refresh() } catch (e) { setErr((e as Error).message) }
  }
  const submit = async () => {
    let parsed: unknown
    try { parsed = JSON.parse(params) } catch { parsed = null }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) { setFormErr('参数必须是 JSON 对象'); return }
    if (!task.trim()) { setFormErr('任务名不能为空'); return }
    setFormErr(null)
    if (!(await confirm('提交研究任务', `提交任务 ${task.trim()}，在隔离的研究运行器中按当前代码与数据快照执行。`))) return
    try { await submitResearchJob(task.trim(), parsed as Record<string, unknown>, timeout ?? undefined); setSubmitting(false); poll.refresh() } catch (e) { setFormErr((e as Error).message) }
  }

  const counts = (s: string) => d.jobs.filter((j) => j.state === s).length
  const running = d.jobs.filter((j) => ACTIVE.has(j.state)).length
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
        <Freshness at={poll.fetchedAt} paused={poll.paused} onTogglePause={() => poll.setPaused(!poll.paused)} onRefresh={poll.refresh} error={poll.error} />
      </div>
      <Hero s={counts('failed') + counts('interrupted') ? 'warn' : running ? 'running' : 'ok'}
        title={`共 ${d.jobs.length} 个任务 · 运行中 ${running} · 成功 ${counts('succeeded')} · 失败 ${counts('failed') + counts('interrupted')}`}
        subtitle={d.submission_enabled ? '每个任务绑定提交时的代码与数据快照哈希。' : '本节点未开启网页提交：只能查看历史任务。'} />
      <Panel title="任务跑完了吗？" extra={d.submission_enabled ? <Button size="small" onClick={() => { setSubmitting(true); setFormErr(null) }}>提交研究任务</Button> : undefined}>
        {err && <div role="alert" style={{ color: p.bad, marginBottom: 8 }}>{err}</div>}
        <Table<ResearchJob> size="small" rowKey="id" dataSource={d.jobs} scroll={{ x: 'max-content' }} pagination={{ pageSize: 20, hideOnSinglePage: true }}
          expandable={{ expandedRowRender: (j) => (
            <div style={{ fontSize: 12 }}>
              {j.detail && <div>说明：{j.detail}</div>}
              <div>参数：<code style={{ fontFamily: MONO }}>{JSON.stringify(j.parameters)}</code></div>
              <div>超时：{j.timeout_seconds ?? '未知'} 秒 · 快照内容哈希 <Hash v={j.snapshot_content_sha256} /></div>
              <div>产物：{j.artifacts.length === 0 ? '尚无可审计产物' : j.artifacts.map((a) => (
                <a key={a.path} style={{ marginRight: 10 }} href={researchArtifactUrl(j.id, a.path)}>{a.path}（{a.bytes} 字节）</a>
              ))}</div>
            </div>
          ) }}
          columns={[
            { title: 'ID', dataIndex: 'id', render: (v: string) => <span style={{ fontFamily: MONO }}>{v.slice(0, 8)}</span> },
            { title: '任务', dataIndex: 'task' },
            { title: '状态', dataIndex: 'state', render: (s: string) => { const [st, text] = STATE[s] ?? ['idle', s]; return <StatusTag s={st} text={text} /> } },
            { title: '进度', key: 'p', render: () => <Missing why="研究任务运行器不报告进度" /> },
            { title: '提交时间', dataIndex: 'created_at', render: (v: string | null) => dateTime(v) },
            { title: '代码', dataIndex: 'code_sha', render: (v: string) => <Hash v={v} /> },
            { title: '快照', dataIndex: 'snapshot_id' },
            { title: '操作', key: 'a', render: (_, j) => !d.submission_enabled ? null : (
              <Space size={4}>
                {ACTIVE.has(j.state) && <Button size="small" danger onClick={() => act('取消研究任务', `请求取消任务 ${j.id.slice(0, 8)}（${j.task}），运行器会在安全点停止。`, () => cancelResearchJob(j.id))}>取消</Button>}
                {RESUBMITTABLE.has(j.state) && <Button size="small" onClick={() => act('重新提交', `将以相同参数创建新任务，原记录保留（${j.id.slice(0, 8)} · ${j.task}）。`, () => resubmitResearchJob(j.id))}>重新提交</Button>}
              </Space>
            ) },
          ]} />
      </Panel>
      <Modal open={submitting} title="提交研究任务" okText="提交" onOk={submit} onCancel={() => setSubmitting(false)} destroyOnHidden>
        <div style={{ display: 'grid', gap: 8 }}>
          <Input aria-label="任务名" placeholder="任务名（运行器白名单内）" value={task} onChange={(e) => setTask(e.target.value)} />
          <Input.TextArea aria-label="参数 JSON" rows={5} value={params} onChange={(e) => setParams(e.target.value)} style={{ fontFamily: MONO }} />
          <InputNumber aria-label="超时秒数" style={{ width: '100%' }} min={60} max={86400} placeholder="超时秒数（可选，60–86400）" value={timeout} onChange={setTimeoutS} />
          {formErr && <div role="alert" style={{ color: p.bad }}>{formErr}</div>}
        </div>
      </Modal>
      {holder}
    </>
  )
}
