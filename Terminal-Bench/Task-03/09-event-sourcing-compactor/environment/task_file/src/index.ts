import express, { Request, Response } from 'express';
import { Pool } from 'pg';
import { EventStore } from './event-store';
import { compactAggregate, compactAll } from './compactor';

const app = express();
app.use(express.json());

const db = new Pool({
  host:     process.env.POSTGRES_HOST     ?? 'localhost',
  port:     parseInt(process.env.POSTGRES_PORT ?? '5432'),
  database: process.env.POSTGRES_DB       ?? 'orders_db',
  user:     process.env.POSTGRES_USER     ?? 'postgres',
  password: process.env.POSTGRES_PASSWORD ?? 'secret',
});

const store = new EventStore(db);

app.post('/orders', async (req: Request, res: Response) => {
  try {
    const orderId = (req.body.orderId as string) ?? `order_${Date.now()}`;
    const version = await store.appendEvent(orderId, 0, 'order_created', { orderId, createdAt: new Date().toISOString() });
    res.status(201).json({ orderId, version });
  } catch (err: unknown) {
    const msg = (err as Error).message;
    res.status(msg.startsWith('CONFLICT') ? 409 : 500).json({ error: msg });
  }
});

app.post('/orders/:id/items', async (req: Request, res: Response) => {
  try {
    const { state, version } = await store.loadAggregate(req.params.id);
    if (state.status !== 'pending') return void res.status(400).json({ error: 'Order is not pending' });
    const newVersion = await store.appendEvent(req.params.id, version, 'item_added', req.body as Record<string, unknown>);
    res.json({ version: newVersion });
  } catch (err: unknown) {
    const msg = (err as Error).message;
    res.status(msg.startsWith('CONFLICT') ? 409 : 500).json({ error: msg });
  }
});

app.delete('/orders/:id/items/:productId', async (req: Request, res: Response) => {
  try {
    const { state, version } = await store.loadAggregate(req.params.id);
    if (state.status !== 'pending') return void res.status(400).json({ error: 'Order is not pending' });
    const newVersion = await store.appendEvent(req.params.id, version, 'item_removed', { productId: req.params.productId });
    res.json({ version: newVersion });
  } catch (err: unknown) {
    const msg = (err as Error).message;
    res.status(msg.startsWith('CONFLICT') ? 409 : 500).json({ error: msg });
  }
});

app.post('/orders/:id/confirm', async (req: Request, res: Response) => {
  try {
    const { state, version } = await store.loadAggregate(req.params.id);
    if (state.status !== 'pending') return void res.status(400).json({ error: 'Order is not pending' });
    if (state.items.length === 0) return void res.status(400).json({ error: 'Cannot confirm empty order' });
    const newVersion = await store.appendEvent(req.params.id, version, 'order_confirmed', {});
    res.json({ version: newVersion });
  } catch (err: unknown) {
    const msg = (err as Error).message;
    res.status(msg.startsWith('CONFLICT') ? 409 : 500).json({ error: msg });
  }
});

app.get('/orders/:id', async (req: Request, res: Response) => {
  try {
    const { state, version } = await store.loadAggregate(req.params.id);
    res.json({ ...state, version });
  } catch (err: unknown) {
    res.status(500).json({ error: (err as Error).message });
  }
});

app.post('/admin/compact/:id', async (req: Request, res: Response) => {
  res.json(await compactAggregate(req.params.id, db));
});

app.post('/admin/compact', async (_req: Request, res: Response) => {
  const results = await compactAll(db);
  res.json({ results, total: results.length });
});

app.get('/admin/snapshot/:id', async (req: Request, res: Response) => {
  const r = await db.query(`SELECT * FROM snapshots WHERE aggregate_id = $1`, [req.params.id]);
  res.json(r.rows[0] ?? null);
});

app.delete('/admin/events/:aggregateId/:version', async (req: Request, res: Response) => {
  await db.query(`DELETE FROM events WHERE aggregate_id = $1 AND version = $2`, [req.params.aggregateId, parseInt(req.params.version)]);
  res.json({ deleted: true });
});

app.get('/health', (_req, res) => res.json({ ok: true }));

const PORT = parseInt(process.env.PORT ?? '3000');
app.listen(PORT, () => console.log(`Order service listening on :${PORT}`));

export default app;
