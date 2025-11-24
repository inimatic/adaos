import { Component, Input, OnDestroy, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { IonicModule, ModalController } from '@ionic/angular'
import { Subscription } from 'rxjs'
import { WidgetConfig } from '../../runtime/page-schema.model'
import { PageDataService } from '../../runtime/page-data.service'
import { PageActionService } from '../../runtime/page-action.service'
import { WeatherModalComponent } from '../modals/weather-modal.component'

@Component({
  selector: 'ada-modal-overlay-widget',
  standalone: true,
  imports: [CommonModule, IonicModule],
  template: ` <ng-container></ng-container> `,
})
export class ModalOverlayWidgetComponent implements OnInit, OnDestroy {
  @Input() widget!: WidgetConfig

  private modalRef?: HTMLIonModalElement
  private dataSub?: Subscription

  constructor(
    private data: PageDataService,
    private actions: PageActionService,
    private modal: ModalController
  ) {}

  async ngOnInit(): Promise<void> {
    await this.presentModal()
  }

  ngOnDestroy(): void {
    this.dataSub?.unsubscribe()
    if (this.modalRef) {
      this.modalRef.dismiss()
      this.modalRef = undefined
    }
  }

  private async presentModal(): Promise<void> {
    const data$ = this.data.load<any>(this.widget?.dataSource)
    let weather: any
    if (data$) {
      weather = await new Promise<any>((resolve) => {
        this.dataSub = data$.subscribe({
          next: (val) => {
            resolve(val)
            this.dataSub?.unsubscribe()
            this.dataSub = undefined
          },
          error: () => {
            resolve(undefined)
            this.dataSub?.unsubscribe()
            this.dataSub = undefined
          },
        })
      })
    }
    this.modalRef = await this.modal.create({
      component: WeatherModalComponent,
      componentProps: {
        title: this.widget?.title,
        weather,
      },
    })
    this.modalRef.onDidDismiss().then(async () => {
      const cfg = this.widget
      if (!cfg?.actions) return
      for (const act of cfg.actions) {
        if (act.on === 'close') {
          await this.actions.handle(act, { widget: cfg })
        }
      }
    })
    await this.modalRef.present()
  }
}
