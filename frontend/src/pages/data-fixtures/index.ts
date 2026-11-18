import type { CatalogDetail, CatalogList, DataOverview, QualitySummary, RuleDetail, TableDetail, TableList, TableSample } from '../../api/data'
import catalog from './catalog.sample.json'
import metric from './metric.sample.json'
import overview from './overview.sample.json'
import quality from './quality.sample.json'
import rule from './rule.sample.json'
import sample from './sample.sample.json'
import table from './table.sample.json'
import tables from './tables.sample.json'

/** 后端测试数据导出的真实形状（见 tests/web/test_app_data.py），每次深拷贝。 */
const clone = <T,>(x: unknown) => structuredClone(x) as unknown as T
export const dfx = {
  overview: () => clone<Extract<DataOverview, { enabled: true }>>(overview),
  tables: () => clone<Extract<TableList, { enabled: true }>>(tables),
  table: () => clone<Extract<TableDetail, { enabled: true }>>(table),
  sample: () => clone<Extract<TableSample, { enabled: true }>>(sample),
  catalog: () => clone<Extract<CatalogList, { enabled: true }>>(catalog),
  metric: () => clone<Extract<CatalogDetail, { enabled: true }>>(metric),
  quality: () => clone<Extract<QualitySummary, { enabled: true }>>(quality),
  rule: () => clone<Extract<RuleDetail, { enabled: true }>>(rule),
}
