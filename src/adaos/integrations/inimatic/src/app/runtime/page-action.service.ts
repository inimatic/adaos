import { Injectable } from '@angular/core'
import { ToastController } from '@ionic/angular'
import { ActionConfig } from './page-schema.model'
import { PageStateService } from './page-state.service'
import { AdaosClient } from '../core/adaos/adaos-client.service'

export interface ActionContext {
  event?: any
  widget?: any
}

@Injectable({ providedIn: 'root' })
export class PageActionService {
  constructor(
    private state: PageStateService,
    private adaos: AdaosClient,
    private toast: ToastController
  ) {}

  async handle(action: ActionConfig, ctx: ActionContext = {}): Promise<void> {
    if (!action) return
    if (action.type === 'updateState') {
      const patch = this.resolveParams(action.params ?? {}, ctx)
      this.state.patch(patch)
      return
    }
    if (action.type === 'callSkill') {
      await this.callSkill(action, ctx)
      return
    }
    if (action.type === 'openOverlay') {
      await this.openOverlay(action, ctx)
      return
    }
  }

  private async callSkill(
    action: ActionConfig,
    ctx: ActionContext
  ): Promise<void> {
    const target = action.target || ''
    const [skill, method] = target.split('.', 2)
    if (!skill || !method) return
    const body = this.resolveParams(action.params ?? {}, ctx)
    try {
      await this.adaos.callSkill(skill, method, body).toPromise()
    } catch (err) {
      try {
        const t = await this.toast.create({
          message: 'Action failed',
          duration: 1500,
        })
        await t.present()
      } catch {
        console.warn('callSkill failed', err)
      }
    }
  }

  private async openOverlay(
    _action: ActionConfig,
    _ctx: ActionContext
  ): Promise<void> {
    // For desktop pilot we model overlays through state flags and dedicated widgets.
    // This hook is kept for future extension where overlays are opened imperatively.
    return
  }

  private resolveParams(input: any, ctx: ActionContext): any {
    if (!input || typeof input !== 'object') return input
    const state = this.state.getSnapshot()
    const out: any = {}
    for (const [k, v] of Object.entries(input)) {
      if (typeof v === 'string') {
        if (v.startsWith('$state.')) {
          const path = v.slice('$state.'.length)
          out[k] = this.readByPath(state, path)
          continue
        }
        if (v.startsWith('$event.')) {
          const path = v.slice('$event.'.length)
          out[k] = this.readByPath(ctx.event, path)
          continue
        }
      }
      out[k] = v
    }
    return out
  }

  private readByPath(source: any, path: string): any {
    if (!source || !path) return undefined
    return path.split('.').reduce((acc, key) => (acc != null ? (acc as any)[key] : undefined), source)
  }
}
