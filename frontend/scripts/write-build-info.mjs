// 为构建产物写 build-info.json：列出除自身外每个文件的 sha256（#662，设计文档 §12）。
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../src/qsys/webapp/spa_dist/', import.meta.url))
const INFO = 'build-info.json'

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    return statSync(p).isDirectory() ? walk(p) : [p]
  })
}

const sha = (p) => createHash('sha256').update(readFileSync(p)).digest('hex')
const files = {}
for (const p of walk(root).sort()) {
  const rel = relative(root, p).split(sep).join('/')
  if (rel !== INFO) files[rel] = sha(p)
}
if (!files['index.html']) throw new Error(`构建产物缺少 index.html：${root}`)
writeFileSync(join(root, INFO), JSON.stringify({ schema_version: 1, files, index_sha256: files['index.html'] }, null, 2) + '\n')
console.log(`build-info.json: ${Object.keys(files).length} files`)
