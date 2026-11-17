import type { BoardData, SeriesData } from '../../api/board'
import sample from './board.sample.json'
import seriesSample from './series.sample.json'

/** 后端测试夹具导出的真实形状（tests/web/test_app_board.py 的 build + build_prev），每次深拷贝。 */
export function board(over: Partial<BoardData> = {}): BoardData {
  return { ...(structuredClone(sample) as unknown as BoardData), ...over }
}

export function series(over: Partial<SeriesData> = {}): SeriesData {
  return { ...(structuredClone(seriesSample) as unknown as SeriesData), ...over }
}
