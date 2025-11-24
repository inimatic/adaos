import { Component, Input, OnChanges, OnInit, SimpleChanges } from '@angular/core'
import { CommonModule } from '@angular/common'
import { IonicModule } from '@ionic/angular'
import { Observable } from 'rxjs'
import { WidgetConfig } from '../../runtime/page-schema.model'
import { PageDataService } from '../../runtime/page-data.service'
import { PageActionService } from '../../runtime/page-action.service'

@Component({
  selector: 'ada-metric-tile-widget',
  standalone: true,
  imports: [CommonModule, IonicModule],
  template: `
    <ion-card (click)="onClick()">
      <ion-card-header>
        <ion-card-title>{{ widget.title }}</ion-card-title>
      </ion-card-header>
      <ion-card-content *ngIf="data$ | async as data">
        <div class="metric-main">
          {{ data?.value ?? data?.temp_c ?? '—' }}
        </div>
        <div class="metric-sub" *ngIf="data?.label || data?.city">
          {{ data.label || data.city }}
        </div>
      </ion-card-content>
    </ion-card>
  `,
  styles: [
    `
      .metric-main {
        font-size: 32px;
        line-height: 1.1;
      }
      .metric-sub {
        font-size: 14px;
        opacity: 0.8;
      }
    `,
  ],
})
export class MetricTileWidgetComponent implements OnInit, OnChanges {
  @Input() widget!: WidgetConfig

  data$?: Observable<any>

  constructor(
    private data: PageDataService,
    private actions: PageActionService
  ) {}

  ngOnInit(): void {
    this.updateStream()
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['widget']) {
      this.updateStream()
    }
  }

  async onClick(): Promise<void> {
    const cfg = this.widget
    if (!cfg?.actions) return
    for (const act of cfg.actions) {
      if (act.on === 'click' || act.on === 'click:weather') {
        await this.actions.handle(act, { widget: cfg })
      }
    }
  }

  private updateStream(): void {
    this.data$ = this.data.load<any>(this.widget?.dataSource)
  }
}
