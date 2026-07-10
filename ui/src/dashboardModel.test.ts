import { describe, expect, it } from 'vitest'
import { filterResults, resultSection, type DashboardResult } from './dashboardModel'

const results: DashboardResult[] = [
  { result_id: 'trusted', status: 'completed', capability_trust: 'builtin_trusted', stale: false },
  { result_id: 'candidate', status: 'caution', capability_trust: 'exploratory', stale: false },
  { result_id: 'stale', status: 'blocked', capability_trust: 'exploratory', stale: true },
]

describe('dashboard result projection', () => {
  it('filters verdicts without changing their authority class', () => {
    expect(filterResults(results, 'caution').map(item => item.result_id)).toEqual(['candidate'])
    expect(resultSection(results[0])).toBe('verified')
    expect(resultSection(results[1])).toBe('exploratory')
  })

  it('keeps every result in the all view', () => {
    expect(filterResults(results, 'all')).toHaveLength(3)
  })
})
