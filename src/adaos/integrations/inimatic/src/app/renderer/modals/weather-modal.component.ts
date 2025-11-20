import { Component, Input, OnDestroy, OnInit } from '@angular/core'
import { IonicModule, ModalController } from '@ionic/angular'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { YDocService } from '../../y/ydoc.service'
import { observeDeep } from '../../y/y-helpers'

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

  constructor(private modal: ModalController, private y: YDocService) {}

  ngOnInit(): void {
    const recompute = () => {
      this.weather = this.y.toJSON(this.y.getPath('data/weather/current')) || this.weather
    }

    // Prefer observing only the weather branch to avoid noisy deep listeners.
    const weatherNode: any = this.y.getPath('data/weather')
    if (weatherNode && typeof (weatherNode as any).observeDeep === 'function') {
      const un = observeDeep(weatherNode, recompute)
      this.dispose = () => { try { un?.() } catch {} }
    } else {
      // Fallback: watch data map and react only to weather key changes.
      const dataMap: any = this.y.doc.getMap('data')
      const handler = (evt: any) => {
        try {
          if (evt?.keysChanged?.has && evt.keysChanged.has('weather')) {
            recompute()
          }
        } catch {
          recompute()
        }
      }
      dataMap.observe(handler)
      this.dispose = () => { try { dataMap.unobserve(handler) } catch {} }
    }

    recompute()
  }

  ngOnDestroy(): void {
    this.dispose?.()
  }

  close() {
    this.modal.dismiss()
  }

  onCityChange(city: string) {
    if (!city) return
    const doc = this.y.doc
    const dataMap: any = doc.getMap('data')
    const snapshot = this.y.toJSON(dataMap.get('weather')) || {}
    doc.transact(() => {
      const nextWeather = {
        ...snapshot,
        current: { ...(snapshot.current || {}), city },
      }
      dataMap.set('weather', nextWeather)
    })
    // Update local view optimistically to avoid UI lag
    if (this.weather) this.weather = { ...this.weather, city }
  }
}
