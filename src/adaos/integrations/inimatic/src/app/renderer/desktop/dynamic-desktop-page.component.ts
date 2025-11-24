import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { IonicModule } from '@ionic/angular'
import { HttpClient } from '@angular/common/http'
import { PageSchema, WidgetConfig } from '../../runtime/page-schema.model'
import { PageStateService } from '../../runtime/page-state.service'
import { PageWidgetHostComponent } from '../widgets/page-widget-host.component'
import { YDocService } from '../../y/ydoc.service'
import '../../runtime/registry.weather'
import '../../runtime/registry.catalogs'
import '../../runtime/registry.workspaces'

@Component({
  selector: 'ada-dynamic-desktop-page',
  standalone: true,
  imports: [CommonModule, IonicModule, PageWidgetHostComponent],
  template: `
    <ion-content>
      <div class="desktop-page">
        <ng-container *ngIf="schema">
          <!-- Step 1: topbar + workspace tools -->
          <ng-container *ngFor="let widget of topbarWidgets">
            <ada-page-widget-host [widget]="widget"></ada-page-widget-host>
          </ng-container>
          <!-- Step 2: icons grid -->
          <ng-container *ngFor="let widget of iconWidgets">
            <ada-page-widget-host [widget]="widget"></ada-page-widget-host>
          </ng-container>
          <!-- Step 3: widgets summary list -->
          <ng-container *ngFor="let widget of widgetSummaryWidgets">
            <ada-page-widget-host [widget]="widget"></ada-page-widget-host>
          </ng-container>
          <!-- Step 4: weather summary tile -->
          <ng-container *ngFor="let widget of weatherSummaryWidgets">
            <ada-page-widget-host [widget]="widget"></ada-page-widget-host>
          </ng-container>
        </ng-container>
      </div>
    </ion-content>
  `,
  styles: [
    `
      .desktop-page {
        display: flex;
        flex-direction: column;
        gap: 8px;
        padding: 8px;
      }
    `,
  ],
})
export class DynamicDesktopPageComponent implements OnInit {
  schema?: PageSchema

  constructor(
    private http: HttpClient,
    private state: PageStateService,
    private ydoc: YDocService
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      // eslint-disable-next-line no-console
      console.log('[DynamicDesktop] ngOnInit: initFromHub()...')
    } catch {}
    await this.ydoc.initFromHub()
    try {
      // eslint-disable-next-line no-console
      console.log('[DynamicDesktop] ngOnInit: YDoc ready, loading schema...')
    } catch {}
    this.loadSchema()
  }

  private loadSchema(): void {
    this.http.get<PageSchema>('assets/ui/desktop.page.json').subscribe({
      next: (s) => {
        this.schema = s
        try {
          // eslint-disable-next-line no-console
          console.log(
            '[DynamicDesktop] schema loaded',
            s?.id,
            Array.isArray(s?.widgets) ? `widgets=${s.widgets.length}` : 'widgets=0'
          )
        } catch {}
      },
      error: (err) => {
        try {
          // eslint-disable-next-line no-console
          console.log('[DynamicDesktop] failed to load schema', err)
        } catch {}
      },
    })
  }

  get topbarWidgets(): WidgetConfig[] {
    if (!this.schema) return []
    return this.schema.widgets.filter(
      (w) => w.id === 'topbar' || w.id === 'workspace-tools'
    )
  }

  get iconWidgets(): WidgetConfig[] {
    if (!this.schema) return []
    return this.schema.widgets.filter((w) => w.id === 'desktop-icons')
  }

  get widgetSummaryWidgets(): WidgetConfig[] {
    if (!this.schema) return []
    return this.schema.widgets.filter((w) => w.id === 'desktop-widgets')
  }

  get weatherSummaryWidgets(): WidgetConfig[] {
    if (!this.schema) return []
    return this.schema.widgets.filter((w) => w.id === 'weather-summary')
  }

  widgetsInArea(areaId: string): WidgetConfig[] {
    if (!this.schema) return []
    return this.schema.widgets.filter((w) => w.area === areaId)
  }
}
