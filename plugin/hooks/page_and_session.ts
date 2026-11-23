import type { Register } from 'claude-code'

// The user's page (status/page.py) as a second window of this session: what the user writes there becomes this
// session's next turn, in their words, and each turn's answer shows there; Pertura's forms go there too while the page
// is open (status/forms_on_the_page.py). The page is a file and cannot reach the session, so a helper on this machine
// (status/page_and_session_helper.py) carries both ways, each request with a token that only the page and the
// session's file under the status home hold.

const BEAT_MS = 30_000 // the session's file kept fresh; the helper ends once it is older than the helper's STALE

type Link = { port: number; token: string }

let link: Link | undefined // the session's helper once it said where it listens

// Each of Pertura's agents records its part through a tool only its agent file gives it (agents/*.md); the main agent
// that called one wrote a research lead's review of its own after a compaction (the lead and the figure reviewer, and
// their tools, were retired on 2026-11-20). Refused on the main loop, whose calls carry no agentId, once a subagent's
// call has carried one: where the ids never arrive, nothing is refused.
const OURS = 'mcp__plugin_pertura_pertura__'
const AGENT_OF_TOOL = new Map([
  ['record_skeptic_check', 'skeptic'],
])
let idsArrive = false

export const register: Register = (on) => {
  on('tool.call', async ($, e, next) => {
    if (e.agentId) idsArrive = true
    const name = e.tool.startsWith(OURS) ? e.tool.slice(OURS.length) : ''
    const agent = AGENT_OF_TOOL.get(name)
    if (agent && idsArrive && !e.agentId) {
      return { deny: `${name} records the ${agent}'s part, not the main agent's: launch the ${agent} agent ` +
                     `(agents/${agent}.md) on the record and it records this itself.` }
    }
    if (!name || !e.agentId) return next(e)
    // the analysis service keeps the main agent's hints and the ended runs' news out of a subagent's replies
    const type = (await $.agent.list().catch(() => [])).find((a) => a.id === e.agentId)?.type ?? 'subagent'
    return next({ ...e, pertura_agent: type })
  })

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    void serve($).catch(() => {
      link = undefined // no helper: the page offers its "copy the id" buttons
    })
    return started
  })

  on('prompt.submit', async ($, e, next) => {
    const entered = await next(e)
    // the terminal's prompts; the page's are told where they are submitted, below
    if (!e.isMeta && !e.command && e.origin?.kind !== 'plugin') void tell($, 'you', entered.text, 'terminal')
    return entered
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    if (!e.agentId && e.reason === 'answer') void tell($, 'pertura', e.answer)
    return done
  })
}

// What was said, for the page's conversation: the user's prompts, the page's or the terminal's, and each answer.
async function tell($: any, who: 'you' | 'pertura', text: string, where?: string) {
  if (!link || !text || !text.trim()) return
  try {
    await $.http.fetch(`http://127.0.0.1:${link.port}/said`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: link.token, who, text, where }),
    })
  } catch {
    // the page misses a line; the session goes on
  }
}

// The helper for the session's life: started here, its file written and kept fresh under each id the session runs
// under (a /clear gives it another), and each message the page sent submitted as the user's own words.
async function serve($: any) {
  const home =
    (await $.env.get('PERTURA_STATUS_HOME')) ??
    `${(await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME'))}/.claude/pertura`
  const ids: string[] = [await $.session.id()]
  const helper = $.process.spawn({
    argv: ['python', '-S', `${$.plugin.root}/status/page_and_session_helper.py`, fileOf(home, ids[0])],
  })
  let rest = ''
  let timer: { cancel: () => void } | undefined
  try {
    for await (const piece of helper) {
      if (piece.stream !== 'stdout') continue
      rest += piece.text
      for (let at = rest.indexOf('\n'); at >= 0; at = rest.indexOf('\n')) {
        const line = rest.slice(0, at)
        rest = rest.slice(at + 1)
        let said: any
        try {
          said = JSON.parse(line)
        } catch {
          continue
        }
        if (!link && said.port && said.token) {
          link = { port: said.port, token: said.token }
          await beat($, home, ids)
          timer = $.clock.every(BEAT_MS, () => {
            void beat($, home, ids)
          })
        } else if (typeof said.ask === 'string' && said.ask.trim()) {
          void tell($, 'you', said.ask, 'page')
          await $.prompt.submit({ text: said.ask, asUser: true })
        }
      }
    }
  } finally {
    timer?.cancel()
    link = undefined
    for (const i of ids) {
      // marked gone at once, so the page and the forms hook stop reaching for it (best effort: the process may be ending)
      await $.fs.write(fileOf(home, i), JSON.stringify({ updated: 0 })).catch(() => {})
    }
  }
}

function fileOf(home: string, id: string) {
  return `${home}/ask/${id.replace(/[^A-Za-z0-9_.-]/g, '-')}.json`
}

// The session's file under each id it has run under, so the page and the forms hook find the helper.
async function beat($: any, home: string, ids: string[]) {
  const id = await $.session.id()
  if (!ids.includes(id)) ids.push(id)
  const updated = await $.clock.now()
  for (const i of ids) await $.fs.write(fileOf(home, i), JSON.stringify({ ...link, updated }))
}
