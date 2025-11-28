import { Component, Input, OnDestroy, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { IonicModule } from '@ionic/angular'
import { WidgetConfig, WidgetType } from '../../runtime/page-schema.model'
import { PageDataService } from '../../runtime/page-data.service'
import { Subscription } from 'rxjs'
import { MetricTileWidgetComponent } from './metric-tile.widget.component'

@Component({
  selector: 'ada-desktop-widgets',
  standalone: true,
  imports: [CommonModule, IonicModule, MetricTileWidgetComponent],
  template: `
    <div class="widgets-section">
      <h2 *ngIf="widget?.title">{{ widget.title }}</h2>
      <ng-container *ngIf="widgets.length; else emptyState">
        <div class="widget-wrapper" *ngFor="let w of widgets">
          <ion-badge *ngIf="w.inputs?.['dev']" color="warning" class="dev-badge">DEV</ion-badge>
          <ng-container [ngSwitch]="w.type">
            <ada-metric-tile-widget
              *ngSwitchCase="'visual.metricTile'"
              [widget]="w"
            ></ada-metric-tile-widget>
            <ada-metric-tile-widget
              *ngSwitchDefault
              [widget]="w"
            ></ada-metric-tile-widget>
          </ng-container>
        </div>
      </ng-container>
      <ng-template #emptyState>
        <div class="empty-hint">No widgets installed</div>
      </ng-template>
    </div>
  `,
  styles: [
    `
      .widgets-section {
        padding: 8px 0;
      }
      .widgets-section h2 {
        font-size: 14px;
        font-weight: 500;
        margin: 0 0 8px;
        text-transform: uppercase;
      }
      .widget-wrapper {
        position: relative;
        margin-bottom: 8px;
      }
      .dev-badge {
        position: absolute;
        top: 4px;
        right: 4px;
        z-index: 1;
      }
      .empty-hint {
        color: var(--ion-color-medium);
        font-size: 14px;
      }
    `,
  ],
})
export class DesktopWidgetsWidgetComponent implements OnInit, OnDestroy {
  @Input() widget!: WidgetConfig

  widgets: Array<WidgetConfig> = []

  private dataSub?: Subscription

  constructor(private data: PageDataService) {}

  ngOnInit(): void {
    const stream = this.data.load<any[]>(this.widget?.dataSource)
    if (stream) {
      this.dataSub = stream.subscribe((items) => {
        const raw = Array.isArray(items) ? items : []
        this.widgets = raw.map((it) =>
          ({
            id: String(it.id),
            type: String(it.type || 'visual.metricTile') as WidgetType,
            area: this.widget.area,
            title: it.title,
            dataSource: it.source
              ? ({
                  kind: 'y',
                  path: String(it.source).startsWith('y:')
                    ? String(it.source).slice(2)
                    : String(it.source),
                } as any)
              : undefined,
            inputs: { dev: !!it.dev },
          } as WidgetConfig)
        )
      })
    }
  }

  ngOnDestroy(): void {
    this.dataSub?.unsubscribe()
  }
}
