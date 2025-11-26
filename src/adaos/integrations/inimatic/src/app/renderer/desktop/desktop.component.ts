// src\adaos\integrations\inimatic\src\app\renderer\desktop\desktop.component.ts
import { Component, OnDestroy, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { IonicModule, ModalController } from '@ionic/angular'
import { YDocService } from '../../y/ydoc.service'
import { AdaosClient } from '../../core/adaos/adaos-client.service'
import { observeDeep } from '../../y/y-helpers'
import { AdaApp } from '../../runtime/dsl-types'
import { PageSchema, WidgetConfig } from '../../runtime/page-schema.model'
import { PageWidgetHostComponent } from '../widgets/page-widget-host.component'
import { DesktopSchemaService } from '../../runtime/desktop-schema.service'
import { ModalHostComponent } from '../modals/modal.component'
import '../../runtime/registry.weather'
import '../../runtime/registry.catalogs'

@Component({
	selector: 'ada-desktop',
	standalone: true,
	imports: [CommonModule, IonicModule, PageWidgetHostComponent],
	templateUrl: './desktop.component.html',
	styleUrls: ['./desktop.component.scss']
})
export class DesktopRendererComponent implements OnInit, OnDestroy {
	app?: AdaApp
	dispose?: () => void
	webspaces: Array<{ id: string; title: string; created_at: number }> = []
	activeWebspace = 'default'
	pageSchema?: PageSchema
	constructor(
		private y: YDocService,
		private modal: ModalController,
		private adaos: AdaosClient,
		private desktopSchema: DesktopSchemaService,
	) { }

	async ngOnInit() {
		await this.y.initFromHub()
		const appNode = this.y.getPath('ui/application')
		const dataNode = this.y.getPath('data')
		const recompute = () => {
			this.app = this.y.toJSON(appNode)
			this.readWebspaces()
			this.pageSchema = this.desktopSchema.loadSchema()
		}
		recompute()
		const un1 = observeDeep(appNode, recompute)
		const un2 = observeDeep(dataNode, recompute)
		this.dispose = () => { un1?.(); un2?.() }
	}
	ngOnDestroy() { this.dispose?.() }

	async openModal(id: string) {
		const modalCfg: any = (this.app as any)?.modals?.[id]
		if (!modalCfg) return
		const type = modalCfg.type
		const source = String(modalCfg.source || '')
		const data = source ? this.y.toJSON(this.y.getPath(source.replace('y:', ''))) : undefined
		// prepare common cfg
		const cfg: any = { title: modalCfg.title, data }

		// catalogs need callbacks
		if (type === 'catalog-apps' || type === 'catalog-widgets') {
			const doc = this.y.doc
			const installedPath = type === 'catalog-apps' ? 'data/installed/apps' : 'data/installed/widgets'
			const itemsPath = type === 'catalog-apps' ? 'data/catalog/apps' : 'data/catalog/widgets'
			const items = this.y.toJSON(this.y.getPath(itemsPath)) || []

			const isInstalled = (it: any) => {
				const cur: string[] = (this.y.toJSON(this.y.getPath(installedPath)) || []) as string[]
				return cur.includes(it.id)
			}
			const toggle = (it: any) => {
				const kind = type === 'catalog-apps' ? 'app' : 'widget'
				this.syncToggleInstall(kind, it.id)
			}
			const modalRef = await this.modal.create({
				component: ModalHostComponent,
				componentProps: { type, cfg: { title: modalCfg.title, items, isInstalled, toggle, close: () => modalRef.dismiss() } }
			})
			await modalRef.present()
			return
		}

		const m = await this.modal.create({ component: ModalHostComponent, componentProps: { type, cfg } })
		await m.present()
	}

	get webspaceLabel(): string {
		const entry = this.webspaces.find(ws => ws.id === this.activeWebspace)
		return entry?.title || this.activeWebspace
	}

	private readWebspaces() {
		const raw = this.y.toJSON(this.y.getPath('data/webspaces'))
		const items = Array.isArray(raw?.items) ? raw.items : []
		this.webspaces = items
		this.activeWebspace = this.y.getWebspaceId()
	}

	private async syncToggleInstall(type: 'app' | 'widget', id: string) {
		try {
			await this.adaos.sendEventsCommand('desktop.toggleInstall', { type, id })
		} catch (err) {
			console.warn('desktop.toggleInstall failed', err)
		}
	}

	async onWebspaceChanged(ev: CustomEvent) {
		const target = ev.detail?.value
		if (!target || target === this.activeWebspace) return
		try {
			await this.y.switchWebspace(target)
		} catch (err) {
			console.warn('webspace switch failed', err)
		}
	}

	async createWebspace() {
		const suggested = `space-${Date.now().toString(16)}`
		const rawId = prompt('ID нового webspace', suggested)
		if (!rawId) return
		const title = prompt('Название webspace', rawId) ?? rawId
		try {
			await this.adaos.sendEventsCommand('desktop.webspace.create', { id: rawId, title })
			await this.y.switchWebspace(rawId)
			this.activeWebspace = rawId
		} catch (err) {
			console.warn('webspace create failed', err)
		}
	}

	async renameWebspace() {
		if (!this.activeWebspace) return
		const entry = this.webspaces.find(ws => ws.id === this.activeWebspace)
		const nextTitle = prompt('Новое имя webspace', entry?.title || this.activeWebspace)
		if (!nextTitle) return
		try {
			await this.adaos.sendEventsCommand('desktop.webspace.rename', { id: this.activeWebspace, title: nextTitle })
		} catch (err) {
			console.warn('webspace rename failed', err)
		}
	}

	async deleteWebspace() {
		if (!this.activeWebspace || this.activeWebspace === 'default' || this.activeWebspace === 'desktop') return
		const entry = this.webspaces.find(ws => ws.id === this.activeWebspace)
		const ok = confirm(`Удалить webspace "${entry?.title || this.activeWebspace}"?`)
		if (!ok) return
		try {
			await this.adaos.sendEventsCommand('desktop.webspace.delete', { id: this.activeWebspace })
			await this.y.switchWebspace('default')
			this.activeWebspace = 'default'
		} catch (err) {
			console.warn('webspace delete failed', err)
		}
	}

	async refreshWebspaces() {
		try {
			await this.adaos.sendEventsCommand('desktop.webspace.refresh', {})
		} catch (err) {
			console.warn('webspace refresh failed', err)
		}
	}

	async resetDb() {
		await this.y.clearStorage()
		location.reload()
	}

	getWidgetById(id: string): WidgetConfig | undefined {
		return this.pageSchema?.widgets.find(w => w.id === id)
	}

	getWidgetsInArea(areaId: string): WidgetConfig[] {
		return (this.pageSchema?.widgets || []).filter(w => w.area === areaId)
	}
}
