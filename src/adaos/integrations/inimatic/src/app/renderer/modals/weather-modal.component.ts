import { Component, Input, OnDestroy, OnInit } from '@angular/core'
import { IonicModule, ModalController } from '@ionic/angular'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { YDocService } from '../../y/ydoc.service'
import { observeDeep } from '../../y/y-helpers'
import { AdaosClient } from '../../core/adaos/adaos-client.service'

@Component({
  selector: 'ada-weather-modal',
  standalone: true,
  imports: [IonicModule, CommonModule, FormsModule],
  templateUrl: './weather-modal.component.html',
  styles: [
    `:host{display:flex;flex-direction:column;height:100%} ion-content{flex:1 1 auto}`
  ]
})
export class WeatherModalComponent implements OnInit, OnDestroy {
  @Input() title = '??????'
  @Input() weather?: {
    city: string
    temp_c: number
    condition: string
    wind_ms: number
    updated_at: string
  }
  cities: string[] = ['Berlin', 'Moscow', 'New York', 'Tokyo', 'Paris']
  private dispose?: () => void

  constructor(
    private modalCtrl: ModalController,
    private y: YDocService,
    private adaos: AdaosClient
  ) {}

  ngOnInit(): void {
    const node: any = this.y.getPath('data')
    const recompute = () => {
      const currentNode: any = this.y.getPath('data/weather/current')
      this.weather = this.y.toJSON(currentNode) || this.weather
    }
    this.dispose = observeDeep(node, recompute)
    recompute()
  }

  ngOnDestroy(): void {
    this.dispose?.()
  }

  close() {
    this.modalCtrl.dismiss()
  }

  async onCityChange(city: string): Promise<void> {
    if (!city) return
    // 1) Обновляем YDoc локально, чтобы модалка и другие клиенты сразу увидели город.
    const doc = this.y.doc
    doc.transact(() => {
      const dataMap: any = this.y.doc.getMap('data')
      const currentWeather = this.y.toJSON(dataMap.get('weather')) || {}
      const nextWeather = {
        ...currentWeather,
        current: { ...(currentWeather.current || {}), city },
      }
      dataMap.set('weather', nextWeather)
    })
    if (this.weather) {
      this.weather = { ...this.weather, city }
    }
    // 2) Отправляем доменное событие, чтобы weather_skill пересчитал снапшот и записал его в YDoc.
    try {
      await this.adaos.sendEventsCommand('weather.city_changed', { city })
    } catch {
      // best-effort; если команда не прошла, останемся на локальном значении
    }
  }
}
