import type { ErrorBody } from './contracts.gen'

/** 接口错误：code 与 message 取自后端统一错误体 {error: {code, message}}；网络失败为 code="network"。 */
export class ApiError extends Error {
  constructor(public code: string, message: string, public status?: number) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(method: string, path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  let r: Response
  try {
    r = await fetch(path, {
      method, cache: 'no-store', signal,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError('network', '接口无法访问')
  }
  if (!r.ok) {
    let err: ApiError = new ApiError(`http_${r.status}`, `接口返回 ${r.status}`, r.status)
    try {
      const j = (await r.json()) as Partial<ErrorBody> & { detail?: unknown }
      if (j?.error?.message) err = new ApiError(j.error.code, j.error.message, r.status)
      // 旧接口（合入时删除）仍用 FastAPI 默认的 {detail}
      else if (typeof j?.detail === 'string') err = new ApiError(`http_${r.status}`, j.detail, r.status)
    } catch { /* 非 JSON 错误体 */ }
    throw err
  }
  return r.json() as Promise<T>
}

export const getJson = <T,>(path: string, signal?: AbortSignal) => request<T>('GET', path, undefined, signal)
export const sendJson = <T,>(method: 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown) => request<T>(method, path, body)
