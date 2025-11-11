import { Injectable } from '@angular/core'
import * as Y from 'yjs'
import { IndexeddbPersistence } from 'y-indexeddb'

@Injectable({ providedIn: 'root' })
export class YDocService {
  public readonly doc = new Y.Doc()
  private readonly db = new IndexeddbPersistence('adaos-mobile', this.doc)
  private initialized = false

  async initFromSeedIfEmpty(): Promise<void> {
    if (this.initialized) return
    await this.db.whenSynced
    const seed = await fetch('assets/seed.json').then(r => r.json())
    if (this.doc.share.size === 0) {
      this.doc.transact(() => {
        const ui = this.doc.getMap('ui')
        const data = this.doc.getMap('data')
        ui.set('application', seed.ui.application)
        if (seed.data?.weather) data.set('weather', seed.data.weather)
        if (seed.data?.catalog) data.set('catalog', seed.data.catalog)
        if (seed.data?.installed) data.set('installed', seed.data.installed)
      })
    } else {
      // Gentle upgrade: if new v0.2 fields are missing, merge from seed
      this.doc.transact(() => {
        const ui = this.doc.getMap('ui')
        const data = this.doc.getMap('data')
        const appCur = this.toJSON(ui.get('application')) || {}
        const appSeed = seed.ui?.application || {}

        // Ensure desktop.topbar/iconTemplate/widgetTemplate
        const desktopCur = { ...(appCur.desktop || {}) }
        const desktopSeed = appSeed.desktop || {}
        if (!desktopCur.topbar && desktopSeed.topbar) desktopCur.topbar = desktopSeed.topbar
        if (!desktopCur.iconTemplate && desktopSeed.iconTemplate) desktopCur.iconTemplate = desktopSeed.iconTemplate
        if (!desktopCur.widgetTemplate && desktopSeed.widgetTemplate) desktopCur.widgetTemplate = desktopSeed.widgetTemplate

        // Ensure modals apps_catalog/widgets_catalog/weather_modal
        const modalsCur = { ...(appCur.modals || {}) }
        const modalsSeed = appSeed.modals || {}
        for (const k of Object.keys(modalsSeed)) {
          if (!modalsCur[k]) modalsCur[k] = modalsSeed[k]
        }

        const nextApp = { ...appCur, desktop: desktopCur, modals: modalsCur, registry: appCur.registry || appSeed.registry }
        ui.set('application', nextApp)

        // Ensure data.catalog and data.installed trees
        const curCatalog = this.toJSON(data.get('catalog'))
        if (!curCatalog && seed.data?.catalog) data.set('catalog', seed.data.catalog)

        // Ensure data.weather
        const curWeather = this.toJSON(data.get('weather'))
        if (!curWeather && seed.data?.weather) data.set('weather', seed.data.weather)

        const curInstalled = this.toJSON(data.get('installed')) || {}
        const seedInstalled = seed.data?.installed || {}
        const nextInstalled = { apps: curInstalled.apps || seedInstalled.apps || [], widgets: curInstalled.widgets || seedInstalled.widgets || [] }
        data.set('installed', nextInstalled)

        // Ensure data.desktop.installed exists, initialize from data.installed if missing
        const curDesktop = this.toJSON(data.get('desktop')) || {}
        const desktopInstalled = curDesktop.installed || {}
        const nextDesktop = {
          ...curDesktop,
          installed: {
            apps: desktopInstalled.apps || nextInstalled.apps || [],
            widgets: desktopInstalled.widgets || nextInstalled.widgets || []
          }
        }
        data.set('desktop', nextDesktop)
      })
    }
    this.initialized = true
  }

  getPath(path: string): any {
    const segs = path.split('/').filter(Boolean)
    let cur: any = this.doc.getMap(segs.shift()!)
    for (const s of segs) {
      if (cur instanceof Y.Map) cur = cur.get(s)
      else if (cur && typeof cur === 'object') cur = cur[s]
      else return undefined
    }
    return cur
  }

  toJSON(val: any): any {
    try {
      const anyVal: any = val
      if (anyVal && typeof anyVal.toJSON === 'function') return anyVal.toJSON()
    } catch {}
    return val
  }

  async clearStorage(): Promise<void> {
    try {
      const anyDb: any = this.db as any
      if (typeof anyDb.clearData === 'function') {
        await anyDb.clearData()
        return
      }
    } catch {}
    // Fallback: best-effort delete by DB name used in IndexeddbPersistence
    await new Promise<void>((resolve) => {
      try {
        const req = indexedDB.deleteDatabase('adaos-mobile')
        req.onsuccess = () => resolve()
        req.onerror = () => resolve()
        req.onblocked = () => resolve()
      } catch {
        resolve()
      }
    })
  }
}
