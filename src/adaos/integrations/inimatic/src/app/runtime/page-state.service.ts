import { Injectable } from '@angular/core'
import { BehaviorSubject, Observable } from 'rxjs'
import { map } from 'rxjs/operators'

export type PageState = Record<string, any>

@Injectable({ providedIn: 'root' })
export class PageStateService {
  private readonly state$ = new BehaviorSubject<PageState>({})

  getSnapshot(): PageState {
    return this.state$.getValue()
  }

  set(key: string, value: any): void {
    const prev = this.state$.getValue()
    if (prev[key] === value) return
    this.state$.next({ ...prev, [key]: value })
  }

  patch(partial: PageState): void {
    if (!partial || typeof partial !== 'object') return
    const prev = this.state$.getValue()
    const next: PageState = { ...prev, ...partial }
    this.state$.next(next)
  }

  get<T = any>(key: string): T | undefined {
    const cur = this.state$.getValue()
    return cur[key] as T | undefined
  }

  select<T = any>(key: string): Observable<T | undefined> {
    return this.state$.pipe(map((s) => s[key] as T | undefined))
  }

  selectAll(): Observable<PageState> {
    return this.state$.asObservable()
  }
}

