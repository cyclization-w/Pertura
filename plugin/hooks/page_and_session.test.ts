import { expect, mock, test } from 'claude-code/testing'

const TOKEN = 't'.repeat(32)

// the plugin's required settings, any values: the hooks module reads none of them
const OPTIONS = { analysis_host: 'server', remote_python: '/env/bin/python', remote_code: '/pertura-core',
                  workspace_config: '/workspace.json' }

test("the page's words become the session's next turn, and the session's words reach the page", { options: OPTIONS },
     async ($, on) => {
  const clock = mock.clock(on, { now: 1_000 })
  const writes: { path: string; text: string }[] = []
  const posted: { url: string; body: any }[] = []
  const submitted: string[] = []
  let release: () => void = () => {}
  const open = new Promise<void>((resolve) => {
    release = resolve
  })
  // Nothing sits beneath the plugin: each hook here stands for the engine.
  on('env.get', (_, e) => ({ value: e.name === 'PERTURA_STATUS_HOME' ? '/home/u/.claude/pertura' : undefined }))
  on('session.id', () => ({ value: 's1' }))
  on('fs.write', (_, e) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  on('http.fetch', (_, e) => {
    posted.push({ url: e.url, body: JSON.parse(String(e.init?.body ?? '{}')) })
    return { value: { status: 200, ok: true, headers: {}, text: '{"ok": true}' } }
  })
  on('process.spawn', async function* () {
    yield { stream: 'stdout', text: `{"port": 50123, "token": "${TOKEN}"}\n` }
    yield { stream: 'stdout', text: '{"ask": "Why is Stat1 weak?"}\n' }
    await open // the helper runs on until the test ends it
    return { code: 0, signal: null }
  })
  on('prompt.submit', (_, e) => {
    submitted.push(e.text)
    return { text: e.text }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('turn.complete', (_, e) => ({ text: e.answer }))

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await clock.advance(0)

  expect(writes[0].path.split('\\').join('/')).toMatch(/home\/u\/\.claude\/pertura\/ask\/s1\.json$/) // the host's slashes
  expect(JSON.parse(writes[0].text)).toEqual({ port: 50123, token: TOKEN, updated: 1_000 })
  expect(submitted).toEqual(['Why is Stat1 weak?'])

  await $.turn.complete({ answer: 'Stat1 has 16 cells at 6h.', durationMs: 5, isAborted: false, turnId: 't1',
                          reason: 'answer' })
  await clock.advance(0)
  const toPage = posted.filter((p) => p.url === 'http://127.0.0.1:50123/said').map((p) => [p.body.who, p.body.text])
  expect(toPage).toContainEqual(['pertura', 'Stat1 has 16 cells at 6h.'])
  // the page's words reach the page once, as the page's
  const said = posted.filter((p) => p.url === 'http://127.0.0.1:50123/said' && p.body.who === 'you')
  expect(said.map((p) => [p.body.text, p.body.where])).toEqual([['Why is Stat1 weak?', 'page']])
  expect(posted.every((p) => p.body.token === TOKEN)).toBe(true)

  await clock.advance(30_000) // the session's file kept fresh
  expect(writes.length).toBeGreaterThan(1)
  release()
})

test("an agent's own tool is refused on the main loop once subagents' calls carry their ids", { options: OPTIONS },
     async ($, on) => {
  const CHECK = 'mcp__plugin_pertura_pertura__record_skeptic_check'
  const READ = 'mcp__plugin_pertura_pertura__read_record'
  const ran: string[] = []
  on('tool.call', (_, e) => { // stands for the engine
    ran.push(`${e.tool}:${e.agentId ?? 'main'}`)
    return { result: 'recorded' }
  })
  await $.tool.call({ tool: CHECK, finding_ids: ['f1'], doubt_ids: [] }) // no id seen yet: let through
  await $.tool.call({ tool: READ, agentId: 'a1' })
  const refused: any = await $.tool.call({ tool: CHECK, finding_ids: ['f1'], doubt_ids: [] })
  await $.tool.call({ tool: READ }) // the main agent's other tools stay its own
  await $.tool.call({ tool: CHECK, finding_ids: ['f1'], doubt_ids: [], agentId: 'a1' })
  expect(ran).toEqual([`${CHECK}:main`, `${READ}:a1`, `${READ}:main`, `${CHECK}:a1`])
  expect(String(refused.deny ?? refused.text)).toContain('launch the skeptic agent')
})

test("a subagent's call to Pertura's tools names its agent; the main loop's and other tools' do not", { options: OPTIONS },
     async ($, on) => {
  const READ = 'mcp__plugin_pertura_pertura__read_record'
  const seen: any[] = []
  on('agent.list', () => ({ value: [{ id: 'a1', type: 'pertura:worker', description: 'a thread',
                                      status: 'running' }] as any }))
  on('tool.call', (_, e) => { // stands for the engine
    seen.push(e)
    return { result: 'read' }
  })
  await $.tool.call({ tool: READ, what: 'threads', agentId: 'a1' })
  await $.tool.call({ tool: READ, what: 'threads', agentId: 'a9' }) // a loop no list names
  await $.tool.call({ tool: READ, what: 'threads' })
  await $.tool.call({ tool: 'Read', file_path: '/x', agentId: 'a1' })
  expect(seen.map((e) => e.pertura_agent)).toEqual(['pertura:worker', 'subagent', undefined, undefined])
  expect(seen[0].what).toBe('threads')
})
