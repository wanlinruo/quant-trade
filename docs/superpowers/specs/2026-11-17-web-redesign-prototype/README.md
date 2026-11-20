# Web 改版可点击原型（2026-11-17 快照，#662）

设计文档 `../2026-11-17-web-redesign-design.md` 的配套原型源码快照。实施计划中“参考原型”指的就是这里。

- **只读参考，不是生产代码**：全部数据为示意数据（`src/mock.ts`、`src/pages/*Mock.ts`），
  不连任何后端；实现时按设计文档与实施计划重写，不直接复制进 `frontend/`。
- 不随原型继续修改；设计变化以设计文档为准。
- 未收入：`node_modules/`、构建产物、Vite 模板自带的未使用图片。

## 本地运行

```bash
cd docs/superpowers/specs/2026-11-17-web-redesign-prototype
npm ci
npx vite --port 5199
```

## 场景

页头可切换三个场景（也可用 `?scene=am|pm|bad`）：

- `am`：盘中 10:42，正常交易日
- `pm`：晚间 20:40，收盘后对账与晚间任务
- `bad`：异常日盘中 10:48，急停落闸（10:32:35）、行情缺口、告警堆积

页面路由用 hash（如 `#board`、`#recon`），看板模式可加 `?bm=`。
