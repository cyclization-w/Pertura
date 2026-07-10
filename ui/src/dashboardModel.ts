export type DashboardResult = {
  result_id: string
  status: string
  capability_trust?: string
  stale: boolean
}

export function filterResults<T extends DashboardResult>(results: T[], status: string): T[] {
  return status === 'all' ? results : results.filter(result => result.status === status)
}

export function resultSection(result: DashboardResult): 'verified' | 'exploratory' {
  return result.capability_trust === 'builtin_trusted' ? 'verified' : 'exploratory'
}
