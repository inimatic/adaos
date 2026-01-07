import { Component, Input, OnChanges, OnDestroy, OnInit, SimpleChanges } from '@angular/core'
import { CommonModule } from '@angular/common'
import { IonicModule } from '@ionic/angular'
import { Observable, Subscription } from 'rxjs'
import { PageDataService } from '../../runtime/page-data.service'
import { PageActionService } from '../../runtime/page-action.service'
import { PageModalService } from '../../runtime/page-modal.service'
import { ActionConfig, WidgetConfig } from '../../runtime/page-schema.model'

@Component({
  selector: 'ada-list-widget',
  standalone: true,
  imports: [CommonModule, IonicModule],
  providers: [PageModalService],
  template: `
    <div class="list-widget">
      <h2 *ngIf="widget?.title">{{ widget.title }}</h2>

      <ion-list *ngIf="items$ | async as items" [inset]="inset">
        <ion-item
          button
          *ngFor="let item of items"
          (click)="onSelect(item)"
          [detail]="false"
        >
          <ion-icon
            *ngIf="iconOf(item)"
            [name]="iconOf(item)"
            slot="start"
          ></ion-icon>
          <ion-label>
            <div class="title">{{ titleOf(item) }}</div>
            <div class="subtitle" *ngIf="subtitleOf(item)">{{ subtitleOf(item) }}</div>
          </ion-label>

          <ion-buttons slot="end" *ngIf="buttons.length">
            <ion-button
              *ngFor="let b of buttons"
              fill="clear"
              size="small"
              (click)="onButtonClick($event, b, item)"
            >
              <ion-icon *ngIf="b.icon" [name]="b.icon" slot="icon-only"></ion-icon>
              <ng-container *ngIf="!b.icon">{{ b.label }}</ng-container>
            </ion-button>
          </ion-buttons>
        </ion-item>

        <ion-item *ngIf="!items.length && emptyText">
          <ion-label>{{ emptyText }}</ion-label>
        </ion-item>
      </ion-list>
    </div>
  `,
  styles: [
    `
      .list-widget h2 {
        font-size: 14px;
        font-weight: 500;
        margin: 0 0 8px;
        text-transform: uppercase;
      }
      .title {
        font-weight: 500;
      }
      .subtitle {
        font-size: 12px;
        opacity: 0.75;
        margin-top: 2px;
      }
    `,
  ],
})
export class ListWidgetComponent implements OnInit, OnChanges, OnDestroy {
  @Input() widget!: WidgetConfig

  items$?: Observable<any[] | undefined>
  private dataSub?: Subscription
  private latestItems: any[] = []

  inset = false
  emptyText = ''

  titleKey = 'title'
  subtitleKey = 'subtitle'
  iconKey = 'icon'

  buttons: Array<{ id: string; label?: string; icon?: string }> = []

  constructor(
    private data: PageDataService,
    private actions: PageActionService,
    private modals: PageModalService,
  ) {}

  ngOnInit(): void {
    this.applyInputs()
    this.updateItemsStream()
  }

  ngOnChanges(_changes: SimpleChanges): void {
    this.applyInputs()
    this.updateItemsStream()
  }

  ngOnDestroy(): void {
    this.dataSub?.unsubscribe()
  }

  private applyInputs(): void {
    const inputs: any = this.widget?.inputs || {}
    this.inset = inputs.inset === true
    this.emptyText = typeof inputs.emptyText === 'string' ? inputs.emptyText : ''
    this.titleKey = typeof inputs.titleKey === 'string' ? inputs.titleKey : 'title'
    this.subtitleKey = typeof inputs.subtitleKey === 'string' ? inputs.subtitleKey : 'subtitle'
    this.iconKey = typeof inputs.iconKey === 'string' ? inputs.iconKey : 'icon'
    this.buttons = Array.isArray(inputs.buttons)
      ? inputs.buttons
          .filter((b: any) => b && typeof b === 'object' && (b.id || b.label))
          .map((b: any) => ({
            id: String(b.id || ''),
            label: typeof b.label === 'string' ? b.label : undefined,
            icon: typeof b.icon === 'string' ? b.icon : undefined,
          }))
          .filter((b: any) => b.id)
      : []
  }

  private updateItemsStream(): void {
    this.dataSub?.unsubscribe()
    this.items$ = this.data.load<any[]>(this.widget?.dataSource)
    const stream = this.items$
    if (!stream) return
    this.dataSub = stream.subscribe((items) => {
      this.latestItems = Array.isArray(items) ? items : []
    })
  }

  titleOf(item: any): string {
    const v = item?.[this.titleKey] ?? item?.title ?? item?.label ?? item?.id ?? ''
    return String(v || '')
  }

  subtitleOf(item: any): string {
    const v = item?.[this.subtitleKey]
    return typeof v === 'string' ? v : ''
  }

  iconOf(item: any): string {
    const v = item?.[this.iconKey]
    return typeof v === 'string' ? v : ''
  }

  async onSelect(item: any): Promise<void> {
    const cfg = this.widget
    if (!cfg?.actions) return
    for (const act of cfg.actions) {
      if (act.on === 'select') {
        await this.dispatchAction(act, item, cfg)
      }
    }
  }

  async onButtonClick(ev: Event, btn: { id: string }, item: any): Promise<void> {
    ev.preventDefault()
    ev.stopPropagation()
    const cfg = this.widget
    if (!cfg?.actions) return
    const event: any = { ...item, _button: btn.id }
    const eventId = `click:${btn.id}`
    for (const act of cfg.actions) {
      if (act.on === eventId || act.on === 'click') {
        await this.dispatchAction(act, event, cfg)
      }
    }
  }

  private async dispatchAction(act: ActionConfig, event: any, widget: WidgetConfig): Promise<void> {
    if (act.type === 'openModal') {
      const modalId = this.resolveValue(act.params?.['modalId'], event)
      await this.modals.openModalById(modalId)
      return
    }
    await this.actions.handle(act, { event, widget })
  }

  private resolveValue(value: any, event: any): any {
    if (typeof value !== 'string') return value
    if (value.startsWith('$event.')) {
      const path = value.slice('$event.'.length)
      return path.split('.').reduce((acc, key) => (acc != null ? acc[key] : undefined), event)
    }
    return value
  }
}

