import { Component, Input, OnChanges, OnInit, SimpleChanges } from '@angular/core'
import { CommonModule } from '@angular/common'
import { IonicModule } from '@ionic/angular'
import { Observable } from 'rxjs'
import { WidgetConfig } from '../../runtime/page-schema.model'
import { PageDataService } from '../../runtime/page-data.service'
import { PageActionService } from '../../runtime/page-action.service'
import { FormsModule } from '@angular/forms'

@Component({
  selector: 'ada-text-editor-widget',
  standalone: true,
  imports: [CommonModule, IonicModule, FormsModule],
  template: `
    <ion-card>
      <ion-card-header *ngIf="widget?.title">
        <ion-card-title>{{ widget.title }}</ion-card-title>
      </ion-card-header>
      <ion-card-content>
        <ion-textarea
          [autoGrow]="true"
          [(ngModel)]="current"
        ></ion-textarea>
        <div style="margin-top: 8px; display: flex; justify-content: flex-end;">
          <ion-button size="small" (click)="onSave()" [disabled]="!isDirty()">
            Save
          </ion-button>
        </div>
      </ion-card-content>
    </ion-card>
  `,
})
export class TextEditorWidgetComponent implements OnInit, OnChanges {
  @Input() widget!: WidgetConfig

  current = ''
  private baseline = ''

  constructor(
    private data: PageDataService,
    private actions: PageActionService
  ) {}

  ngOnInit(): void {
    this.updateStream()
  }

  ngOnChanges(_changes: SimpleChanges): void {
    this.updateStream()
  }

  private updateStream(): void {
    const ds = this.widget?.dataSource
    if (!ds) {
      return
    }
    const bindField: string =
      (this.widget.inputs && this.widget.inputs['bindField']) || 'content'
    // Subscribe once to initialise baseline + current value.
    this.data.load<any>(ds).subscribe({
      next: (value) => {
        try {
          const next =
            value && typeof value === 'object' ? (value as any)[bindField] : undefined
          const text = typeof next === 'string' ? next : ''
          this.baseline = text
          this.current = text
        } catch {
          this.baseline = ''
          this.current = ''
        }
      },
      error: () => {
        this.baseline = ''
        this.current = ''
      },
    })
  }

  isDirty(): boolean {
    return (this.current || '') !== (this.baseline || '')
  }

  async onSave(): Promise<void> {
    const cfg = this.widget
    if (!cfg?.actions || !cfg.actions.length) return
    const event = { content: this.current }
    for (const act of cfg.actions) {
      if (act.on === 'save') {
        await this.actions.handle(act, { event, widget: cfg })
      }
    }
  }
}
