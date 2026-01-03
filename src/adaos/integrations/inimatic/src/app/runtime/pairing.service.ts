import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http'
import { Injectable } from '@angular/core'
import { Observable } from 'rxjs'

type PairCreateResponse = { ok: boolean; pair_code?: string; expires_at?: number }
type PairStatusResponse = {
	ok: boolean
	state?: string
	expires_in?: number
	session_jwt?: string | null
	hub_id?: string | null
	webspace_id?: string | null
}

@Injectable({ providedIn: 'root' })
export class PairingService {
	private readonly base = 'https://api.inimatic.com'

	constructor(private http: HttpClient) {}

	createBrowserPair(ttlSec = 600): Observable<PairCreateResponse> {
		return this.http.post<PairCreateResponse>(`${this.base}/v1/browser/pair/create`, {
			ttl: ttlSec,
		})
	}

	getBrowserPairStatus(code: string): Observable<PairStatusResponse> {
		const params = new HttpParams().set('code', code)
		return this.http.get<PairStatusResponse>(`${this.base}/v1/browser/pair/status`, {
			params,
		})
	}

	approveBrowserPair(code: string, webspaceId: string): Observable<{ ok: boolean }> {
		const jwt = (() => {
			try {
				return (localStorage.getItem('adaos_web_session_jwt') || '').trim()
			} catch {
				return ''
			}
		})()
		const headers = jwt ? new HttpHeaders({ Authorization: `Bearer ${jwt}` }) : undefined
		return this.http.post<{ ok: boolean }>(
			`${this.base}/v1/browser/pair/approve`,
			{ code, webspace_id: webspaceId },
			{ headers },
		)
	}
}

