export interface Item {
  productId: string;
  quantity: number;
  price: number;
}

export interface OrderState {
  orderId: string;
  items: Item[];
  status: 'pending' | 'confirmed' | 'cancelled';
  total: number;
  createdAt: string;
}

export type EventType =
  | 'order_created'
  | 'item_added'
  | 'item_removed'
  | 'order_confirmed'
  | 'order_cancelled';

export interface CompactionResult {
  aggregateId: string;
  snapshotVersion: number;
  eventsCompacted: number;
  success: boolean;
  error?: string;
}
