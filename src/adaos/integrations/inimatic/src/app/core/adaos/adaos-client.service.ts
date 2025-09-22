import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';

export type AdaosEvent = { type: string;[k: string]: any };
export interface AdaosConfig { baseUrl: string; token?: string | null; }

@Injectable({ providedIn: 'root' })
export class AdaosClient {
	private ws?: WebSocket;
	private cfg: AdaosConfig;

	constructor(private http: HttpClient) {
		this.cfg = {
			baseUrl: (window as any).__ADAOS_BASE__ ?? 'http://127.0.0.1:8777',
			token: (window as any).__ADAOS_TOKEN__ ?? null,
		};
	}

	setBase(url: string) { this.cfg.baseUrl = url.replace(/\/$/, ''); }
	setToken(token: string | null) { this.cfg.token = token; }

	// аккуратная склейка без new URL — работает и с абсолютной, и с относительной базой
	private abs(path: string) {
		const base = this.cfg.baseUrl.replace(/\/$/, '');
		const rel = path.startsWith('/') ? path : `/${path}`;
		return `${base}${rel}`;
	}
	private h() {
		return this.cfg.token ? new HttpHeaders({ 'X-AdaOS-Token': this.cfg.token }) : undefined;
	}

	get<T>(path: string) { return this.http.get<T>(this.abs(path), { headers: this.h() }); }
	post<T>(path: string, body?: any) { return this.http.post<T>(this.abs(path), body ?? {}, { headers: this.h() }); }

	// WebSocket напрямую к локальной ноде
	connect(topics: string[] = []) {
		const wsUrl = this.abs('/ws').replace(/^http/, 'ws');
		const u = new URL(wsUrl);
		if (this.cfg.token) u.searchParams.set('token', this.cfg.token);
		this.ws = new WebSocket(u.toString());
		this.ws.onopen = () => { if (topics.length) this.subscribe(topics); };
		return this.ws;
	}
	subscribe(topics: string[]) { this.ws?.send(JSON.stringify({ type: 'subscribe', topics })); }

	say(text: string) { return this.post('/api/say', { text }); }
	callSkill<T = any>(skill: string, method: string, body?: any) {
		return this.post<T>(`/api/skills/${skill}/${method}`, body ?? {});
	}
}
