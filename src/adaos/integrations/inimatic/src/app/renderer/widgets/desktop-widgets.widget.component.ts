import { Component, Input, OnDestroy, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { IonicModule } from '@ionic/angular'
import { WidgetConfig } from '../../runtime/page-schema.model'
import { PageDataService } from '../../runtime/page-data.service'
import { YDocService } from '../../y/ydoc.service'
import { WidgetComponent } from './widget.component'
import { Subscription } from 'rxjs'

@Component({
  selector: 'ada-desktop-widgets',
  standalone: true,
  imports: [CommonModule, IonicModule],
  template: `
    <div class="widgets-section">
      <h2 *ngIf="widget?.title">{{ widget.title }}</h2>
      <ion-list *ngIf="widgets.length; else emptyState">
        <ion-item *ngFor="let w of widgets">
          <ion-label>
            <h3>{{ w.title || w.id }}</h3>
            <p>{{ w.type }}</p>
          </ion-label>
          <ion-badge *ngIf="w.dev" color="warning" slot="end">DEV</ion-badge>
        </ion-item>
      </ion-list>
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
      .empty-hint {
        color: var(--ion-color-medium);
        font-size: 14px;
      }
    `,
  ],
})
export class DesktopWidgetsWidgetComponent implements OnInit, OnDestroy {
  @Input() widget!: WidgetConfig

  widgets: Array<{ id: string; type: string; title?: string; source?: string; dev?: boolean }> = []
  private dataSub?: Subscription

  constructor(private data: PageDataService, private ydoc: YDocService) {}

  ngOnInit(): void {
    const stream = this.data.load<any[]>(this.widget?.dataSource)
    if (stream) {
      this.dataSub = stream.subscribe((items) => {
        this.widgets = Array.isArray(items) ? items : []
      })
    }
  }

  ngOnDestroy(): void {
    this.dataSub?.unsubscribe()
  }

  getData(source?: string): any {
    if (!source) return undefined
    const path = source.startsWith('y:') ? source.slice(2) : source
    return this.ydoc.toJSON(this.ydoc.getPath(path))
  }
}
