import { Component, Input, OnChanges, OnInit, SimpleChanges } from '@angular/core'
import { CommonModule } from '@angular/common'
import { IonicModule } from '@ionic/angular'
import { Observable } from 'rxjs'
import { WidgetConfig } from '../../runtime/page-schema.model'
import { PageDataService } from '../../runtime/page-data.service'
import { PageActionService } from '../../runtime/page-action.service'

@Component({
  selector: 'ada-selector-widget',
  standalone: true,
  imports: [CommonModule, IonicModule],
  template: `
    <ion-item lines="full">
      <ion-label position="stacked">{{ widget.title || widget.inputs?.['label'] || 'Select' }}</ion-label>
      <ion-select
        [value]="currentValue"
        (ionChange)="onChange($event.detail.value)"
      >
        <ion-select-option
          *ngFor="let option of options"
          [value]="option"
        >
          {{ option }}
        </ion-select-option>
      </ion-select>
    </ion-item>
  `,
})
export class SelectorWidgetComponent implements OnInit, OnChanges {
  @Input() widget!: WidgetConfig

  currentValue?: string
  options: string[] = []

  private data$?: Observable<any>

  constructor(
    private data: PageDataService,
    private actions: PageActionService
  ) {}

  ngOnInit(): void {
    this.options = Array.isArray(this.widget?.inputs?.['options'])
      ? this.widget.inputs!['options']
      : []
    this.setupStream()
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['widget']) {
      this.options = Array.isArray(this.widget?.inputs?.['options'])
        ? this.widget.inputs!['options']
        : []
      this.setupStream()
    }
  }

  private setupStream(): void {
    if (!this.widget?.dataSource) return
    const stream = this.data.load<any>(this.widget.dataSource)
    this.data$ = stream
    stream.subscribe((value) => {
      const city = (value && (value.city || value.label)) as string | undefined
      this.currentValue = city
    })
  }

  async onChange(value: string): Promise<void> {
    this.currentValue = value
    if (!this.widget?.actions) return
    for (const act of this.widget.actions) {
      if (act.on === 'change') {
        await this.actions.handle(act, { event: { value } })
      }
    }
  }
}

