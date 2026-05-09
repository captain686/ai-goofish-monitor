type WebSocketEventHandler = (data: any) => void;

class WebSocketService {
  private ws: WebSocket | null = null;
  private reconnectInterval = 3000;
  private listeners: Map<string, WebSocketEventHandler[]> = new Map();
  public isConnected = false;
  private shouldConnect = false;

  constructor() {
    if (localStorage.getItem('auth_logged_in') === 'true') {
      this.start();
    }
  }

  public start() {
    this.shouldConnect = true;
    if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
      void this.connect();
    }
  }

  public stop() {
    this.shouldConnect = false;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private async fetchWsTicket(): Promise<string | null> {
    try {
      const response = await fetch('/api/ws/ticket', {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) return null;
      const data = await response.json();
      return data.ticket || null;
    } catch {
      return null;
    }
  }

  private async connect() {
    const ticket = await this.fetchWsTicket();
    if (!ticket) {
      if (this.shouldConnect) {
        setTimeout(() => void this.connect(), this.reconnectInterval);
      }
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = `${protocol}//${host}/ws?ticket=${encodeURIComponent(ticket)}`;

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.isConnected = true;
      this.emit('connected', { isConnected: true });
    };

    this.ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type) {
          this.emit(message.type, message.data);
        }
      } catch (e) {
        console.error('Failed to parse WebSocket message', e);
      }
    };

    this.ws.onclose = () => {
      if (this.isConnected) {
        this.isConnected = false;
        this.emit('disconnected', { isConnected: false });
      }
      if (this.shouldConnect || localStorage.getItem('auth_logged_in') === 'true') {
        setTimeout(() => void this.connect(), this.reconnectInterval);
      }
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  public on(event: string, handler: WebSocketEventHandler) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event)?.push(handler);
  }

  public off(event: string, handler: WebSocketEventHandler) {
    const handlers = this.listeners.get(event);
    if (handlers) {
      const index = handlers.indexOf(handler);
      if (index !== -1) {
        handlers.splice(index, 1);
      }
    }
  }

  private emit(event: string, data: any) {
    const handlers = this.listeners.get(event);
    if (handlers) {
      handlers.forEach((handler) => handler(data));
    }
  }
}

export const wsService = new WebSocketService();
