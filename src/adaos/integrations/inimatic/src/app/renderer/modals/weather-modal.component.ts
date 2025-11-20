import { Component, Input, OnDestroy, OnInit } from '@angular/core'
import { IonicModule, ModalController } from '@ionic/angular'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import * as Y from 'yjs'
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
    const dataNode: any = this.y.getPath('data')
    const recompute = () => {
      this.weather = this.y.toJSON(this.y.getPath('data/weather/current')) || this.weather
    }
    this.dispose = observeDeep(dataNode, recompute)
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
    const dataMap: Y.Map<any> = doc.getMap('data')
    const snapshot = this.y.toJSON(dataMap.get('weather')) || {}
    doc.transact(() => {
      let weatherMap = dataMap.get('weather') as any
      if (!(weatherMap instanceof Y.Map)) {
        weatherMap = new Y.Map()
        dataMap.set('weather', weatherMap)
        // hydrate with existing snapshot so we do not drop other fields
        Object.entries(snapshot || {}).forEach(([k, v]) => {
          if (k === 'current') return
          try { weatherMap.set(k, v as any) } catch {}
        })
      }
      let currentMap = weatherMap.get('current') as any
      if (!(currentMap instanceof Y.Map)) {
        currentMap = new Y.Map()
        weatherMap.set('current', currentMap)
        const currentSnapshot = (snapshot as any)?.current
        if (currentSnapshot && typeof currentSnapshot === 'object') {
          Object.entries(currentSnapshot).forEach(([k, v]) => {
            try { currentMap.set(k, v as any) } catch {}
          })
        }
      }
      currentMap.set('city', city)
    })
    // Update local view optimistically to avoid UI lag
    if (this.weather) this.weather = { ...this.weather, city }
  }
}
