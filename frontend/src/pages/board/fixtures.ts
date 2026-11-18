import type { Board, BoardSeries } from '../../api/board'
import sample from './board.sample.json'
import seriesSample from './series.sample.json'

/** 后端契约的真实输出（tests/webapp/board_fixtures.build + test_webapp_board.build_prev 经 BoardQuery 导出），每次深拷贝。 */
export function board(over: Partial<Board> = {}): Board {
  return { ...(structuredClone(sample) as unknown as Board), ...over }
}

export function series(over: Partial<BoardSeries> = {}): BoardSeries {
  return { ...(structuredClone(seriesSample) as unknown as BoardSeries), ...over }
}
