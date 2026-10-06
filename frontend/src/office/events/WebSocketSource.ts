import type { EventSource, OfficeEvent } from './types';

/**
 * Connects to a backend that streams newline/JSON OfficeEvents over a WebSocket.
 * Same EventSource interface as MockSimulator, so the scene never knows which it's using.
 * See ../README.md for the wire format and how to point this at a real agent backend.
 */
export class WebSocketSource implements EventSource {
  private url: string;
  private socket: WebSocket | null = null;
  private onEvent: ((event: OfficeEvent) => void) | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  onStatusChange?: (status: 'connecting' | 'open' | 'closed') => void;

  constructor(url: string) {
    this.url = url;
  }

  start(onEvent: (event: OfficeEvent) => void) {
    this.stopped = false;
    this.onEvent = onEvent;
    this.connect();
  }

  stop() {
    this.stopped = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.socket?.close();
    this.socket = null;
  }

  private connect() {
    this.onStatusChange?.('connecting');
    const socket = new WebSocket(this.url);
    this.socket = socket;
    socket.onopen = () => this.onStatusChange?.('open');
    socket.onmessage = (message) => {
      try {
        const event = JSON.parse(String(message.data)) as OfficeEvent;
        this.onEvent?.(event);
      } catch {
        // ignore malformed frames
      }
    };
    socket.onclose = () => {
      this.onStatusChange?.('closed');
      if (!this.stopped) this.retryTimer = setTimeout(() => this.connect(), 2000);
    };
    socket.onerror = () => socket.close();
  }
}
