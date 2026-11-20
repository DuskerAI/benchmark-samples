import { OrderState, EventType } from './types';

export function initialState(orderId: string): OrderState {
  return { orderId, items: [], status: 'pending', total: 0, createdAt: new Date().toISOString() };
}

export function applyEvent(
  state: OrderState,
  eventType: EventType,
  payload: Record<string, unknown>
): OrderState {
  switch (eventType) {
    case 'order_created':
      return { ...state, orderId: payload.orderId as string, createdAt: payload.createdAt as string };

    case 'item_added': {
      const inc = payload as { productId: string; quantity: number; price: number };
      const idx = state.items.findIndex((i) => i.productId === inc.productId);
      const items =
        idx >= 0
          ? state.items.map((it, i) =>
              i === idx ? { ...it, quantity: it.quantity + inc.quantity } : it
            )
          : [...state.items, inc];
      return { ...state, items, total: items.reduce((s, i) => s + i.price * i.quantity, 0) };
    }

    case 'item_removed': {
      const items = state.items.filter((i) => i.productId !== (payload.productId as string));
      return { ...state, items, total: items.reduce((s, i) => s + i.price * i.quantity, 0) };
    }

    case 'order_confirmed':
      return { ...state, status: 'confirmed' };

    case 'order_cancelled':
      return { ...state, status: 'cancelled' };

    default:
      return state;
  }
}
